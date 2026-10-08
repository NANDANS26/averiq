/**
 * Shared TypeScript types for Averiq frontend.
 * These mirror the Pydantic schemas in shared/contracts/ and backend API responses.
 * Financial amounts are always number (integer paise/minor units) — never string or float.
 */

// ---------------------------------------------------------------------------
// Enums (must match backend CaseState, FailureCategory, etc.)
// ---------------------------------------------------------------------------

export type CaseState =
  | "OBSERVED"
  | "AT_RISK"
  | "DETECTED"
  | "EVALUATED"
  | "RECOVERY_ELIGIBLE"
  | "ACTION_PENDING"
  | "AWAITING_APPROVAL"
  | "ACTION_APPROVED"
  | "ACTION_EXECUTING"
  | "EXECUTING"
  | "VERIFYING"
  | "RECOVERED"
  | "STOPPED"
  | "ESCALATED"
  | "FAILED"
  | "UNKNOWN_TERMINAL";

export type TerminalCaseState = "RECOVERED" | "STOPPED" | "ESCALATED" | "FAILED";

export type FailureCategory =
  | "INSUFFICIENT_FUNDS"
  | "CARD_DECLINED"
  | "EXPIRED_CARD"
  | "AUTHENTICATION_FAILURE"
  | "TECHNICAL_FAILURE"
  | "NETWORK_TIMEOUT"
  | "FRAUD_SUSPECTED"
  | "LIMIT_EXCEEDED"
  | "UNKNOWN";

export type InterventionType =
  | "PAYMENT_LINK"
  | "RETRY"
  | "CUSTOMER_ACTION"
  | "PAUSE_SUBSCRIPTION"
  | "ESCALATE_TO_HUMAN"
  | "STOP"
  | "WAIVER"
  | "CUSTOM_NOTIFICATION";

export type ExposureTier = "HIGH" | "MEDIUM" | "LOW";
export type RecoveryRiskTier = "HIGH" | "MEDIUM" | "LOW";
export type UserRole = "ADMIN" | "OPERATOR" | "VIEWER" | "SYSTEM";
export type OperatingMode = "SIMULATED" | "REAL_TEST_MODE" | "DEMO_FIXTURE";
export type PolicyDecisionOutcome = "APPROVED" | "BLOCKED" | "HUMAN_REVIEW";
export type AttributionClass = "DIRECT_INTERVENTION" | "ORGANIC" | "UNKNOWN_ATTRIBUTION";

// ---------------------------------------------------------------------------
// Core domain entities
// ---------------------------------------------------------------------------

export interface Merchant {
  id: string;
  name: string;
  operating_mode: OperatingMode;
  created_at: string;
}

export interface User {
  id: string;
  merchant_id: string;
  email: string;
  role: UserRole;
  created_at: string;
}

export interface RevenueRiskCase {
  id: string;
  merchant_id: string;
  payment_id: string;
  state: CaseState;
  payment_amount_at_risk: number; // integer paise
  currency: string;
  projected_subscription_revenue_at_risk: number | null; // integer paise
  risk_calculation_path: "SINGLE_PAYMENT" | "SUBSCRIPTION_PROJECTED";
  exposure_tier: ExposureTier;
  recovery_risk_tier: RecoveryRiskTier;
  failure_category: FailureCategory | null;
  confidence_score: number | null;
  recovery_probability?: number | null;
  ai_source: string | null;
  decision_timestamp: string | null;
  previous_case_id: string | null;
  recovered_amount?: number | null;
  recovered_currency?: string | null;
  is_demo_fixture: boolean;
  created_at: string;
  updated_at: string;
}

export interface Intervention {
  id: string;
  case_id: string;
  merchant_id: string;
  intervention_type: InterventionType;
  expected_recovery_value: number; // integer paise
  recovery_probability: number;
  policy_decision: PolicyDecisionOutcome;
  blocking_rules: string[];
  evaluated_values: Record<string, unknown>;
  policy_version: string;
  model_version: string;
  execution_status: string;
  created_at: string;
}

export interface AuditEvent {
  id: string;
  case_id: string | null;
  merchant_id: string;
  actor: string;
  action: string;
  result: string;
  reason: string | null;
  policy_version: string | null;
  model_version: string | null;
  correlation_id: string;
  created_at: string;
}

export interface WebhookEvent {
  id: string;
  merchant_id: string;
  provider_event_id: string;
  event_type: string;
  processing_status: string;
  entity_id: string | null;
  created_at: string;
}

export interface Policy {
  id: string;
  merchant_id: string;
  lifecycle_state: "draft" | "validated" | "active" | "retired";
  version: string;
  min_confidence: number;
  cooldown_seconds: number;
  max_retries: Record<string, number>;
  max_amount: Record<string, number>;
  human_approval_threshold: number;
  attribution_window_seconds: number;
  activated_at: string | null;
  created_at: string;
}

// ---------------------------------------------------------------------------
// API response wrappers
// ---------------------------------------------------------------------------

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  cursor: string | null;
}

export interface AuthTokenResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
}

export interface SystemStatus {
  status: "ok" | "degraded";
  version: string;
  operating_mode: OperatingMode;
  is_demo_fixture: boolean;
  failure_lab_enabled: boolean;
}
