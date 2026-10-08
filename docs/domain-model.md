# Averiq Domain Model

This document describes the core domain entities and the rules that govern
their lifecycle. It is the canonical reference for the state machine, case
correlation strategy, and re-failure linkage.

---

## 1. RevenueRiskCase

A `RevenueRiskCase` represents a single lifecycle of recovering one failed
payment. It is the central domain entity in Averiq.

### 1.1 Case uniqueness and correlation

One webhook does **not** necessarily create one new case.

- A case is correlated to a specific payment failure identified by
  `(merchant_id, payment_id)`.
- Multiple webhooks for the same payment may update an existing case rather
  than creating duplicates.
- **At most one active case** per `(merchant_id, payment_id)` may exist at any
  time. "Active" means any non-terminal state. Enforced by the partial unique
  index:

  ```sql
  CREATE UNIQUE INDEX idx_cases_active_payment
      ON revenue_risk_cases(merchant_id, payment_id)
      WHERE state NOT IN ('RECOVERED','STOPPED','ESCALATED','FAILED','UNKNOWN_TERMINAL');
  ```

### 1.2 Re-failure linkage

If a payment produces a second failure *after* a previous case has reached a
terminal state, a **new** case is created with `previous_case_id` referencing
the prior case. This preserves the full history without mutating terminal
records.

---

## 2. State Machine

### 2.1 State definitions

| State              | Terminal? | Description                                                                    |
|--------------------|-----------|--------------------------------------------------------------------------------|
| `OBSERVED`         | No        | Case created; awaiting initial risk assessment                                 |
| `AT_RISK`          | No        | Revenue confirmed at risk; awaiting diagnosis                                  |
| `DIAGNOSED`        | No        | Root cause identified by the Root_Cause_Reasoner                               |
| `RECOVERY_ELIGIBLE`| No        | Recoverability score meets the configured threshold; eligible for intervention |
| `ACTION_PENDING`   | No        | Intervention selected; awaiting Policy_Engine evaluation                       |
| `ACTION_APPROVED`  | No        | Policy_Engine approved the intervention; awaiting execution                    |
| `ACTION_EXECUTING` | No        | Execution_Engine is actively executing the intervention                        |
| `VERIFYING`        | No        | Awaiting outcome confirmation from the payment gateway                         |
| `RETRYABLE`        | No        | Outcome requires re-evaluation; exits to `RECOVERY_ELIGIBLE`                  |
| `RECOVERED`        | **Yes**   | Payment confirmed recovered                                                    |
| `STOPPED`          | **Yes**   | Policy blocked all interventions or STOP intervention was selected             |
| `ESCALATED`        | **Yes**   | Retry budget exhausted or verification permanently ambiguous                   |
| `FAILED`           | **Yes**   | Known, unrecoverable execution error                                           |
| `UNKNOWN_TERMINAL` | **Yes**   | Catch-all terminal state when final outcome cannot be classified               |

### 2.2 Terminal states

Terminal states have no valid outbound transitions. Any transition attempt from
a terminal state is rejected and recorded as an `INVALID_TRANSITION_ATTEMPTED`
AuditEvent (FR-STATE-017).

```python
TERMINAL_STATES: frozenset[str] = frozenset(
    {"RECOVERED", "STOPPED", "ESCALATED", "FAILED", "UNKNOWN_TERMINAL"}
)
```

### 2.3 Allowed transition map

The table below lists every valid outbound transition. States not present have
no outbound transitions (effectively terminal). The full map is encoded in
`backend/state_machine/machine.py` as `ALLOWED_TRANSITIONS`.

| From state          | Allowed target states                                                  |
|---------------------|------------------------------------------------------------------------|
| `OBSERVED`          | `AT_RISK`, `STOPPED`, `FAILED`                                         |
| `AT_RISK`           | `DIAGNOSED`, `STOPPED`, `FAILED`                                       |
| `DIAGNOSED`         | `RECOVERY_ELIGIBLE`, `STOPPED`, `FAILED`                               |
| `RECOVERY_ELIGIBLE` | `ACTION_PENDING`, `STOPPED`, `FAILED`                                  |
| `ACTION_PENDING`    | `ACTION_APPROVED`, `STOPPED`, `FAILED`                                 |
| `ACTION_APPROVED`   | `ACTION_EXECUTING`, `STOPPED`, `FAILED`                                |
| `ACTION_EXECUTING`  | `VERIFYING`, `STOPPED`, `FAILED`                                       |
| `VERIFYING`         | `RECOVERED`, `RETRYABLE`, `ESCALATED`, `FAILED`, `STOPPED`             |
| `RETRYABLE`         | `RECOVERY_ELIGIBLE`, `ESCALATED`, `FAILED`, `STOPPED`                  |
| `RECOVERED`         | *(none — terminal)*                                                    |
| `STOPPED`           | *(none — terminal)*                                                    |
| `ESCALATED`         | *(none — terminal)*                                                    |
| `FAILED`            | *(none — terminal)*                                                    |
| `UNKNOWN_TERMINAL`  | *(none — terminal)*                                                    |

`STOPPED` and `FAILED` are reachable from most non-terminal states to support
graceful termination at any point in the lifecycle (FR-STATE-012,
FR-STATE-014, REL-FAIL-001).

### 2.4 State transition atomicity guarantee

**Every state transition executes atomically within a single database
transaction.** Specifically, the following two operations are committed
together or not at all:

1. `UPDATE revenue_risk_cases SET state = :to_state, updated_at = NOW()`
2. `INSERT INTO audit_events (from_state, to_state, actor, reason, …)`

If the `INSERT` fails for any reason, the entire transaction rolls back. There
is **no un-audited state change** — this is a hard invariant enforced by the
implementation (`backend/state_machine/machine.py`).

The relevant code path in `StateMachine.transition()`:

```python
# Step 3: update case state
case.state = to_state
case.updated_at = now
await session.execute(
    update(RevenueRiskCase)
    .where(RevenueRiskCase.id == case_id)
    .values(state=to_state, updated_at=now)
)

# Step 4: INSERT AuditEvent — SAME transaction
session.add(audit_event)
await session.flush()  # both writes flushed; caller's commit() finalises atomically
```

### 2.5 Concurrency safety: `FOR UPDATE` vs `SKIP LOCKED`

#### `SELECT FOR UPDATE` — case state serialisation

```sql
SELECT * FROM revenue_risk_cases WHERE id = :case_id FOR UPDATE
```

Used exclusively in `StateMachine.transition()`. This acquires a **row-level
exclusive lock** on the specific case row. Concurrent Celery workers that
attempt to transition the same case will **block** at this statement until the
first worker commits or rolls back. After unblocking, the second worker
re-reads the case state and validates the transition from the *updated* state.
If the case has already been advanced to a state where the intended transition
is invalid, the second worker backs off and re-schedules.

**Why not SKIP LOCKED here?** `SKIP LOCKED` means "skip rows that are already
locked and move on to the next available row". This is correct semantics for
a job queue (pick any available webhook to process). It is *wrong* for state
serialisation: if a worker skips a locked case, it proceeds as if it never saw
it, which can result in lost transitions and missed audit events. For case
state, serialisation is mandatory — workers must wait, not skip.

#### `SELECT FOR UPDATE SKIP LOCKED` — outbox queue consumption

```sql
SELECT … FROM outbox_events WHERE enqueued = FALSE … FOR UPDATE SKIP LOCKED
```

Used exclusively by the Celery beat outbox publisher (`backend/webhook_processor/outbox.py`).
Multiple beat instances must not double-publish the same outbox record.
`SKIP LOCKED` is appropriate here because the goal is to distribute queue work
across workers — any unlocked outbox record is equivalent. A worker that finds
a record already locked simply processes a different record; the locked one
will be processed by whoever holds the lock.

---

## 3. AuditEvent

The `AuditEvent` table is **append-only** at the application layer (AUD-002).
The PostgreSQL application role grants `INSERT, SELECT` on this table — no
`UPDATE` or `DELETE`. The `AuditEventRepository` exposes only `append(event)`.

Every `AuditEvent` records:

| Field                | Description                                                         |
|----------------------|---------------------------------------------------------------------|
| `actor`              | `"SYSTEM"` for automated steps; user ID string for human actions    |
| `action_type`        | e.g. `"state_transition"`, `"INVALID_TRANSITION_ATTEMPTED"`         |
| `from_state`         | State before the transition                                         |
| `to_state`           | State after the transition (or attempted target state if invalid)   |
| `correlation_id`     | End-to-end chain identifier linking all entries for one workflow    |
| `payload`            | JSON blob with `reason`, `from_state`, `to_state`, and other context|
| `created_at`         | UTC timestamp (timezone-aware; `TIMESTAMP WITH TIME ZONE`)          |

**Invalid transition attempts** are also audited: a rejected transition
produces an `INVALID_TRANSITION_ATTEMPTED` event with the rejected target
state recorded in `to_state` and the rejection reason in `payload`.

---

## 4. Financial data invariants

- All financial amounts (`payment_amount_at_risk`, `recovered_amount`, etc.)
  are stored as **integer paise** (`BIGINT` in PostgreSQL; `int` in Python).
  Never `float`. (DATA-001, DATA-003)
- Every financial record carries an explicit `currency` field (`CHAR(3)`,
  ISO 4217). (DATA-002, DATA-005)
- All timestamps are timezone-aware UTC (`TIMESTAMP WITH TIME ZONE`). Naive
  datetimes are forbidden for financial events. (DATA-004)

---

## 5. Source code references

| Concept                        | File                                             |
|--------------------------------|--------------------------------------------------|
| `ALLOWED_TRANSITIONS` map      | `backend/state_machine/machine.py`               |
| `TERMINAL_STATES` frozenset    | `backend/state_machine/machine.py`               |
| `StateMachine.transition()`    | `backend/state_machine/machine.py`               |
| `InvalidTransitionError`       | `backend/state_machine/machine.py`               |
| `CaseNotFoundError`            | `backend/state_machine/machine.py`               |
| `RevenueRiskCase` ORM model    | `backend/domain/models.py`                       |
| `AuditEvent` ORM model         | `backend/domain/models.py`                       |
| `CaseState` enum               | `shared/contracts/enums.py`                      |
