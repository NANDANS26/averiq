import axios from "axios";
import type { AxiosInstance, AxiosError, InternalAxiosRequestConfig } from "axios";

/**
 * Typed API error that preserves the structured error body from the backend.
 * The backend returns: { error: string; detail: unknown[]; request_id: string }
 */
export interface ApiError {
  error: string;
  detail: unknown[];
  request_id: string;
  status: number;
}

/**
 * Base axios instance pre-configured for the Averiq backend.
 * Auth token injection and 401 handling are added in task 17.1
 * once the AuthContext is implemented.
 */
export const apiClient: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "/api/v1",
  timeout: 30_000,
  headers: {
    "Content-Type": "application/json",
    Accept: "application/json",
  },
  withCredentials: false,
});

// Request interceptor — attach Bearer token when available & strip redundant /api/v1
apiClient.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    if (config.url && config.url.startsWith("/api/v1")) {
      config.url = config.url.replace(/^\/api\/v1/, "");
    }
    const token = localStorage.getItem("averiq_access_token");
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error: unknown) => Promise.reject(error),
);

// Response interceptor — normalize errors
apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    const apiErr: ApiError = {
      error: "Unknown error",
      detail: [],
      request_id: "",
      status: error.response?.status ?? 0,
    };

    if (error.response?.data && typeof error.response.data === "object") {
      const data = error.response.data as Record<string, unknown>;
      if (typeof data["error"] === "string") apiErr.error = data["error"];
      if (Array.isArray(data["detail"])) apiErr.detail = data["detail"];
      if (typeof data["request_id"] === "string") apiErr.request_id = data["request_id"];
    } else if (error.message) {
      apiErr.error = error.message;
    }

    return Promise.reject(apiErr);
  },
);
