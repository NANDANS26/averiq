"""Decision Engine — orchestrates the full AI → Policy → Execution pipeline.

Modules:
- risk_assessor.py       — revenue risk assessment (exposure tier, risk tier)
- fallback_classifier.py — deterministic failure category classifier
- root_cause_reasoner.py — AI diagnosis orchestrator with fallback
- intervention_engine.py — candidate intervention generation and ranking
- expected_value.py      — EV computation using Decimal with ROUND_FLOOR
"""
