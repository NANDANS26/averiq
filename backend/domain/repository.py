"""Merchant-scoped repository base class and concrete implementations.

Every repository in Averiq that handles merchant-owned data inherits from
``MerchantScopedRepository``.  The base class injects the authenticated
merchant's ``merchant_id`` into every read/write operation, preventing
cross-merchant data leakage at the persistence layer (SEC-ISOL-001,
SEC-ISOL-002).

Scope of isolation
------------------
Merchant isolation applies to:
  RevenueRiskCase, Payment, Customer, Intervention, Subscription,
  AuditEvent, Policy, WebhookEvent, HumanReview.

It does NOT apply to:
  ``model_artifacts`` or global configuration tables (not merchant-owned).

Repositories
------------
  MerchantScopedRepository  — abstract base
  RevenueRiskCaseRepository — concrete; RevenueRiskCase rows
  PaymentRepository         — concrete; Payment rows
  CustomerRepository        — concrete; Customer rows
  WebhookEventRepository    — concrete; WebhookEvent rows
  AuditEventRepository      — concrete; INSERT + SELECT only (AUD-002)
  InterventionRepository    — concrete; Intervention rows
  PolicyRepository          — concrete; Policy rows

Error semantics
---------------
  ForbiddenError  — raised (→ HTTP 403) when a cross-merchant access is detected
                    at the repository layer; wraps the resource type and ID.

Requirements: SEC-ISOL-001, SEC-ISOL-002
"""

from __future__ import annotations

import uuid
from typing import Any, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain.models import (
    AuditEvent,
    Customer,
    Intervention,
    Payment,
    Policy,
    RevenueRiskCase,
    WebhookEvent,
)

# ---------------------------------------------------------------------------
# Public error
# ---------------------------------------------------------------------------


class ForbiddenError(Exception):
    """Raised when a repository operation detects a cross-merchant access attempt.

    Maps to HTTP 403 in the FastAPI exception handler.

    Attributes
    ----------
    resource_type:
        Human-readable name of the resource class (e.g. ``"RevenueRiskCase"``).
    resource_id:
        UUID of the resource that was requested.
    requesting_merchant_id:
        ``merchant_id`` from the authenticated context.
    owning_merchant_id:
        ``merchant_id`` actually stored on the fetched row.
    """

    def __init__(
        self,
        resource_type: str,
        resource_id: uuid.UUID,
        requesting_merchant_id: uuid.UUID,
        owning_merchant_id: uuid.UUID,
    ) -> None:
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.requesting_merchant_id = requesting_merchant_id
        self.owning_merchant_id = owning_merchant_id
        super().__init__(
            f"Cross-merchant access denied: {resource_type}({resource_id}) "
            f"belongs to merchant {owning_merchant_id}, "
            f"but requesting merchant is {requesting_merchant_id}"
        )


# ---------------------------------------------------------------------------
# Generic type variable for the ORM model
# ---------------------------------------------------------------------------

_M = TypeVar("_M")


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class MerchantScopedRepository(Generic[_M]):
    """Abstract base repository that scopes all data access to one merchant.

    Subclasses must set ``_model`` to the SQLAlchemy mapped class.

    Parameters
    ----------
    session:
        An async SQLAlchemy session (injected by FastAPI's dependency system).
    merchant_id:
        The UUID of the authenticated merchant; injected from the JWT payload.
    """

    _model: type[Any]  # must be overridden in each subclass

    def __init__(self, session: AsyncSession, merchant_id: uuid.UUID) -> None:
        self._session = session
        self._merchant_id = merchant_id

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _assert_same_merchant(self, row: Any, resource_type: str | None = None) -> None:
        """Raise ForbiddenError if ``row.merchant_id`` != ``self._merchant_id``."""
        owning = getattr(row, "merchant_id", None)
        if owning is None:
            # row has no merchant_id column — skip isolation check
            return
        if owning != self._merchant_id:
            rtype = resource_type or type(row).__name__
            rid = getattr(row, "id", uuid.uuid4())
            raise ForbiddenError(
                resource_type=rtype,
                resource_id=rid,
                requesting_merchant_id=self._merchant_id,
                owning_merchant_id=owning,
            )

    # ------------------------------------------------------------------
    # Core CRUD — merchant-scoped
    # ------------------------------------------------------------------

    async def list(self, **filters: Any) -> list[_M]:
        """Return all rows that belong to the authenticated merchant.

        Extra keyword arguments are applied as equality filters.
        merchant_id is always injected regardless of ``filters``.
        """
        stmt = select(self._model).where(
            self._model.merchant_id == self._merchant_id  # type: ignore[attr-defined]
        )
        for column_name, value in filters.items():
            col = getattr(self._model, column_name, None)
            if col is not None:
                stmt = stmt.where(col == value)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, resource_id: uuid.UUID) -> _M:
        """Fetch a single row by primary key and enforce merchant isolation.

        Raises
        ------
        ForbiddenError
            If the fetched row belongs to a different merchant.
        ValueError
            If no row with the given ID exists.
        """
        row = await self._session.get(self._model, resource_id)
        if row is None:
            raise ValueError(
                f"{self._model.__name__} with id={resource_id} does not exist"
            )
        self._assert_same_merchant(row)
        return row  # type: ignore[return-value]

    async def create(self, **kwargs: Any) -> _M:
        """Create a new row, always forcing ``merchant_id`` to the authenticated value.

        Any ``merchant_id`` passed in ``kwargs`` is silently overridden.
        This prevents callers from injecting a foreign merchant_id via the
        request payload (SEC-ISOL-002).
        """
        # Force merchant_id regardless of what the caller passed
        kwargs["merchant_id"] = self._merchant_id
        instance = self._model(**kwargs)
        self._session.add(instance)
        await self._session.flush()
        await self._session.refresh(instance)
        return instance  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Concrete repositories
# ---------------------------------------------------------------------------


class RevenueRiskCaseRepository(MerchantScopedRepository[RevenueRiskCase]):
    """Repository for ``RevenueRiskCase`` rows, scoped to one merchant."""

    _model = RevenueRiskCase


class PaymentRepository(MerchantScopedRepository[Payment]):
    """Repository for ``Payment`` rows, scoped to one merchant."""

    _model = Payment


class CustomerRepository(MerchantScopedRepository[Customer]):
    """Repository for ``Customer`` rows, scoped to one merchant."""

    _model = Customer


class WebhookEventRepository(MerchantScopedRepository[WebhookEvent]):
    """Repository for ``WebhookEvent`` rows, scoped to one merchant."""

    _model = WebhookEvent


class AuditEventRepository(MerchantScopedRepository[AuditEvent]):
    """INSERT + SELECT only repository for ``AuditEvent`` rows (AUD-002).

    UPDATE and DELETE are forbidden; the ``AuditEvent`` table is append-only
    at both the application and database access layer.
    """

    _model = AuditEvent

    async def append(self, **kwargs: Any) -> AuditEvent:
        """Insert a new audit event. ``merchant_id`` is always forced.

        This is the ONLY write method on this repository — there is no
        ``update()`` or ``delete()``.
        """
        return await self.create(**kwargs)  # type: ignore[return-value]


class InterventionRepository(MerchantScopedRepository[Intervention]):
    """Repository for ``Intervention`` rows, scoped to one merchant."""

    _model = Intervention


class PolicyRepository(MerchantScopedRepository[Policy]):
    """Repository for ``Policy`` rows, scoped to one merchant."""

    _model = Policy
