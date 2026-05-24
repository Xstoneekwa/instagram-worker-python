// deno-lint-ignore-file no-explicit-any
/**
 * Secure Outreach enqueue API.
 *
 * This function is the server-side boundary for dashboard/n8n/import producers.
 * It validates the request, then calls public.enqueue_outreach_dm_job. It never
 * writes directly to ig_dm_jobs and never calls the sender.
 */

type Source = "dashboard" | "n8n" | "manual" | "campaign";
type EnqueueResult = "created" | "duplicate_existing";
type ProducerAuth =
  | { ok: true; mode: "internal"; authUserId: null }
  | { ok: true; mode: "client"; authUserId: string }
  | { ok: false; response: Response };

const SOURCE_ALLOWLIST = new Set<Source>(["dashboard", "n8n", "manual", "campaign"]);
const FORBIDDEN_TOP_LEVEL_FIELDS = new Set([
  "dm_type",
  "status",
  "reserved_by",
  "reserved_at",
  "started_at",
  "finished_at",
  "sent_at",
  "attempts",
  "idempotency_key",
]);
const DANGEROUS_METADATA_FIELDS = new Set([
  "handoff",
  "status",
  "reserved_by",
  "reserved_at",
  "attempts",
  "idempotency_key",
  "dm_type",
]);
const SAFE_METADATA_FIELDS = new Set([
  "external_request_id",
  "import_id",
  "created_by",
  "created_for",
  "source_context",
  "campaign_name",
  "note",
]);
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const USERNAME_RE = /^[a-z0-9._]{1,30}$/;

export function normalizeUsername(value: unknown): string {
  return String(value ?? "").trim().replace(/^@+/, "").toLowerCase();
}

export function validateUsername(value: unknown): { ok: true; username: string } | {
  ok: false;
  error: string;
} {
  const username = normalizeUsername(value);
  if (!username) return { ok: false, error: "invalid_username_empty" };
  if (!USERNAME_RE.test(username)) return { ok: false, error: "invalid_username" };
  if (username.includes("..")) return { ok: false, error: "invalid_username" };
  return { ok: true, username };
}

export function validateSource(value: unknown): { ok: true; source: Source } | {
  ok: false;
  error: string;
} {
  const source = String(value || "manual").trim().toLowerCase();
  if (source === "import_csv") {
    return { ok: false, error: "source_import_csv_not_supported_use_campaign" };
  }
  if (!SOURCE_ALLOWLIST.has(source as Source)) {
    return { ok: false, error: "source_not_allowed" };
  }
  return { ok: true, source: source as Source };
}

export function validateForbiddenTopLevelFields(payload: Record<string, unknown>): string | null {
  for (const key of Object.keys(payload || {})) {
    if (FORBIDDEN_TOP_LEVEL_FIELDS.has(key)) return key;
  }
  return null;
}

export function sanitizeMetadata(value: unknown): { ok: true; metadata: Record<string, string> } | {
  ok: false;
  error: string;
} {
  if (value == null) return { ok: true, metadata: {} };
  if (typeof value !== "object" || Array.isArray(value)) {
    return { ok: false, error: "metadata_must_be_object" };
  }
  const input = value as Record<string, unknown>;
  const metadata: Record<string, string> = {};
  for (const [key, raw] of Object.entries(input)) {
    if (DANGEROUS_METADATA_FIELDS.has(key)) {
      if (key === "handoff" && String(raw || "").trim().toLowerCase() === "unfollow") {
        return { ok: false, error: "metadata_handoff_unfollow_forbidden" };
      }
      return { ok: false, error: `metadata_field_forbidden:${key}` };
    }
    if (!SAFE_METADATA_FIELDS.has(key)) continue;
    if (raw == null) continue;
    const str = String(raw).trim();
    if (!str) continue;
    metadata[key] = str.slice(0, 500);
  }
  return { ok: true, metadata };
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
  const allowed = (Deno.env.get("OUTREACH_ENQUEUE_ALLOWED_ORIGINS") || "")
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

async function verifyClientJwt(token: string): Promise<string | null> {
  const url = requireEnv("SUPABASE_URL").replace(/\/+$/, "");
  const key = requireEnv("SUPABASE_SERVICE_ROLE_KEY");
  const res = await fetch(`${url}/auth/v1/user`, {
    headers: {
      "apikey": key,
      "authorization": `Bearer ${token}`,
    },
  });
  if (!res.ok) return null;
  const user = await res.json();
  return typeof user?.id === "string" && isUuid(user.id) ? user.id : null;
}

async function assertProducerAuthenticated(req: Request): Promise<ProducerAuth> {
  const expected = (Deno.env.get("OUTREACH_ENQUEUE_INTERNAL_API_TOKEN") || "").trim();
  const auth = req.headers.get("authorization") || "";
  const got = auth.toLowerCase().startsWith("bearer ") ? auth.slice(7).trim() : "";
  const headers = corsHeaders(req);
  if (!got) {
    return {
      ok: false,
      response: jsonResponse(401, { ok: false, error: "unauthorized" }, headers),
    };
  }
  if (expected && got === expected) {
    return { ok: true, mode: "internal", authUserId: null };
  }
  const authUserId = await verifyClientJwt(got);
  if (!authUserId) {
    return {
      ok: false,
      response: jsonResponse(401, { ok: false, error: "unauthorized" }, headers),
    };
  }
  return { ok: true, mode: "client", authUserId };
}

function parsePositiveIntEnv(name: string, fallback: number): number {
  const raw = (Deno.env.get(name) || "").trim();
  if (!raw) return fallback;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function isUuid(value: unknown): value is string {
  return typeof value === "string" && UUID_RE.test(value.trim());
}

function normalizeOptionalUuid(value: unknown, field: string): { ok: true; value: string | null } | {
  ok: false;
  error: string;
} {
  if (value == null || String(value).trim() === "") return { ok: true, value: null };
  const str = String(value).trim();
  if (!isUuid(str)) return { ok: false, error: `${field}_invalid` };
  return { ok: true, value: str };
}

async function supabaseFetch(path: string, init: RequestInit = {}) {
  const url = requireEnv("SUPABASE_URL").replace(/\/+$/, "");
  const key = requireEnv("SUPABASE_SERVICE_ROLE_KEY");
  const headers = new Headers(init.headers || {});
  headers.set("apikey", key);
  headers.set("authorization", `Bearer ${key}`);
  if (!headers.has("content-type") && init.body) headers.set("content-type", "application/json");
  return await fetch(`${url}${path}`, { ...init, headers });
}

async function supabaseJson(path: string, init: RequestInit = {}): Promise<any> {
  const res = await supabaseFetch(path, init);
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) {
    throw new Error(`supabase_error:${res.status}:${JSON.stringify(body)}`);
  }
  return body;
}

async function getSettings(accountId: string): Promise<Record<string, any> | null> {
  const rows = await supabaseJson(
    `/rest/v1/ig_account_dm_settings?account_id=eq.${encodeURIComponent(accountId)}&select=*`,
  );
  return Array.isArray(rows) && rows.length > 0 ? rows[0] : null;
}

async function getTemplate(templateId: string): Promise<Record<string, any> | null> {
  const rows = await supabaseJson(
    `/rest/v1/ig_dm_templates?id=eq.${encodeURIComponent(templateId)}&select=id,account_id,template_type,active,body`,
  );
  return Array.isArray(rows) && rows.length > 0 ? rows[0] : null;
}

async function pendingQueueCount(accountId: string): Promise<number> {
  const res = await supabaseFetch(
    `/rest/v1/ig_dm_jobs?account_id=eq.${encodeURIComponent(accountId)}&dm_type=eq.outreach&status=in.(pending,reserved,running)&select=id`,
    {
      method: "GET",
      headers: {
        prefer: "count=exact",
        range: "0-0",
      },
    },
  );
  if (!res.ok) return 0;
  const range = res.headers.get("content-range") || "";
  const total = Number.parseInt(range.split("/")[1] || "0", 10);
  return Number.isFinite(total) ? total : 0;
}

async function findExistingJob(
  accountId: string,
  username: string,
  campaignId: string | null,
): Promise<Record<string, any> | null> {
  const campaignFilter = campaignId
    ? `campaign_id=eq.${encodeURIComponent(campaignId)}`
    : "campaign_id=is.null";
  const rows = await supabaseJson(
    `/rest/v1/ig_dm_jobs?account_id=eq.${encodeURIComponent(accountId)}&dm_type=eq.outreach&recipient_username_normalized=eq.${encodeURIComponent(username)}&${campaignFilter}&select=*&limit=1`,
  );
  return Array.isArray(rows) && rows.length > 0 ? rows[0] : null;
}

async function ensureMessageResolvable(
  payload: Record<string, unknown>,
  settings: Record<string, any>,
): Promise<{ ok: true; messageBody: string | null; templateId: string | null } | {
  ok: false;
  error: string;
}> {
  const rawBody = payload.message_body == null ? "" : String(payload.message_body).trim();
  if (payload.message_body != null && !rawBody) return { ok: false, error: "message_body_empty" };

  const templateIdResult = normalizeOptionalUuid(payload.template_id, "template_id");
  if (!templateIdResult.ok) return templateIdResult;
  const templateId = templateIdResult.value;

  if (templateId) {
    const template = await getTemplate(templateId);
    if (!template) return { ok: false, error: "template_not_found" };
    if (String(template.account_id) !== String(payload.account_id)) {
      return { ok: false, error: "template_account_mismatch" };
    }
    if (String(template.template_type) !== "outreach") {
      return { ok: false, error: "template_not_outreach" };
    }
    if (template.active !== true) return { ok: false, error: "template_inactive" };
    if (!rawBody && !String(template.body || "").trim()) {
      return { ok: false, error: "template_body_empty" };
    }
  }

  if (!rawBody && !templateId) {
    const defaultId = String(settings.default_outreach_template_id || "").trim();
    if (!defaultId) return { ok: false, error: "missing_message_template_or_default" };
    const template = await getTemplate(defaultId);
    if (!template || template.active !== true || String(template.account_id) !== String(payload.account_id)) {
      return { ok: false, error: "default_outreach_template_invalid" };
    }
    if (!String(template.body || "").trim()) return { ok: false, error: "default_template_body_empty" };
  }

  return { ok: true, messageBody: rawBody || null, templateId };
}

export function parseAllowedAccountIds(raw: string): string[] {
  return raw
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
}

export function allowlistFallbackEnabled(): boolean {
  return ["1", "true", "yes", "on"].includes(
    (Deno.env.get("OUTREACH_ENQUEUE_ALLOWLIST_FALLBACK_ENABLED") || "").trim().toLowerCase(),
  );
}

export function accountAllowedByEnv(accountId: string): boolean {
  const allowed = parseAllowedAccountIds(Deno.env.get("OUTREACH_ENQUEUE_ALLOWED_ACCOUNT_IDS") || "");
  return allowed.length > 0 && allowed.includes(accountId);
}

export function accessDecision(
  hasDbEntitlement: boolean,
  accountId: string,
  options: {
    dbCheckFailed?: boolean;
    allowlistFallback?: boolean;
    allowlistAllowed?: boolean;
  } = {},
): { ok: true; mode: "db" | "allowlist_fallback" } | { ok: false; error: string; status: number } {
  const {
    dbCheckFailed = false,
    allowlistFallback = false,
    allowlistAllowed = false,
  } = options;
  if (hasDbEntitlement) return { ok: true, mode: "db" };
  if (allowlistFallback && allowlistAllowed) return { ok: true, mode: "allowlist_fallback" };
  if (dbCheckFailed) return { ok: false, error: "account_ownership_check_failed", status: 503 };
  if (!isUuid(accountId)) return { ok: false, error: "account_id_invalid", status: 400 };
  return { ok: false, error: "account_outreach_entitlement_required", status: 403 };
}

async function rpcBoolean(path: string, payload: Record<string, unknown>): Promise<boolean> {
  const result = await supabaseJson(path, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return result === true;
}

async function hasClientAccountAccess(authUserId: string, accountId: string): Promise<boolean> {
  return await rpcBoolean("/rest/v1/rpc/client_can_enqueue_outreach", {
    p_auth_user_id: authUserId,
    p_account_id: accountId,
  });
}

async function hasInternalAccountAccess(accountId: string): Promise<boolean> {
  return await rpcBoolean("/rest/v1/rpc/client_account_has_outreach_entitlement", {
    p_account_id: accountId,
  });
}

async function validateOwnershipGuard(
  accountId: string,
  auth: Extract<ProducerAuth, { ok: true }>,
): Promise<{ ok: true; mode: "db" | "allowlist_fallback" } | { ok: false; error: string; status: number }> {
  const fallback = allowlistFallbackEnabled();
  const fallbackAllowed = accountAllowedByEnv(accountId);
  try {
    const hasAccess = auth.mode === "client"
      ? await hasClientAccountAccess(auth.authUserId, accountId)
      : await hasInternalAccountAccess(accountId);
    return accessDecision(hasAccess, accountId, {
      allowlistFallback: auth.mode === "internal" && fallback,
      allowlistAllowed: fallbackAllowed,
    });
  } catch {
    return accessDecision(false, accountId, {
      dbCheckFailed: true,
      allowlistFallback: auth.mode === "internal" && fallback,
      allowlistAllowed: fallbackAllowed,
    });
  }
}

async function validateCommon(
  payload: Record<string, unknown>,
  auth: Extract<ProducerAuth, { ok: true }>,
): Promise<
  | {
    ok: true;
    accountId: string;
    source: Source;
    campaignId: string | null;
    templateId: string | null;
    messageBody: string | null;
    priority: number;
    metadata: Record<string, string>;
    settings: Record<string, any>;
  }
  | { ok: false; error: string; status?: number }
> {
  const forbidden = validateForbiddenTopLevelFields(payload);
  if (forbidden) return { ok: false, error: `field_forbidden:${forbidden}`, status: 400 };

  if (!isUuid(payload.account_id)) return { ok: false, error: "account_id_invalid", status: 400 };
  const accountId = String(payload.account_id).trim();

  const ownership = await validateOwnershipGuard(accountId, auth);
  if (!ownership.ok) return { ok: false, error: ownership.error, status: ownership.status };

  const sourceResult = validateSource(payload.source);
  if (!sourceResult.ok) return { ok: false, error: sourceResult.error, status: 400 };

  const campaignResult = normalizeOptionalUuid(payload.campaign_id, "campaign_id");
  if (!campaignResult.ok) return { ok: false, error: campaignResult.error, status: 400 };

  const metadataResult = sanitizeMetadata(payload.metadata);
  if (!metadataResult.ok) return { ok: false, error: metadataResult.error, status: 400 };

  const settings = await getSettings(accountId);
  if (!settings) return { ok: false, error: "account_dm_settings_not_found", status: 404 };
  if (settings.outreach_enabled !== true) return { ok: false, error: "outreach_disabled", status: 403 };

  const message = await ensureMessageResolvable({ ...payload, account_id: accountId }, settings);
  if (!message.ok) return { ok: false, error: message.error, status: 400 };

  const priorityRaw = payload.priority == null ? 0 : Number(payload.priority);
  if (!Number.isFinite(priorityRaw)) return { ok: false, error: "priority_invalid", status: 400 };
  const priority = Math.trunc(priorityRaw);

  return {
    ok: true,
    accountId,
    source: sourceResult.source,
    campaignId: campaignResult.value,
    templateId: message.templateId,
    messageBody: message.messageBody,
    priority,
    metadata: metadataResult.metadata,
    settings,
  };
}

async function enqueueOne(
  payload: Record<string, unknown>,
  base?: Awaited<ReturnType<typeof validateCommon>> & { ok: true },
  auth?: Extract<ProducerAuth, { ok: true }>,
): Promise<Record<string, unknown>> {
  const usernameResult = validateUsername(payload.recipient_username);
  if (!usernameResult.ok) {
    return {
      recipient_username: String(payload.recipient_username ?? ""),
      ok: false,
      error: usernameResult.error,
    };
  }

  if (!base && !auth) {
    return {
      recipient_username: usernameResult.username,
      ok: false,
      error: "auth_context_required",
    };
  }
  const common = base ?? await validateCommon(payload, auth as Extract<ProducerAuth, { ok: true }>);
  if (!common.ok) {
    return {
      recipient_username: usernameResult.username,
      ok: false,
      error: common.error,
    };
  }

  const existing = await findExistingJob(common.accountId, usernameResult.username, common.campaignId);
  const rpcPayload = {
    p_account_id: common.accountId,
    p_recipient_username: usernameResult.username,
    p_message_body: common.messageBody,
    p_template_id: common.templateId,
    p_source: common.source,
    p_campaign_id: common.campaignId,
    p_priority: common.priority,
    p_metadata: common.metadata,
  };

  const row = await supabaseJson("/rest/v1/rpc/enqueue_outreach_dm_job", {
    method: "POST",
    body: JSON.stringify(rpcPayload),
  });
  if (!row || !row.id) {
    return {
      recipient_username: usernameResult.username,
      ok: false,
      error: "enqueue_returned_null",
    };
  }

  const result: EnqueueResult = existing ? "duplicate_existing" : "created";
  return {
    ok: true,
    result,
    job_id: row.id,
    status: row.status,
    account_id: row.account_id,
    recipient_username: row.recipient_username,
    recipient_username_normalized: row.recipient_username_normalized,
    campaign_id: row.campaign_id,
    source: row.source,
    message_frozen: Boolean(String(row.message_body || "").trim()),
  };
}

function requestId(req: Request): string {
  return req.headers.get("x-request-id") || crypto.randomUUID();
}

function logEvent(event: string, payload: Record<string, unknown>) {
  console.log(JSON.stringify({ ts: new Date().toISOString(), event, ...payload }));
}

async function handleSingle(
  req: Request,
  payload: Record<string, unknown>,
  auth: Extract<ProducerAuth, { ok: true }>,
) {
  const headers = corsHeaders(req);
  const rid = requestId(req);
  const common = await validateCommon(payload, auth);
  if (!common.ok) {
    logEvent("outreach_enqueue_rejected", {
      request_id: rid,
      account_id: payload.account_id || null,
      source: payload.source || null,
      error: common.error,
    });
    return jsonResponse(common.status || 400, { ok: false, error: common.error, request_id: rid }, headers);
  }

  const maxPending = parsePositiveIntEnv("OUTREACH_ENQUEUE_MAX_PENDING_PER_ACCOUNT", 500);
  const pending = await pendingQueueCount(common.accountId);
  if (pending >= maxPending) {
    return jsonResponse(429, {
      ok: false,
      error: "pending_queue_limit_exceeded",
      pending_count: pending,
      max_pending: maxPending,
      request_id: rid,
    }, headers);
  }

  const result = await enqueueOne(payload, common);
  logEvent("outreach_enqueue_completed", {
    request_id: rid,
    account_id: common.accountId,
    source: common.source,
    campaign_id: common.campaignId,
    external_request_id: common.metadata.external_request_id || null,
    import_id: common.metadata.import_id || null,
    result: result.result || null,
    ok: result.ok,
    error: result.error || null,
  });
  return jsonResponse(result.ok ? 200 : 400, { ...result, request_id: rid }, headers);
}

async function handleBulk(
  req: Request,
  payload: Record<string, unknown>,
  auth: Extract<ProducerAuth, { ok: true }>,
) {
  const headers = corsHeaders(req);
  const rid = requestId(req);
  const recipients = Array.isArray(payload.recipients) ? payload.recipients : null;
  if (!recipients) {
    return jsonResponse(400, { ok: false, error: "recipients_must_be_array", request_id: rid }, headers);
  }
  const maxBatch = parsePositiveIntEnv("OUTREACH_ENQUEUE_MAX_BATCH_SIZE", 100);
  if (recipients.length <= 0) {
    return jsonResponse(400, { ok: false, error: "recipients_empty", request_id: rid }, headers);
  }
  if (recipients.length > maxBatch) {
    return jsonResponse(413, {
      ok: false,
      error: "batch_too_large",
      max_batch_size: maxBatch,
      request_id: rid,
    }, headers);
  }

  const common = await validateCommon(payload, auth);
  if (!common.ok) {
    return jsonResponse(common.status || 400, { ok: false, error: common.error, request_id: rid }, headers);
  }

  const maxPending = parsePositiveIntEnv("OUTREACH_ENQUEUE_MAX_PENDING_PER_ACCOUNT", 500);
  const pending = await pendingQueueCount(common.accountId);
  if (pending + recipients.length > maxPending) {
    return jsonResponse(429, {
      ok: false,
      error: "pending_queue_limit_exceeded",
      pending_count: pending,
      requested: recipients.length,
      max_pending: maxPending,
      request_id: rid,
    }, headers);
  }

  const results: Record<string, unknown>[] = [];
  for (const recipient of recipients) {
    results.push(await enqueueOne({ ...payload, recipient_username: recipient }, common));
  }

  const accepted = results.filter((r) => r.ok === true && r.result === "created").length;
  const duplicates = results.filter((r) => r.ok === true && r.result === "duplicate_existing").length;
  const rejected = results.filter((r) => r.ok !== true).length;
  logEvent("outreach_bulk_enqueue_completed", {
    request_id: rid,
    account_id: common.accountId,
    source: common.source,
    campaign_id: common.campaignId,
    count: recipients.length,
    accepted,
    duplicates,
    rejected,
    external_request_id: common.metadata.external_request_id || null,
    import_id: common.metadata.import_id || null,
  });

  return jsonResponse(200, {
    ok: rejected === 0,
    request_id: rid,
    summary: { accepted, duplicates, rejected },
    results,
  }, headers);
}

async function handleRequest(req: Request): Promise<Response> {
  const headers = corsHeaders(req);
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers });
  if (req.method !== "POST") return jsonResponse(405, { ok: false, error: "method_not_allowed" }, headers);

  const auth = await assertProducerAuthenticated(req);
  if (!auth.ok) return auth.response;

  let payload: Record<string, unknown>;
  try {
    payload = await req.json();
  } catch {
    return jsonResponse(400, { ok: false, error: "invalid_json" }, headers);
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return jsonResponse(400, { ok: false, error: "payload_must_be_object" }, headers);
  }

  try {
    const path = new URL(req.url).pathname.replace(/\/+$/, "");
    if (path.endsWith("/outreach/enqueue") || path.endsWith("/enqueue")) {
      return await handleSingle(req, payload, auth);
    }
    if (path.endsWith("/outreach/bulk-enqueue") || path.endsWith("/bulk-enqueue")) {
      return await handleBulk(req, payload, auth);
    }
    return jsonResponse(404, { ok: false, error: "route_not_found" }, headers);
  } catch (error) {
    logEvent("outreach_enqueue_unhandled_error", {
      request_id: requestId(req),
      error: error instanceof Error ? error.message : String(error),
    });
    return jsonResponse(500, { ok: false, error: "internal_error" }, headers);
  }
}

if (import.meta.main) {
  Deno.serve(handleRequest);
}
