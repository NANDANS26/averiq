# Security Policy

## 1. Supported Versions

Averiq is an active research and production simulation platform. Security fixes are applied to the `main` branch.

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |
| < 1.0   | :x:                |

---

## 2. Reporting a Vulnerability

We take the security of payment decision infrastructure seriously. If you discover a security vulnerability in Averiq, please report it privately.

**DO NOT report security vulnerabilities through public GitHub issues.**

Instead, please report security vulnerabilities via email or security advisory:
- **Email**: `security@averiq.dev` (or open a private GitHub Security Advisory)
- Include a detailed description of the vulnerability, steps to reproduce, proof-of-concept code, and the potential impact.
- You should receive an initial acknowledgment within 48 hours.

---

## 3. Security Architecture & Controls

Averiq employs multi-layered defense-in-depth principles:

1. **Authentication & Authorization**:
   - JWT-based authentication with HS256/RS256 and configurable token lifetimes.
   - Strict role-based access control (RBAC): `viewer`, `operator`, `risk_officer`, `admin`.
   - Multi-tenant data isolation: all queries enforce mandatory `merchant_id` row-level scoping.
2. **Secrets & Cryptography**:
   - Webhook signing secrets and sensitive credentials are encrypted at rest using AES-256-GCM.
   - No plaintext API secrets or private tokens are stored in application logs or unmasked in APIs.
3. **Execution Safety & Race Condition Prevention**:
   - Distributed locking via Redis with cryptographically random tokens and TTL safety guarantees.
   - Circuit breakers and velocity limits prevent runaway retry loops on payment rails.
4. **Input Validation**:
   - Strong type enforcement and bounds checking via Pydantic v2 schemas on all public ingestion endpoints.
