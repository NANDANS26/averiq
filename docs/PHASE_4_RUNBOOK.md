# Averiq — Phase 4 Operations & Runbook
## Operator Guide for Production Orchestration & Queue Management

---

## 1. Overview & Service Dependencies

Averiq's Phase 4 execution environment consists of three primary layers:
1. **Relational Database**: PostgreSQL 18 on `127.0.0.1:5432` (`averiq/averiq@averiq`).
2. **Message Broker / Cache**: Redis on `127.0.0.1:6379`.
3. **Task Workers**: Celery workers consuming queues `default`, `webhook`, `decisions`, and `verification`.

---

## 2. Service Management Procedures

### 2.1 Starting Redis
```powershell
# In a background process / service manager:
& "C:\Program Files\Redis\redis-server.exe" --port 6379
```

### 2.2 Starting PostgreSQL
For production service environments running on Windows:
```powershell
# Starting the managed PostgreSQL Windows service (Administrative shell):
Start-Service postgresql-x64-18

# Verify service status:
Get-Service postgresql-x64-18

# Check connection availability:
& "D:\Program Files\PostgreSQL\18\bin\pg_isready.exe" -h 127.0.0.1 -p 5432
```

For non-elevated developer / sandbox runtime fallback:
```powershell
& "D:\Program Files\PostgreSQL\18\bin\postgres.exe" -D "D:\Program Files\PostgreSQL\18\data"
```

### 2.3 Starting Celery Workers
Windows requires `--pool=solo` for asynchronous multi-task workers:
```powershell
.\.venv\Scripts\celery.exe -A backend.worker.celery_app worker --loglevel=info --pool=solo -Q default,webhook,decisions,verification
```

### 2.4 Verifying Worker & Queue Health
```powershell
# Ping workers
.\.venv\Scripts\celery.exe -A backend.worker.celery_app inspect ping

# Inspect registered tasks
.\.venv\Scripts\celery.exe -A backend.worker.celery_app inspect registered

# Check active task workload
.\.venv\Scripts\celery.exe -A backend.worker.celery_app inspect active
```

---

## 3. Queue Architecture & Responsibilities

| Queue | Consumer Module | Concurrency Semantics | Typical Latency |
|---|---|---|---|
| `webhook` | `backend.webhook_processor.processor.process_webhook_event` | Idempotent on `provider_event_id` | < 100ms |
| `decisions` | `backend.decision_engine.tasks.run_decision_pipeline` | Serialized per case via `SELECT FOR UPDATE` | ~200ms–800ms |
| `default` | `backend.execution_engine.tasks.execute_intervention` | Guard-checked before external gateway dispatch | ~150ms |
| `verification` | `backend.verification_engine.tasks.run_verification` | Bounded retries (max 3), exponential/interval | ~100ms |

---

## 4. Failure Modes & Recovery Procedures

### 4.1 Dead-Lettered Webhooks
- **Symptom**: Webhook events in `webhook_events` with `processing_status = 'DEAD_LETTERED'`.
- **Cause**: Persistent database errors or malformed payloads exceeding `MAX_RETRIES` (5).
- **Remediation**:
  1. Inspect `webhook_events.last_error` for the failure stack trace.
  2. If caused by network glitch or schema bug, run outbox reconciliation:
     ```python
     from backend.webhook_processor.outbox import reconcile_outbox
     # Dispatches pending events
     ```

### 4.2 Human Review Queue Backlog
- **Symptom**: Cases remaining in `ACTION_PENDING` with an associated `human_reviews` entry with `status = 'OPEN'`.
- **Cause**: High-value transactions (> ₹10,000) or high-risk thresholds triggered policy approval gates.
- **Remediation**:
  1. Operator reviews ticket in Dashboard.
  2. Action approved: Case transitions to `ACTION_APPROVED` and execution task is queued.
  3. Action rejected: Case transitions to `STOPPED`.

### 4.3 Gateway Timeouts & Unresolved Verifications
- **Symptom**: Case transitions to `ESCALATED` after 3 failed verification attempts.
- **Cause**: Payment link created, but customer did not complete checkout within the verification window.
- **Remediation**:
  - Cases in `ESCALATED` are safely terminal; operators can inspect gateway logs or trigger follow-up outreach manually.

---

## 5. End-to-End Simulation Run
To verify complete system health on demand:
```powershell
.\.venv\Scripts\python.exe backend/simulation/live_orchestrator_simulation.py
```
This tests standard auto-approval, high-ticket human review routing, STOP policy enforcement, duplicate webhook deduplication, and concurrency serialization.
