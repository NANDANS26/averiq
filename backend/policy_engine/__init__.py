"""Policy Engine — deterministic rule layer that approves or blocks AI proposals.

AI never directly authorises a financial action. Every proposal from the
Decision_Engine passes through the Policy_Engine before the Execution_Engine
acts. This separation is structural: the Policy_Engine is a mandatory call-graph
gate between Decision_Engine and Execution_Engine.

Modules:
- engine.py       — PolicyEngine with 7-rule evaluation order
- lifecycle.py    — Policy draft → validated → active → retired lifecycle
- human_review.py — HumanReview workflow (OPEN → ASSIGNED → APPROVED/REJECTED)
"""
