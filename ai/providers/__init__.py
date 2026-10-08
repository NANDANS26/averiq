"""AI provider implementations for Averiq.

This package contains concrete implementations of the ``AIProvider`` protocol
defined in ``shared/contracts/protocols.py``.

Available providers:

* ``stub.StubAIProvider`` — deterministic, zero-configuration provider.
  Used in local development, CI, unit tests, and bulk evaluation.
  FR-DIAG-007, AI-PROV-001.

* ``external_llm.ExternalLLMProvider`` — external LLM provider with prompt
  injection defence (task 9.2).  FR-DIAG-008, SEC-AI-002.

Both implementations satisfy the ``AIProvider`` protocol via structural
subtyping (``typing.Protocol``) — no explicit inheritance required.
"""
