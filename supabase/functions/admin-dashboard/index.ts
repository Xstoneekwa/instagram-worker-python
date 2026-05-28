// deno-lint-ignore-file no-explicit-any
/**
 * Read-only admin dashboard API over Dashboard Foundation 1A RPCs.
 *
 * This function is internal/admin only in DF-1B. It accepts only the
 * ADMIN_DASHBOARD_INTERNAL_API_TOKEN bearer token and exposes no direct table
 * reads, settings mutations, client JWT access, or device controls.
 */

type AdminDashboardAction = "health" | "manage_overview" | "radar_overview";
type Fetcher = (
  input: string | URL | Request,
  init?: RequestInit,
) => Promise<Response>;
type Dependencies = {
  fetch?: Fetcher;
  log?: (event: string, payload: Record<string, unknown>) => void;
  requestId?: () => string;
};
type RpcClient = {
  rpc: (
    functionName: string,
    body: Record<string, unknown>,
  ) => Promise<Response>;
};
type ParsedPayload = {
  action: AdminDashboardAction;
  limit: number;
  offset: number;
  search: string | null;
  status: string | null;
  health: string | null;
};
type ErrorCode =
  | "unauthorized"
  | "validation_error"
  | "unsupported_action"
  | "rpc_failed"
  | "internal_error";

const MAX_LIMIT = 200;
const DEFAULT_LIMIT = 100;
const MAX_SEARCH_LENGTH = 200;
const MAX_FILTER_LENGTH = 80;
const ACTIONS = new Set(["health", "manage_overview", "radar_overview"]);
const FORBIDDEN_KEYS = new Set([
  "password",
  "password_hash",
  "password_length",
  "secret",
  "secret_ref",
  "secret_provider",
  "vault",
  "vault_id",
  "vault_payload",
  "token",
  "cookie",
  "authorization",
  "service_role",
  "service_role_key",
  "internal_api_token",
  "webhook",
  "webhook_url",
  "slack_url",
  "discord_url",
  "raw_xml",
  "xml",
  "screenshot",
  "screenshot_path",
  "raw_logs",
  "raw_metadata",
  "metadata",
  "adb_serial",
  "usb_port",
  "hub_port",
  "device_udid",
]);

function jsonResponse(
  status: number,
  body: Record<string, unknown>,
  headers: HeadersInit = {},
) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      ...headers,
    },
  });
}

function errorResponse(
  status: number,
  code: ErrorCode,
  message: string,
  headers: HeadersInit = {},
) {
  return jsonResponse(status, { ok: false, error: { code, message } }, headers);
}

function corsHeaders(req: Request): HeadersInit {
  const allowed = (Deno.env.get("ADMIN_DASHBOARD_ALLOWED_ORIGINS") || "")
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
  const origin = req.headers.get("origin") || "";
  const allowOrigin =
    allowed.includes("*") || (origin && allowed.includes(origin)) ? origin : "";
  return {
    ...(allowOrigin ? { "access-control-allow-origin": allowOrigin } : {}),
    "access-control-allow-methods": "POST, OPTIONS",
    "access-control-allow-headers": "authorization, content-type, x-request-id",
    "vary": "origin",
  };
}

function requireEnv(name: string): string {
  const value = (Deno.env.get(name) || "").trim();
  if (!value) throw new Error(`missing_env:${name}`);
  return value;
}

function requestId(req: Request, deps: Dependencies): string {
  return req.headers.get("x-request-id") || deps.requestId?.() ||
    crypto.randomUUID();
}

function logEvent(
  deps: Dependencies,
  event: string,
  payload: Record<string, unknown>,
) {
  const logger = deps.log ?? ((name, body) =>
    console.log(JSON.stringify({
      ts: new Date().toISOString(),
      event: name,
      ...body,
    })));
  logger(event, payload);
}

function bearerToken(req: Request): string {
  const auth = req.headers.get("authorization") || "";
  return auth.toLowerCase().startsWith("bearer ") ? auth.slice(7).trim() : "";
}

function assertInternalAuthenticated(
  req: Request,
  headers: HeadersInit,
): { ok: true } | { ok: false; response: Response } {
  const expected = (Deno.env.get("ADMIN_DASHBOARD_INTERNAL_API_TOKEN") || "")
    .trim();
  const got = bearerToken(req);
  if (!expected || !got || got !== expected) {
    return {
      ok: false,
      response: errorResponse(401, "unauthorized", "Unauthorized.", headers),
    };
  }
  return { ok: true };
}

export function clampLimit(value: unknown): number {
  if (value == null) return DEFAULT_LIMIT;
  const raw = Number(value);
  if (!Number.isFinite(raw)) return DEFAULT_LIMIT;
  return Math.max(1, Math.min(MAX_LIMIT, Math.trunc(raw)));
}

export function normalizeOffset(value: unknown): number {
  if (value == null) return 0;
  const raw = Number(value);
  if (!Number.isFinite(raw) || raw < 0) return 0;
  return Math.trunc(raw);
}

function optionalBoundedString(
  value: unknown,
  key: string,
  maxLength: number,
): { ok: true; value: string | null } | { ok: false; error: string } {
  if (value == null) return { ok: true, value: null };
  if (typeof value !== "string") return { ok: false, error: `${key}_invalid` };
  const trimmed = value.trim();
  if (!trimmed) return { ok: true, value: null };
  if (trimmed.length > maxLength) {
    return { ok: false, error: `${key}_too_long` };
  }
  return { ok: true, value: trimmed };
}

export function validatePayload(
  payload: Record<string, unknown>,
): { ok: true; payload: ParsedPayload } | {
  ok: false;
  error: string;
  code: ErrorCode;
  status: number;
} {
  const rawAction = typeof payload.action === "string"
    ? payload.action.trim()
    : "";
  if (!rawAction) {
    return {
      ok: false,
      error: "action_required",
      code: "validation_error",
      status: 400,
    };
  }
  if (!ACTIONS.has(rawAction)) {
    return {
      ok: false,
      error: "unsupported_action",
      code: "unsupported_action",
      status: 400,
    };
  }

  const search = optionalBoundedString(
    payload.search,
    "search",
    MAX_SEARCH_LENGTH,
  );
  if (!search.ok) {
    return {
      ok: false,
      error: search.error,
      code: "validation_error",
      status: 400,
    };
  }

  const status = optionalBoundedString(
    payload.status,
    "status",
    MAX_FILTER_LENGTH,
  );
  if (!status.ok) {
    return {
      ok: false,
      error: status.error,
      code: "validation_error",
      status: 400,
    };
  }

  const health = optionalBoundedString(
    payload.health,
    "health",
    MAX_FILTER_LENGTH,
  );
  if (!health.ok) {
    return {
      ok: false,
      error: health.error,
      code: "validation_error",
      status: 400,
    };
  }

  if (rawAction !== "radar_overview" && health.value !== null) {
    return {
      ok: false,
      error: "health_filter_requires_radar_overview",
      code: "validation_error",
      status: 400,
    };
  }

  return {
    ok: true,
    payload: {
      action: rawAction as AdminDashboardAction,
      limit: clampLimit(payload.limit),
      offset: normalizeOffset(payload.offset),
      search: search.value,
      status: status.value,
      health: health.value,
    },
  };
}

function hasForbiddenKey(key: string): boolean {
  const normalized = key.trim().toLowerCase();
  if (normalized === "password_display") return false;
  return FORBIDDEN_KEYS.has(normalized);
}

function hasForbiddenValue(value: string): boolean {
  const normalized = value.toLowerCase();
  return normalized.includes("supabase_vault://") ||
    normalized.includes("service_role") ||
    normalized.includes("authorization:") ||
    normalized.includes("bearer ") ||
    normalized.includes("hooks.slack") ||
    normalized.includes("discord.com/api/webhooks") ||
    normalized.includes("<?xml") ||
    normalized.includes("logs/screenshots/") ||
    normalized.includes("logs/xml/");
}

export function sanitizeForResponse(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sanitizeForResponse);
  if (!value || typeof value !== "object") {
    if (typeof value === "string" && hasForbiddenValue(value)) return null;
    return value;
  }

  const output: Record<string, unknown> = {};
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    if (hasForbiddenKey(key)) continue;
    output[key] = sanitizeForResponse(child);
  }
  return output;
}

function itemListFromRpc(data: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(data)) return [];
  const sanitized = sanitizeForResponse(data);
  return Array.isArray(sanitized)
    ? sanitized as Array<Record<string, unknown>>
    : [];
}

function createServiceRoleRpcClient(deps: Dependencies = {}): RpcClient {
  const url = requireEnv("SUPABASE_URL").replace(/\/+$/, "");
  const key = requireEnv("SUPABASE_SERVICE_ROLE_KEY");
  const fetcher = deps.fetch ?? fetch;
  return {
    rpc: (functionName: string, body: Record<string, unknown>) => {
      const headers = new Headers();
      headers.set("apikey", key);
      headers.set("authorization", `Bearer ${key}`);
      headers.set("content-type", "application/json");
      return fetcher(`${url}/rest/v1/rpc/${functionName}`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
      });
    },
  };
}

async function callRpc(payload: ParsedPayload, deps: Dependencies) {
  const supabase = createServiceRoleRpcClient(deps);
  if (payload.action === "manage_overview") {
    return await supabase.rpc("get_admin_account_overview", {
      p_limit: payload.limit,
      p_offset: payload.offset,
      p_search: payload.search,
      p_status: payload.status,
    });
  }

  return await supabase.rpc("get_admin_radar_overview", {
    p_limit: payload.limit,
    p_offset: payload.offset,
    p_search: payload.search,
    p_status: payload.status,
    p_health: payload.health,
  });
}

async function parseRpcItems(
  res: Response,
): Promise<
  { ok: true; items: Array<Record<string, unknown>> } | { ok: false }
> {
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    return { ok: false };
  }
  if (!res.ok) return { ok: false };
  return { ok: true, items: itemListFromRpc(body) };
}

export async function handleRequest(
  req: Request,
  deps: Dependencies = {},
): Promise<Response> {
  const headers = corsHeaders(req);
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers });
  }
  if (req.method !== "POST") {
    return errorResponse(405, "validation_error", "POST is required.", headers);
  }

  const auth = assertInternalAuthenticated(req, headers);
  if (!auth.ok) return auth.response;

  let rawPayload: Record<string, unknown>;
  try {
    rawPayload = await req.json();
  } catch {
    return errorResponse(
      400,
      "validation_error",
      "Invalid JSON body.",
      headers,
    );
  }
  if (
    !rawPayload || typeof rawPayload !== "object" || Array.isArray(rawPayload)
  ) {
    return errorResponse(
      400,
      "validation_error",
      "Payload must be a JSON object.",
      headers,
    );
  }

  const parsed = validatePayload(rawPayload);
  if (!parsed.ok) {
    return errorResponse(parsed.status, parsed.code, parsed.error, headers);
  }

  const payload = parsed.payload;
  const rid = requestId(req, deps);
  if (payload.action === "health") {
    logEvent(deps, "admin_dashboard_health", {
      request_id: rid,
      action: "health",
      status: 200,
    });
    return jsonResponse(200, {
      ok: true,
      service: "admin-dashboard",
      version: "df-1b",
    }, headers);
  }

  try {
    const rpcResult = await parseRpcItems(await callRpc(payload, deps));
    if (!rpcResult.ok) {
      logEvent(deps, "admin_dashboard_rpc_failed", {
        request_id: rid,
        action: payload.action,
        limit: payload.limit,
        offset: payload.offset,
        status: 502,
        error: "rpc_failed",
      });
      return errorResponse(
        502,
        "rpc_failed",
        "Dashboard projection query failed.",
        headers,
      );
    }

    logEvent(deps, "admin_dashboard_rpc_succeeded", {
      request_id: rid,
      action: payload.action,
      limit: payload.limit,
      offset: payload.offset,
      count: rpcResult.items.length,
      status: 200,
    });
    return jsonResponse(200, {
      ok: true,
      action: payload.action,
      count: rpcResult.items.length,
      items: rpcResult.items,
    }, headers);
  } catch {
    logEvent(deps, "admin_dashboard_unhandled_error", {
      request_id: rid,
      action: payload.action,
      limit: payload.limit,
      offset: payload.offset,
      status: 500,
      error: "internal_error",
    });
    return errorResponse(
      500,
      "internal_error",
      "Internal dashboard error.",
      headers,
    );
  }
}

if (import.meta.main) {
  Deno.serve((req) => handleRequest(req));
}
