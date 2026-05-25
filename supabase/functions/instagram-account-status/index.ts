// deno-lint-ignore-file no-explicit-any
/**
 * Internal Instagram account status publisher.
 *
 * Receives login/provisioning results from trusted internal producers and
 * delegates all writes to public.update_client_instagram_account_status.
 * It never reads Vault, accepts passwords, or exposes secret references.
 */

type AccountStatusAction = "update_status";
type ActorType = "internal" | "worker" | "provisioner";
type Fetcher = (input: string | URL | Request, init?: RequestInit) => Promise<Response>;
type Dependencies = {
  fetch?: Fetcher;
  log?: (event: string, payload: Record<string, unknown>) => void;
  requestId?: () => string;
};
type ParsedPayload = {
  action: AccountStatusAction;
  accountId: string;
  loginStatus: string | null;
  provisioningStatus: string | null;
  onboardingStatus: string | null;
  reauthRequired: boolean | null;
  reauthReason: string | null;
  reason: string | null;
  externalRequestId: string | null;
  metadata: Record<string, unknown>;
  actorType: ActorType;
};

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SAFE_REQUEST_ID_RE = /^[a-zA-Z0-9._:-]{1,120}$/;
const FORBIDDEN_TOP_LEVEL_FIELDS = new Set([
  "password",
  "secret",
  "secret_ref",
  "secret_provider",
  "token",
  "cookie",
  "raw_secret",
  "webhook_url",
  "webhook",
  "service_role",
  "vault_payload",
  "vault",
  "adb_serial",
  "device_udid",
  "xml",
  "screenshot",
  "authorization",
  "bearer",
]);
const FORBIDDEN_METADATA_FIELDS = new Set([
  "password",
  "secret",
  "secret_ref",
  "raw_secret",
  "token",
  "cookie",
  "webhook",
  "webhook_url",
  "vault",
  "vault_payload",
  "service_role",
  "authorization",
  "bearer",
  "adb_serial",
  "device_udid",
  "xml",
  "screenshot",
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

function isUuid(value: unknown): value is string {
  return typeof value === "string" && UUID_RE.test(value.trim());
}

function optionalString(value: unknown): string | null {
  if (value == null) return null;
  const raw = String(value).trim();
  return raw ? raw : null;
}

function actorTypeFromMetadata(metadata: Record<string, unknown>): ActorType {
  const source = optionalString(metadata.source)?.toLowerCase();
  if (source === "provisioner") return "provisioner";
  if (source === "worker") return "worker";
  return "internal";
}

export function validatePayload(payload: Record<string, unknown>): { ok: true; payload: ParsedPayload } | {
  ok: false;
  error: string;
  status?: number;
} {
  const forbidden = validateForbiddenFields(payload);
  if (forbidden) return { ok: false, error: `field_forbidden:${forbidden}`, status: 400 };

  const action = optionalString(payload.action);
  if (action !== "update_status") return { ok: false, error: "invalid_action", status: 400 };

  if (!isUuid(payload.account_id)) return { ok: false, error: "account_id_invalid", status: 400 };
  const accountId = String(payload.account_id).trim();

  const loginStatus = optionalString(payload.login_status);
  const provisioningStatus = optionalString(payload.provisioning_status);
  const onboardingStatus = optionalString(payload.onboarding_status);
  const hasStatusField = loginStatus != null ||
    provisioningStatus != null ||
    onboardingStatus != null ||
    payload.reauth_required != null;
  if (!hasStatusField) return { ok: false, error: "no_status_fields", status: 400 };

  if (payload.reauth_required != null && typeof payload.reauth_required !== "boolean") {
    return { ok: false, error: "reauth_required_invalid", status: 400 };
  }

  let metadata: Record<string, unknown> = {};
  if (payload.metadata != null) {
    if (typeof payload.metadata !== "object" || Array.isArray(payload.metadata)) {
      return { ok: false, error: "metadata_must_be_object", status: 400 };
    }
    metadata = payload.metadata as Record<string, unknown>;
  }

  const reason = optionalString(payload.reason);
  if (reason && reason.length > 500) return { ok: false, error: "reason_too_long", status: 400 };

  const externalRequestId = optionalString(payload.external_request_id);
  if (externalRequestId && !SAFE_REQUEST_ID_RE.test(externalRequestId)) {
    return { ok: false, error: "external_request_id_invalid", status: 400 };
  }

  return {
    ok: true,
    payload: {
      action,
      accountId,
      loginStatus,
      provisioningStatus,
      onboardingStatus,
      reauthRequired: typeof payload.reauth_required === "boolean" ? payload.reauth_required : null,
      reauthReason: optionalString(payload.reauth_reason),
      reason,
      externalRequestId,
      metadata,
      actorType: actorTypeFromMetadata(metadata),
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
  const allowed = (Deno.env.get("INSTAGRAM_ACCOUNT_STATUS_ALLOWED_ORIGINS") || "")
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

async function assertInternalAuthenticated(req: Request): Promise<{ ok: true } | { ok: false; response: Response }> {
  const expected = (Deno.env.get("INSTAGRAM_ACCOUNT_STATUS_INTERNAL_API_TOKEN") || "").trim();
  const auth = req.headers.get("authorization") || "";
  const got = auth.toLowerCase().startsWith("bearer ") ? auth.slice(7).trim() : "";
  const headers = corsHeaders(req);
  if (!got || !expected || got !== expected) {
    return { ok: false, response: jsonResponse(401, { ok: false, error: "unauthorized" }, headers) };
  }
  return { ok: true };
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

function rpcError(body: unknown): { status: number; error: string } {
  const message = typeof (body as Record<string, unknown> | null)?.message === "string"
    ? String((body as Record<string, unknown>).message)
    : "";
  const code = typeof (body as Record<string, unknown> | null)?.code === "string"
    ? String((body as Record<string, unknown>).code)
    : "";

  if (code === "P0002" || message.includes("client_instagram_account_not_found")) {
    return { status: 404, error: "account_not_found" };
  }
  if (
    message.includes("client_instagram_accounts_login_status_check") ||
    message.includes("client_instagram_accounts_provisioning_status_check") ||
    message.includes("client_instagram_accounts_onboarding_status_check") ||
    message.includes("invalid input value") ||
    message.includes("violates check constraint")
  ) {
    return { status: 400, error: "invalid_status" };
  }
  if (message.includes("metadata contains a forbidden key")) {
    return { status: 400, error: "field_forbidden:metadata" };
  }
  if (message.includes("metadata must be a json object")) {
    return { status: 400, error: "metadata_must_be_object" };
  }
  if (message.includes("reason too long")) {
    return { status: 400, error: "reason_too_long" };
  }
  return { status: 500, error: "status_update_failed" };
}

function safeActionList(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) return [];
  return value.map((row) => {
    const input = (row && typeof row === "object") ? row as Record<string, unknown> : {};
    return {
      id: typeof input.id === "string" ? input.id : null,
      action_type: typeof input.action_type === "string" ? input.action_type : null,
      status: typeof input.status === "string" ? input.status : null,
    };
  });
}

function safeRpcResponse(body: Record<string, unknown>, requestIdValue: string): Record<string, unknown> {
  return {
    ok: body.ok === true,
    request_id: requestIdValue,
    account_id: typeof body.account_id === "string" ? body.account_id : null,
    login_status: typeof body.login_status === "string" ? body.login_status : null,
    provisioning_status: typeof body.provisioning_status === "string" ? body.provisioning_status : null,
    onboarding_status: typeof body.onboarding_status === "string" ? body.onboarding_status : null,
    credentials_configured: body.credentials_configured === true,
    reauth_required: body.reauth_required === true,
    reauth_reason: typeof body.reauth_reason === "string" ? body.reauth_reason : null,
    actions_upserted: safeActionList(body.actions_upserted),
    actions_resolved: safeActionList(body.actions_resolved),
  };
}

async function updateStatus(payload: ParsedPayload, rid: string, deps: Dependencies) {
  const metadata = {
    ...payload.metadata,
    source: optionalString(payload.metadata.source) || "instagram_account_status",
    edge_function: "instagram-account-status",
    request_id: rid,
  };

  const res = await supabaseFetch("/rest/v1/rpc/update_client_instagram_account_status", {
    method: "POST",
    body: JSON.stringify({
      p_account_id: payload.accountId,
      p_login_status: payload.loginStatus,
      p_provisioning_status: payload.provisioningStatus,
      p_onboarding_status: payload.onboardingStatus,
      p_reauth_required: payload.reauthRequired,
      p_reauth_reason: payload.reauthReason,
      p_actor_type: payload.actorType,
      p_reason: payload.reason,
      p_external_request_id: payload.externalRequestId,
      p_metadata: metadata,
    }),
  }, deps);
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const mapped = rpcError(body);
    return { ok: false as const, status: mapped.status, error: mapped.error };
  }
  return { ok: true as const, body: safeRpcResponse(body || {}, rid) };
}

export async function handleRequest(req: Request, deps: Dependencies = {}): Promise<Response> {
  const headers = corsHeaders(req);
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers });
  if (req.method !== "POST") return jsonResponse(405, { ok: false, error: "method_not_allowed" }, headers);

  const auth = await assertInternalAuthenticated(req);
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

  const payload = payloadResult.payload;
  const rid = requestId(req, deps);
  try {
    const result = await updateStatus(payload, rid, deps);
    if (!result.ok) {
      logEvent(deps, "instagram_account_status_update_failed", {
        request_id: rid,
        action: payload.action,
        account_id: payload.accountId,
        actor_type: payload.actorType,
        error: result.error,
        status: result.status,
      });
      return jsonResponse(result.status, { ok: false, error: result.error, request_id: rid }, headers);
    }
    logEvent(deps, "instagram_account_status_update_succeeded", {
      request_id: rid,
      action: payload.action,
      account_id: payload.accountId,
      actor_type: payload.actorType,
      status: 200,
    });
    return jsonResponse(200, result.body, headers);
  } catch {
    logEvent(deps, "instagram_account_status_unhandled_error", {
      request_id: rid,
      action: payload.action,
      account_id: payload.accountId,
      actor_type: payload.actorType,
      error: "status_update_failed",
      status: 500,
    });
    return jsonResponse(500, { ok: false, error: "status_update_failed", request_id: rid }, headers);
  }
}

if (import.meta.main) {
  Deno.serve((req) => handleRequest(req));
}
