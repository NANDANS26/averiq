"""Expected value (EV) computation for intervention candidates.

EV = floor(recovery_probability × payment_amount_at_risk) - cost - risk_penalty

All computations use decimal.Decimal with ROUND_FLOOR.
The function always returns int (minor currency units, i.e. paise).
The intermediate Decimal is discarded — never stored.

Key invariants:
  - EV is always int (DATA-001, DATA-003)
  - Computed for ALL candidates before any policy blocking
  - cost and risk_penalty sourced from active Policy (integers, minor units)
  - A negative EV is valid (intervention expected to be net-negative)

Requirements: FR-INTVN-002, DATA-001, DATA-003
"""
from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal


def compute_expected_value(
    recovery_probability: float,
    payment_amount_at_risk: int,
    cost: int = 0,
    risk_penalty: int = 0,
) -> int:
    """Compute expected recovery value in integer minor units (paise).

    Args:
        recovery_probability: float in [0.0, 1.0] from recoverability model
        payment_amount_at_risk: integer paise (must be > 0)
        cost: integer paise cost of this intervention (default 0)
        risk_penalty: integer paise risk adjustment (default 0)

    Returns:
        int: EV in minor units (paise). May be negative. Never float.

    Raises:
        ValueError: if recovery_probability not in [0.0, 1.0]
        ValueError: if payment_amount_at_risk <= 0
    """
    if not (0.0 <= recovery_probability <= 1.0):
        raise ValueError(f"recovery_probability must be in [0.0, 1.0], got {recovery_probability}")
    if payment_amount_at_risk <= 0:
        raise ValueError(f"payment_amount_at_risk must be > 0, got {payment_amount_at_risk}")

    gross_ev = Decimal(str(recovery_probability)) * Decimal(payment_amount_at_risk)
    floored = gross_ev.to_integral_value(rounding=ROUND_FLOOR)
    return int(floored) - int(cost) - int(risk_penalty)
