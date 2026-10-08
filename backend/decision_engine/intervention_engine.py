"""Intervention Engine — generates and ranks recovery action candidates.

For each case, generates InterventionCandidate objects for all applicable
intervention types, computes expected recovery value (EV) using:
  EV = floor(recovery_probability × payment_amount_at_risk)
  using Decimal with ROUND_FLOOR — never float for financial amounts (DATA-001)

Candidates are ranked by expected_recovery_value (descending).

Razorpay API Capability Register (FR-INTVN-001):
  PAYMENT_LINK     → CREATE_PAYMENT_LINK (EXECUTABLE, VERIFIED)
  RETRY            → NO_OP for standalone payments (NOT_SUPPORTED by Razorpay API)
  CUSTOMER_ACTION  → SEND_NOTIFICATION (EXECUTABLE)
  ESCALATE_TO_HUMAN → NO_OP (INTERNAL_ACTION)
  STOP             → NO_OP (INTERNAL_ACTION)

Test-mode limit guard (FR-INTVN-007):
  When operating_mode=REAL_TEST_MODE AND payment_link_count >= 30,
  substitute PAYMENT_LINK with RECOMMENDATION_ONLY (NO_OP) automatically.

Requirements: FR-INTVN-001 through FR-INTVN-007
"""
from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal

from shared.contracts.enums import ExecutionCapability, InterventionType, OperatingMode
from shared.contracts.schemas import CaseContext, InterventionCandidate, PolicyConfig

# ---------------------------------------------------------------------------
# Razorpay test-mode Payment Link limit (FR-INTVN-007, INT-RAZORPAY-011)
# ---------------------------------------------------------------------------

RAZORPAY_TEST_MODE_PAYMENT_LINK_LIMIT: int = 30
"""
Razorpay test mode limits accounts to 30 Payment Links per business.

When operating_mode=REAL_TEST_MODE and the current payment_link_count is at
or above this limit, the PAYMENT_LINK intervention is substituted with a
RECOMMENDATION_ONLY (NO_OP) candidate automatically.

FakeRazorpayGateway (SIMULATED mode) does not have this restriction.
FR-INTVN-007, INT-RAZORPAY-011.
"""

# Stub model version used when no real artifact is available (unit tests / dev).
_STUB_MODEL_VERSION: str = "stub-v0"


def compute_expected_value(recovery_probability: float, amount_paise: int) -> int:
    """Compute EV as floor(probability × amount) using Decimal. Returns int. DATA-001.

    This function is the canonical EV implementation for the Intervention_Engine.
    It MUST be used for every EV calculation involving financial amounts to prevent
    float arithmetic rounding errors.

    Args:
        recovery_probability:
            P(recovery | context, action) — a float in [0.0, 1.0].
            Not a financial value; used only for EV multiplication.
        amount_paise:
            payment_amount_at_risk in integer paise.  Must be a non-negative integer.
            DATA-001.

    Returns:
        Floor of (probability × amount) as a Python ``int`` in paise.
        The intermediate ``Decimal`` is discarded after conversion.
        DATA-001, DATA-003.

    Examples::

        >>> compute_expected_value(0.5, 100000)
        50000
        >>> compute_expected_value(0.333, 100)
        33
        >>> compute_expected_value(0.0, 50000)
        0
    """
    ev = Decimal(str(recovery_probability)) * Decimal(amount_paise)
    return int(ev.to_integral_value(rounding=ROUND_FLOOR))


class InterventionEngine:
    """Generates and ranks recovery action candidates for a given CaseContext.

    The engine applies the Razorpay API Capability Register to decide whether
    each intervention type is EXECUTABLE or NO_OP, then computes the expected
    recovery value (EV) for each candidate and sorts them by EV descending.

    **EV Pipeline order** (FR-INTVN-001 through FR-INTVN-004):
      1. Recoverability model produces ``recovery_probability`` (provided as argument)
      2. Generate candidate interventions based on ProviderCapabilityConfig
      3. Compute ``expected_recovery_value`` per candidate using ``compute_expected_value``
      4. Rank candidates by ``expected_recovery_value`` descending
      5. Pass ranked list to Policy_Engine

    Note: Policy safety eligibility rules are applied by the Policy_Engine —
    NOT as a pre-filter before EV calculation here.

    Requirements: FR-INTVN-001 through FR-INTVN-007
    """

    def generate_candidates(
        self,
        context: CaseContext,
        recovery_probability: float,
        policy_config: PolicyConfig,
        payment_link_count: int,
        operating_mode: str | OperatingMode,
        *,
        model_version: str = _STUB_MODEL_VERSION,
    ) -> list[InterventionCandidate]:
        """Generate a ranked list of InterventionCandidate objects for a case.

        Candidates are generated for all applicable intervention types and then
        sorted by ``expected_recovery_value`` descending (FR-INTVN-004).

        Intervention types generated:
          1. **PAYMENT_LINK** — EXECUTABLE (CREATE_PAYMENT_LINK) when operating in
             SIMULATED mode OR when the test-mode Payment Link count is below the
             30-link limit; otherwise NO_OP (RECOMMENDATION_ONLY) per FR-INTVN-007.
          2. **ESCALATE_TO_HUMAN** — always NO_OP (INTERNAL_ACTION); EV = 0, so it
             is placed below any positive-EV candidates in the ranking.
          3. **STOP** — always NO_OP (INTERNAL_ACTION); EV = 0, probability = 0.0.

        When no candidate has a positive EV (all EVs ≤ 0), STOP will float to a
        tie position.  Callers downstream may treat a top-ranked STOP or all-zero
        EV list as the signal to stop recovery (FR-INTVN-005).

        Args:
            context:
                The CaseContext for the payment failure being evaluated.
                Provides ``amount_paise`` used for EV calculation.
            recovery_probability:
                P(recovery | context, action) from the Recoverability_Model.
                Float in [0.0, 1.0].  Not a financial value.
            policy_config:
                Active policy snapshot.  Reserved for future EV extensions
                (cost, risk_penalty from policy); currently not used in base EV.
            payment_link_count:
                Number of Payment Links already created in the current operating
                mode for this merchant.  Used to enforce the test-mode limit guard
                (FR-INTVN-007).
            operating_mode:
                Current operating mode.  Accepts ``OperatingMode`` enum or the
                string value (e.g. ``"REAL_TEST_MODE"`` / ``"SIMULATED"``).
            model_version:
                Version string of the Recoverability_Model artifact that produced
                ``recovery_probability``.  Recorded on each candidate for audit.

        Returns:
            A list of ``InterventionCandidate`` objects sorted by
            ``expected_recovery_value`` descending (FR-INTVN-004).

        Note:
            This method is stateless — it does not read from or write to any
            database or external service.  All inputs are provided explicitly.
        """
        # Normalise operating_mode to string for comparison
        mode_str: str = (
            operating_mode.value
            if isinstance(operating_mode, OperatingMode)
            else str(operating_mode)
        )

        candidates: list[InterventionCandidate] = []

        # ------------------------------------------------------------------
        # 1. PAYMENT_LINK candidate (FR-INTVN-001, FR-INTVN-003, FR-INTVN-007)
        # ------------------------------------------------------------------
        #
        # PAYMENT_LINK is the only EXECUTABLE intervention for standalone failed
        # payments confirmed as VERIFIED in the Razorpay API Capability Register.
        #
        # Test-mode limit guard (FR-INTVN-007):
        #   When operating_mode=REAL_TEST_MODE AND payment_link_count >= limit:
        #     → substitute with RECOMMENDATION_ONLY (NO_OP)
        #
        # In SIMULATED mode, the FakeRazorpayGateway has no limit, so we always
        # produce CREATE_PAYMENT_LINK.
        payment_link_ev = compute_expected_value(
            recovery_probability, context.amount_paise
        )

        is_test_mode_limit_reached = (
            mode_str == OperatingMode.REAL_TEST_MODE.value
            and payment_link_count >= RAZORPAY_TEST_MODE_PAYMENT_LINK_LIMIT
        )

        if is_test_mode_limit_reached:
            # Substitute with RECOMMENDATION_ONLY per FR-INTVN-007.
            # EV is still computed (EV pipeline order enforced — no pre-filtering).
            payment_link_capability = ExecutionCapability.NO_OP
            payment_link_reasoning = (
                "PAYMENT_LINK substituted with RECOMMENDATION_ONLY: Razorpay "
                f"test-mode limit of {RAZORPAY_TEST_MODE_PAYMENT_LINK_LIMIT} Payment "
                f"Links reached (current count: {payment_link_count}). "
                "Use FakeRazorpayGateway (SIMULATED mode) for bulk scenarios. "
                "INT-RAZORPAY-011, FR-INTVN-007."
            )
        else:
            payment_link_capability = ExecutionCapability.CREATE_PAYMENT_LINK
            payment_link_reasoning = (
                "PAYMENT_LINK is the primary EXECUTABLE recovery action for standalone "
                "failed payments. Calls POST /v1/payment_links on Razorpay (VERIFIED). "
                "FR-INTVN-001, INT-RAZORPAY-004."
            )

        candidates.append(
            InterventionCandidate(
                intervention_type=InterventionType.PAYMENT_LINK,
                execution_capability=payment_link_capability,
                recovery_probability=recovery_probability,
                expected_recovery_value=payment_link_ev,
                reasoning=payment_link_reasoning,
                model_version=model_version,
            )
        )

        # ------------------------------------------------------------------
        # 2. ESCALATE_TO_HUMAN candidate (FR-INTVN-001)
        # ------------------------------------------------------------------
        #
        # ESCALATE_TO_HUMAN is an INTERNAL_ACTION — no Razorpay API call is made.
        # It routes the case to a human reviewer.  EV = 0 because the probability
        # of recovery is attributed to the human action, not to a direct intervention.
        # probability is preserved as recovery_probability so the human reviewer
        # can see the model's assessment.
        candidates.append(
            InterventionCandidate(
                intervention_type=InterventionType.ESCALATE_TO_HUMAN,
                execution_capability=ExecutionCapability.NO_OP,
                recovery_probability=recovery_probability,
                expected_recovery_value=0,
                reasoning=(
                    "ESCALATE_TO_HUMAN routes this case to a human reviewer for "
                    "manual assessment. INTERNAL_ACTION — no direct gateway call. "
                    "EV is recorded as 0 (recovery credited to human action, not "
                    "to a direct automated intervention)."
                ),
                model_version=model_version,
            )
        )

        # ------------------------------------------------------------------
        # 3. STOP candidate (FR-INTVN-001, FR-INTVN-005)
        # ------------------------------------------------------------------
        #
        # STOP is always included as a terminal option (FR-INTVN-005).
        # When no candidate has a positive EV, STOP floats to the top of the
        # tie group signalling that recovery efforts should cease.
        # probability = 0.0 because selecting STOP means we do not expect recovery.
        candidates.append(
            InterventionCandidate(
                intervention_type=InterventionType.STOP,
                execution_capability=ExecutionCapability.NO_OP,
                recovery_probability=0.0,
                expected_recovery_value=0,
                reasoning=(
                    "STOP terminates all recovery attempts for this case. "
                    "INTERNAL_ACTION — no gateway call. Always included per "
                    "FR-INTVN-005 so that the Policy_Engine always has a "
                    "terminal option available."
                ),
                model_version=model_version,
            )
        )

        # ------------------------------------------------------------------
        # Rank by expected_recovery_value descending (FR-INTVN-004)
        # ------------------------------------------------------------------
        candidates.sort(key=lambda c: c.expected_recovery_value, reverse=True)

        return candidates
