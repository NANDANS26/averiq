import { apiClient } from "./client";
import type { RevenueRiskCase, AuditEvent, Intervention } from "@/types";

/**
 * casesApi — typed wrappers around /api/v1/cases endpoints.
 * Financial amounts in all responses are integers (paise/minor units).
 */
export const casesApi = {
  /** List cases with optional state filter, cursor-based pagination. */
  list: (params?: { state?: string; limit?: number; cursor?: string }) =>
    apiClient.get("/api/v1/cases", { params }).then((r) => r.data),

  /** Get a single case by ID. */
  get: (id: string) =>
    apiClient.get<RevenueRiskCase>(`/api/v1/cases/${id}`).then((r) => r.data),

  /**
   * Get the full decision trace for a case (DEC-TRACE-001 through 005).
   * Returns a free-form trace object — shape depends on how far the case has progressed.
   */
  getTrace: (id: string) =>
    apiClient.get(`/api/v1/cases/${id}/trace`).then((r) => r.data),

  /**
   * Get the ordered AuditEvent history for a case (AUD-004).
   * The backend guarantees no raw gateway payloads or PII are present (AUD-006).
   */
  getAudit: (id: string) =>
    apiClient.get<AuditEvent[]>(`/api/v1/cases/${id}/audit`).then((r) => r.data),

  /** Get interventions for a case. */
  getInterventions: (id: string) =>
    apiClient
      .get<Intervention[]>(`/api/v1/cases/${id}/interventions`)
      .then((r) => r.data),

  /** Revenue risk summary aggregated by state and calculation path (FR-RISK-006). */
  getRevenueSummary: () =>
    apiClient.get("/api/v1/analytics/revenue").then((r) => r.data),

  /** Named metrics summary (attribution, rates). */
  getMetrics: () =>
    apiClient.get("/api/v1/analytics/metrics").then((r) => r.data),
};
