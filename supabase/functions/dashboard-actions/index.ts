// deno-lint-ignore-file no-explicit-any
/**
 * Safe dashboard action read API.
 *
 * Exposes count/list only. All reads go through service-role PostgREST calls;
 * clients never read public.account_dashboard_actions directly.
 */

type DashboardAction = "count" | "list";
type Audience = "client" | "admin" | "assistant" | "ops";
type ProducerAuth =
  | { ok: true; mode: "internal"; authUserId: null }
  | { ok: true; mode: "client"; authUserId: string }
  | { ok: false; response: Response };
type Fetcher = (input: string | URL | Request, init?: RequestInit) => Promise<Response>;
type Dependencies = {
  fetch?: Fetcher;
  log?: (event: string, payload: Record<string, unknown>) => void;
  requestId?: () => string;
};
type ParsedPayload = {
  action: DashboardAction;
  accountId: string | null;
  audience: Audience | null;
  status: string | null;
  limit: number;
  offset: number;
};
type AccessScope = {
  audience: Audience | null;
  clientId: string | null;
  ownedAccountIds: string[];
  accountId: string | null;
};

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const ACTIVE_STATUSES = ["pending", "acknowledged", "pending_verification"];
const STATUS_ALLOWLIST = new Set([
  "pending",
  "acknowledged",
  "pending_verification",
  "resolved",
  "dismissed",
  "ignored",
]);
const AUDIENCE_ALLOWLIST = new Set(["client", "admin", "assistant", "ops"]);
const SEVERITIES = ["info", "warning", "error", "critical"];
const FORBIDDEN_TOP_LEVEL_FIELDS = new Set([
  "password",
  "secret_ref",
  "secret_provider",
  "token",
  "cookie",
  "raw_secret",
  "webhook_url",
  "service_role",
  "vault_payload",
]);
const FORBIDDEN_METADATA_FIELDS = new Set([
  "password",
  "secret",
  "secret_ref",
  "token",
  "cookie",
  "webhook_url",
  "service_role",
  "vault_payload",
]);

export function validateForbiddenFields(payload: Record<string, unknown>): string | null {
  for (const [key, value] of Object.entries(payload || {})) {
    if (FORBIDDEN_TOP_LEVEL_FIELDS.has(key)) return key;
    if (key === "metadata" && value && typeof value === "object" && !Array.isArray(value)) {
      for (const nestedKey of Object.keys(value as Record<string, unknown>)) {
        if (FORBIDDEN_METADATA_FIELDS.has(nestedKey)) return `metadata.${nestedKey}`;
      }
    }
  }
  return null;
}

export function clampLimit(value: unknown): number {
  const raw = value == null ? 20 : Number(value);
  if (!Number.isFinite(raw)) return 20;
  return Math.max(1, Math.min(100, Math.trunc(raw)));
}

export function normalizeOffset(value: unknown): number {
  const raw = value == null ? 0 : Number(value);
  if (!Number.isFinite(raw) || raw < 0) return 0;
  return Math.trunc(raw);
}

export function validatePayload(payload: Record<string, unknown>): { ok: true; payload: ParsedPayload } | {
  ok: false;
  error: string;
  status?: number;
} {
  const forbidden = validateForbiddenFields(payload);
  if (forbidden) return { ok: false, error: `field_forbidden:${forbidden}`, status: 400 };

  const action = String(payload.action || "").trim();
  if (action !== "count" && action !== "list") return { ok: false, error: "invalid_action", status: 400 };

  let accountId: string | null = null;
  if (payload.account_id != null && String(payload.account_id).trim() !== "") {
    if (!isUuid(payload.account_id)) return { ok: false, error: "account_id_invalid", status: 400 };
    accountId = String(payload.account_id).trim();
  }

  let audience: Audience | null = null;
  if (payload.audience != null && String(payload.audience).trim() !== "") {
    const rawAudience = String(payload.audience).trim();
    if (!AUDIENCE_ALLOWLIST.has(rawAudience)) return { ok: false, error: "audience_invalid", status: 400 };
    audience = rawAudience as Audience;
  }

  let status: string | null = null;
  if (payload.status != null && String(payload.status).trim() !== "") {
    const rawStatus = String(payload.status).trim();
    if (!STATUS_ALLOWLIST.has(rawStatus)) return { ok: false, error: "status_invalid", status: 400 };
    status = rawStatus;
  }

  return {
    ok: true,
    payload: {
      action,
      accountId,
      audience,
      status,
      limit: clampLimit(payload.limit),
      offset: normalizeOffset(payload.offset),
    },
  };
}

function jsonResponse(status: number, body: Record<string, unknown>, headers: HeadersInit = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      ...headers,
    },
  });
}

function corsHeaders(req: Request): HeadersInit {
  const allowed = (Deno.env.get("DASHBOARD_ACTIONS_ALLOWED_ORIGINS") || "")
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
  const origin = req.headers.get("origin") || "";
  const allowOrigin = allowed.includes("*") || (origin && allowed.includes(origin)) ? origin : "";
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

function isUuid(value: unknown): value is string {
  return typeof value === "string" && UUID_RE.test(value.trim());
}

function requestId(req: Request, deps: Dependencies): string {
  return req.headers.get("x-request-id") || deps.requestId?.() || crypto.randomUUID();
}

function logEvent(deps: Dependencies, event: string, payload: Record<string, unknown>) {
  const logger = deps.log ?? ((name, body) => console.log(JSON.stringify({
    ts: new Date().toISOString(),
    event: name,
    ...body,
  })));
  logger(event, payload);
}

async function verifyClientJwt(token: string, deps: Dependencies): Promise<string | null> {
  const url = requireEnv("SUPABASE_URL").replace(/\/+$/, "");
  const key = requireEnv("SUPABASE_SERVICE_ROLE_KEY");
  const fetcher = deps.fetch ?? fetch;
  const res = await fetcher(`${url}/auth/v1/user`, {
    headers: {
      "apikey": key,
      "authorization": `Bearer ${token}`,
    },
  });
  if (!res.ok) return null;
  const user = await res.json();
  return typeof user?.id === "string" && isUuid(user.id) ? user.id : null;
}

async function assertAuthenticated(req: Request, deps: Dependencies): Promise<ProducerAuth> {
  const expected = (Deno.env.get("DASHBOARD_ACTIONS_INTERNAL_API_TOKEN") || "").trim();
  const auth = req.headers.get("authorization") || "";
  const got = auth.toLowerCase().startsWith("bearer ") ? auth.slice(7).trim() : "";
  const headers = corsHeaders(req);
  if (!got) return { ok: false, response: jsonResponse(401, { ok: false, error: "unauthorized" }, headers) };
  if (expected && got === expected) return { ok: true, mode: "internal", authUserId: null };
  const authUserId = await verifyClientJwt(got, deps);
  if (!authUserId) return { ok: false, response: jsonResponse(401, { ok: false, error: "unauthorized" }, headers) };
  return { ok: true, mode: "client", authUserId };
}

async function supabaseFetch(path: string, init: RequestInit = {}, deps: Dependencies = {}) {
  const url = requireEnv("SUPABASE_URL").replace(/\/+$/, "");
  const key = requireEnv("SUPABASE_SERVICE_ROLE_KEY");
  const headers = new Headers(init.headers || {});
  headers.set("apikey", key);
  headers.set("authorization", `Bearer ${key}`);
  if (!headers.has("content-type") && init.body) headers.set("content-type", "application/json");
  const fetcher = deps.fetch ?? fetch;
  return await fetcher(`${url}${path}`, { ...init, headers });
}

async function supabaseJson(path: string, init: RequestInit = {}, deps: Dependencies = {}): Promise<any> {
  const res = await supabaseFetch(path, init, deps);
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) throw new Error(`supabase_error:${res.status}`);
  return body;
}

async function rpcBoolean(path: string, payload: Record<string, unknown>, deps: Dependencies): Promise<boolean> {
  const result = await supabaseJson(path, { method: "POST", body: JSON.stringify(payload) }, deps);
  return result === true;
}

async function clientIdForAuthUser(authUserId: string, deps: Dependencies): Promise<string | null> {
  const rows = await supabaseJson(
    `/rest/v1/client_users?auth_user_id=eq.${encodeURIComponent(authUserId)}&status=eq.active&select=client_id&limit=1`,
    { method: "GET" },
    deps,
  );
  const clientId = Array.isArray(rows) && rows[0] ? rows[0].client_id : null;
  return isUuid(clientId) ? clientId : null;
}

async function accountIdsForClient(clientId: string, deps: Dependencies): Promise<string[]> {
  const rows = await supabaseJson(
    `/rest/v1/client_instagram_accounts?client_id=eq.${encodeURIComponent(clientId)}&select=account_id`,
    { method: "GET" },
    deps,
  );
  if (!Array.isArray(rows)) return [];
  return rows.map((row) => String(row.account_id || "").trim()).filter(isUuid);
}

async function hasClientAccountAccess(authUserId: string, accountId: string, deps: Dependencies): Promise<boolean> {
  return await rpcBoolean("/rest/v1/rpc/client_can_manage_instagram_account", {
    p_auth_user_id: authUserId,
    p_account_id: accountId,
  }, deps);
}

async function resolveAccess(
  payload: ParsedPayload,
  auth: Extract<ProducerAuth, { ok: true }>,
  deps: Dependencies,
): Promise<{ ok: true; scope: AccessScope } | { ok: false; status: number; error: string }> {
  if (auth.mode === "internal") {
    return {
      ok: true,
      scope: {
        audience: payload.audience,
        clientId: null,
        ownedAccountIds: [],
        accountId: payload.accountId,
      },
    };
  }

  if (payload.audience && payload.audience !== "client") {
    return { ok: false, status: 403, error: "audience_not_allowed" };
  }

  try {
    const clientId = await clientIdForAuthUser(auth.authUserId, deps);
    if (!clientId) return { ok: false, status: 403, error: "account_not_allowed" };

    if (payload.accountId) {
      const hasAccess = await hasClientAccountAccess(auth.authUserId, payload.accountId, deps);
      if (!hasAccess) return { ok: false, status: 403, error: "account_not_allowed" };
    }

    const ownedAccountIds = payload.accountId ? [payload.accountId] : await accountIdsForClient(clientId, deps);
    return {
      ok: true,
      scope: {
        audience: "client",
        clientId,
        ownedAccountIds,
        accountId: payload.accountId,
      },
    };
  } catch {
    return { ok: false, status: 503, error: "account_ownership_check_failed" };
  }
}

function encodeFilterValue(value: string): string {
  return encodeURIComponent(value);
}

function buildVisibilityFilters(scope: AccessScope): string[] {
  const filters: string[] = [];
  if (scope.audience) filters.push(`audience=eq.${encodeFilterValue(scope.audience)}`);
  if (scope.accountId) filters.push(`account_id=eq.${encodeFilterValue(scope.accountId)}`);

  if (scope.clientId) {
    if (scope.accountId) {
      filters.push(`or=(client_id.eq.${scope.clientId},account_id.eq.${scope.accountId})`);
    } else if (scope.ownedAccountIds.length > 0) {
      filters.push(`or=(client_id.eq.${scope.clientId},account_id.in.(${scope.ownedAccountIds.join(",")}))`);
    } else {
      filters.push(`client_id=eq.${encodeFilterValue(scope.clientId)}`);
    }
  }
  return filters;
}

function buildQuery(params: Record<string, string | number | boolean | null | undefined>): string {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(params)) {
    if (value == null) continue;
    parts.push(`${key}=${value}`);
  }
  return parts.join("&");
}

function activeStatusFilter(): string {
  return `status=in.(${ACTIVE_STATUSES.join(",")})`;
}

function countStatusFilters(status: string | null): string[] {
  if (!status) return [activeStatusFilter()];
  if (!ACTIVE_STATUSES.includes(status)) return ["status=eq.__no_active_status__"];
  return [`status=eq.${encodeFilterValue(status)}`];
}

async function exactCount(filters: string[], deps: Dependencies): Promise<number> {
  const path = `/rest/v1/account_dashboard_actions?${filters.join("&")}&select=id`;
  const res = await supabaseFetch(path, {
    method: "GET",
    headers: {
      prefer: "count=exact",
      range: "0-0",
    },
  }, deps);
  if (!res.ok) throw new Error(`count_failed:${res.status}`);
  const range = res.headers.get("content-range") || "";
  const total = Number.parseInt(range.split("/")[1] || "0", 10);
  return Number.isFinite(total) ? total : 0;
}

async function handleCount(
  req: Request,
  payload: ParsedPayload,
  auth: Extract<ProducerAuth, { ok: true }>,
  deps: Dependencies,
) {
  const headers = corsHeaders(req);
  const rid = requestId(req, deps);
  const access = await resolveAccess(payload, auth, deps);
  if (!access.ok) {
    logEvent(deps, "dashboard_actions_count_rejected", {
      request_id: rid,
      account_id: payload.accountId,
      error: access.error,
    });
    return jsonResponse(access.status, { ok: false, error: access.error, request_id: rid }, headers);
  }

  const base = buildVisibilityFilters(access.scope);
  const statusFilters = countStatusFilters(payload.status);
  const activeBase = [...base, ...statusFilters];

  try {
    const pendingCount = await exactCount(activeBase, deps);
    const blockingCount = await exactCount([...activeBase, "blocking_campaign=eq.true"], deps);
    const clientRequiredCount = await exactCount([
      ...base,
      ...statusFilters,
      "audience=eq.client",
      "requires_client_action=eq.true",
    ], deps);
    const countsBySeverity: Record<string, number> = {};
    for (const severity of SEVERITIES) {
      countsBySeverity[severity] = await exactCount([...activeBase, `severity=eq.${severity}`], deps);
    }

    return jsonResponse(200, {
      ok: true,
      request_id: rid,
      pending_count: pendingCount,
      blocking_count: blockingCount,
      client_required_count: clientRequiredCount,
      counts_by_severity: countsBySeverity,
    }, headers);
  } catch {
    return jsonResponse(500, { ok: false, error: "dashboard_actions_count_failed", request_id: rid }, headers);
  }
}

function safeActionRow(row: Record<string, any>) {
  return {
    id: row.id,
    account_id: row.account_id,
    action_type: row.action_type,
    status: row.status,
    severity: row.severity,
    audience: row.audience,
    requires_client_action: row.requires_client_action === true,
    blocking_campaign: row.blocking_campaign === true,
    title: row.title,
    safe_client_message: row.safe_client_message ?? null,
    action_label: row.action_label ?? null,
    action_deep_link: row.action_deep_link ?? null,
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}

async function handleList(
  req: Request,
  payload: ParsedPayload,
  auth: Extract<ProducerAuth, { ok: true }>,
  deps: Dependencies,
) {
  const headers = corsHeaders(req);
  const rid = requestId(req, deps);
  const access = await resolveAccess(payload, auth, deps);
  if (!access.ok) {
    logEvent(deps, "dashboard_actions_list_rejected", {
      request_id: rid,
      account_id: payload.accountId,
      error: access.error,
    });
    return jsonResponse(access.status, { ok: false, error: access.error, request_id: rid }, headers);
  }

  const filters = buildVisibilityFilters(access.scope);
  if (payload.status) filters.push(`status=eq.${encodeFilterValue(payload.status)}`);

  const select = "id,account_id,action_type,status,severity,audience,requires_client_action,blocking_campaign,title,safe_client_message,action_label,action_deep_link,created_at,updated_at";
  const query = buildQuery({
    select,
    order: "created_at.desc,id.desc",
    limit: payload.limit + 1,
    offset: payload.offset,
  });
  const path = `/rest/v1/account_dashboard_actions?${filters.join("&")}${filters.length ? "&" : ""}${query}`;

  try {
    const rows = await supabaseJson(path, { method: "GET" }, deps);
    const list = Array.isArray(rows) ? rows : [];
    const visibleRows = list.slice(0, payload.limit);
    const nextOffset = list.length > payload.limit ? payload.offset + payload.limit : null;
    return jsonResponse(200, {
      ok: true,
      request_id: rid,
      actions: visibleRows.map(safeActionRow),
      next_offset: nextOffset,
    }, headers);
  } catch {
    return jsonResponse(500, { ok: false, error: "dashboard_actions_list_failed", request_id: rid }, headers);
  }
}

export async function handleRequest(req: Request, deps: Dependencies = {}): Promise<Response> {
  const headers = corsHeaders(req);
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers });
  if (req.method !== "POST") return jsonResponse(405, { ok: false, error: "method_not_allowed" }, headers);

  const auth = await assertAuthenticated(req, deps);
  if (!auth.ok) return auth.response;

  let rawPayload: Record<string, unknown>;
  try {
    rawPayload = await req.json();
  } catch {
    return jsonResponse(400, { ok: false, error: "invalid_json" }, headers);
  }
  if (!rawPayload || typeof rawPayload !== "object" || Array.isArray(rawPayload)) {
    return jsonResponse(400, { ok: false, error: "payload_must_be_object" }, headers);
  }

  const payloadResult = validatePayload(rawPayload);
  if (!payloadResult.ok) {
    return jsonResponse(payloadResult.status || 400, { ok: false, error: payloadResult.error }, headers);
  }

  try {
    if (payloadResult.payload.action === "count") return await handleCount(req, payloadResult.payload, auth, deps);
    return await handleList(req, payloadResult.payload, auth, deps);
  } catch {
    logEvent(deps, "dashboard_actions_unhandled_error", {
      request_id: requestId(req, deps),
      action: payloadResult.payload.action,
      account_id: payloadResult.payload.accountId,
      error: "internal_error",
    });
    return jsonResponse(500, { ok: false, error: "internal_error" }, headers);
  }
}

if (import.meta.main) {
  Deno.serve((req) => handleRequest(req));
}
