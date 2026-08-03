const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const AUTH_TOKEN_STORAGE_KEY = "spop.authToken";
const REFRESH_TOKEN_STORAGE_KEY = "spop.refreshToken";
const DEVICE_ID_STORAGE_KEY = "spop.deviceId";

export interface HealthResponse {
  status: string;
  environment: string;
}

export interface AuthUser {
  id: string;
  email: string;
  full_name: string;
  role: string;
  branch_id?: string | null;
  is_active: boolean;
}

export interface LoginResponse {
  user: AuthUser;
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function getStoredAuthToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
}

let cachedAuthToken: string | null = null;

export function getAuthToken(): string | null {
  if (cachedAuthToken === null) {
    cachedAuthToken = getStoredAuthToken();
  }
  return cachedAuthToken;
}

export function setAuthToken(token: string | null) {
  cachedAuthToken = token;
  if (typeof window === "undefined") return;
  if (token) window.localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token);
  else window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
}

export function getRefreshToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY);
}

export function setRefreshToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) window.localStorage.setItem(REFRESH_TOKEN_STORAGE_KEY, token);
  else window.localStorage.removeItem(REFRESH_TOKEN_STORAGE_KEY);
}

export function clearAuthToken() {
  setAuthToken(null);
  setRefreshToken(null);
}

/** Stable per-browser device id, persisted so the same device is
 * recognized across sessions (used for refresh-token binding and Phase 3
 * offline sync attribution). */
export function getDeviceId(): string {
  if (typeof window === "undefined") return crypto.randomUUID();
  let deviceId = window.localStorage.getItem(DEVICE_ID_STORAGE_KEY);
  if (!deviceId) {
    deviceId = crypto.randomUUID();
    window.localStorage.setItem(DEVICE_ID_STORAGE_KEY, deviceId);
  }
  return deviceId;
}

export async function checkApiHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE_URL}/health`);
  if (!response.ok) {
    throw new ApiError(response.status, `Health check failed with status ${response.status}`);
  }
  return (await response.json()) as HealthResponse;
}

export async function login(email: string, password: string): Promise<LoginResponse> {
  const response = await fetch(`${API_BASE_URL}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, device_id: getDeviceId() }),
  });
  const payload = await response.json().catch(() => ({ detail: "Login failed" }));
  if (!response.ok) {
    throw new ApiError(response.status, payload.detail ?? "Login failed");
  }
  setAuthToken(payload.access_token);
  setRefreshToken(payload.refresh_token);
  return payload as LoginResponse;
}

export async function logout(): Promise<void> {
  const refreshToken = getRefreshToken();
  if (refreshToken) {
    await fetch(`${API_BASE_URL}/api/v1/auth/logout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    }).catch(() => undefined);
  }
  clearAuthToken();
}

export async function registerDevice(): Promise<void> {
  await request("/devices/register", { method: "POST", body: JSON.stringify({ device_id: getDeviceId(), platform: "web" }) }).catch(() => undefined);
}

export async function getCurrentUser(): Promise<{ user: AuthUser }> {
  const response = await request("/auth/me");
  return response as { user: AuthUser };
}

async function refreshAccessToken(): Promise<boolean> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return false;
  const response = await fetch(`${API_BASE_URL}/api/v1/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!response.ok) return false;
  const payload = await response.json();
  setAuthToken(payload.access_token);
  setRefreshToken(payload.refresh_token);
  return true;
}

export async function request(path: string, init?: RequestInit): Promise<unknown> {
  const attempt = async (): Promise<Response> => {
    const headers = new Headers(init?.headers);
    if (!headers.has("Content-Type") && init?.body) {
      headers.set("Content-Type", "application/json");
    }
    const token = getAuthToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return fetch(`${API_BASE_URL}/api/v1${path}`, { headers, ...init });
  };

  let response = await attempt();
  if (response.status === 401 && path !== "/auth/refresh" && (await refreshAccessToken())) {
    response = await attempt();
  }

  if (response.status === 204) return null;
  const payload = await response.json().catch(() => ({ detail: "Request failed" }));
  if (!response.ok) {
    throw new ApiError(response.status, payload.detail ?? "Request failed");
  }
  return payload;
}
