// deno-lint-ignore-file no-explicit-any
/**
 * Internal admin dashboard API over Dashboard Foundation 1A RPCs and small
 * audited admin-only mutations.
 *
 * This function is internal/admin only in DF-1B. It accepts only the
 * ADMIN_DASHBOARD_INTERNAL_API_TOKEN bearer token and exposes no client JWT
 * access, credentials, runner dispatch, login, follow/DM/unfollow, or device
 * automation.
 */

import {
  handleLiveViewAction,
  LiveViewActionError,
  validateLiveViewPayload,
} from "./live_view.ts";

type AdminDashboardAction =
  | "health"
  | "manage_overview"
  | "radar_overview"
  | "devices_overview"
  | "add_physical_phone"
  | "delete_physical_phone_preflight"
  | "delete_physical_phone";
type OverviewDashboardAction = Exclude<
  AdminDashboardAction,
  "add_physical_phone" | "delete_physical_phone_preflight" | "delete_physical_phone"
>;
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
type RestClient = {
  select: (
    tableName: string,
    query: Record<string, string>,
  ) => Promise<Response>;
  insert: (
    tableName: string,
    body: Record<string, unknown>,
  ) => Promise<Response>;
  update: (
    tableName: string,
    query: Record<string, string>,
    body: Record<string, unknown>,
  ) => Promise<Response>;
  delete: (
    tableName: string,
    query: Record<string, string>,
  ) => Promise<Response>;
};
type OverviewPayload = {
  action: OverviewDashboardAction;
  limit: number;
  offset: number;
  search: string | null;
  status: string | null;
  health: string | null;
};
type AddPhysicalPhonePayload = {
  action: "add_physical_phone";
  displayName: string;
  adbSerial: string;
  model: string | null;
  product: string | null;
  device: string | null;
  pool: "full_cycle" | "outreach_only";
  maxClones: number;
  hubLabel: string | null;
  hubPort: string | null;
  hostLabel: string | null;
  packagesMode: "standard_instagram_4_packages";
  actorType: string | null;
  actorId: string | null;
};
type DeletePhysicalPhonePreflightPayload = {
  action: "delete_physical_phone_preflight";
  deviceId: string;
  actorType: string | null;
  actorId: string | null;
};
type DeletePhysicalPhonePayload = {
  action: "delete_physical_phone";
  deviceId: string;
  confirmationName: string;
  actorType: string | null;
  actorId: string | null;
};
type ParsedPayload =
  | OverviewPayload
  | AddPhysicalPhonePayload
  | DeletePhysicalPhonePreflightPayload
  | DeletePhysicalPhonePayload;
type PhoneDeviceRow = {
  id: string;
  name: string | null;
  adb_serial: string | null;
  hub_port?: string | null;
  metadata?: Record<string, unknown> | null;
};
type AppInstanceRow = {
  id: string;
  instance_type: string | null;
  instance_index: number;
  package_name: string | null;
  status: string | null;
  current_account_id: string | null;
  metadata?: Record<string, unknown> | null;
};
type AddPhysicalPhoneResult = {
  deviceId: string;
  adbSerial: string;
  displayName: string;
  appInstancesCreatedCount: number;
  appInstancesExistingCount: number;
  warnings: string[];
  audit: { attempted: boolean; published: boolean; reason: string | null };
};
type ErrorCode =
  | "unauthorized"
  | "validation_error"
  | "unsupported_action"
  | "conflict"
  | "rpc_failed"
  | "internal_error"
  | "assignment_not_found"
  | "device_unavailable"
  | "session_not_found"
  | "session_not_active"
  | "livekit_not_configured";

const MAX_LIMIT = 200;
const DEFAULT_LIMIT = 100;
const MAX_SEARCH_LENGTH = 200;
const MAX_FILTER_LENGTH = 80;
const MAX_PHONE_FIELD_LENGTH = 120;
const MAX_METADATA_FIELD_LENGTH = 160;
const ACTIONS = new Set([
  "health",
  "manage_overview",
  "radar_overview",
  "devices_overview",
  "add_physical_phone",
  "delete_physical_phone_preflight",
  "delete_physical_phone",
]);
const ACTIVE_ASSIGNMENT_STATUSES = new Set(["pending", "reserved", "active"]);
const ACTIVE_RUN_REQUEST_STATUSES = new Set([
  "queued",
  "claimed",
  "starting",
  "running",
]);
const ACTIVE_LIVE_VIEW_STATUSES = new Set([
  "pending",
  "starting",
  "active",
]);
const OCCUPIED_APP_INSTANCE_STATUSES = new Set(["occupied", "reserved"]);
const ADD_PHONE_POOLS = new Set(["full_cycle", "outreach_only"]);
const STANDARD_INSTAGRAM_APP_INSTANCES = [
  {
    instanceType: "primary_app",
    instanceIndex: 0,
    labelSuffix: "primary",
    packageName: "com.instagram.android",
  },
  {
    instanceType: "clone",
    instanceIndex: 1,
    labelSuffix: "clone 1",
    packageName: "com.instagram.androie",
  },
  {
    instanceType: "clone",
    instanceIndex: 2,
    labelSuffix: "clone 2",
    packageName: "com.instagram.androif",
  },
  {
    instanceType: "clone",
    instanceIndex: 3,
    labelSuffix: "clone 3",
    packageName: "com.instagram.androig",
  },
] as const;
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
const ADD_PHONE_FORBIDDEN_TOP_LEVEL_FIELDS = new Set([
  "password",
  "password_hash",
  "password_length",
  "password_confirm",
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
  "raw_xml",
  "xml",
  "screenshot",
  "screenshot_path",
  "raw_logs",
  "raw_metadata",
  "metadata",
  "metadata_safe",
  "credential",
  "credentials",
  "account_id",
  "current_account_id",
  "assignment_id",
  "clone_id",
  "app_instance_id",
  "run_id",
  "request_id",
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
    "access-control-allow-headers": "authorization, apikey, content-type, x-request-id",
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

/** Non-JWT internal secrets should use `apikey` per Supabase edge auth guidance. */
function internalApiToken(req: Request): string {
  const bearer = bearerToken(req);
  if (bearer) return bearer;
  return (req.headers.get("apikey") || req.headers.get("x-api-key") || "").trim();
}

function assertInternalAuthenticated(
  req: Request,
  headers: HeadersInit,
): { ok: true } | { ok: false; response: Response } {
  const expected = (Deno.env.get("ADMIN_DASHBOARD_INTERNAL_API_TOKEN") || "")
    .trim();
  const got = internalApiToken(req);
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

function requiredBoundedString(
  value: unknown,
  key: string,
  maxLength: number,
): { ok: true; value: string } | { ok: false; error: string } {
  if (typeof value !== "string") return { ok: false, error: `${key}_required` };
  const trimmed = value.trim();
  if (!trimmed) return { ok: false, error: `${key}_required` };
  if (trimmed.length > maxLength) {
    return { ok: false, error: `${key}_too_long` };
  }
  return { ok: true, value: trimmed };
}

function validateNoAddPhoneForbiddenFields(
  payload: Record<string, unknown>,
): string | null {
  for (const key of Object.keys(payload || {})) {
    const normalized = key.trim().toLowerCase();
    if (ADD_PHONE_FORBIDDEN_TOP_LEVEL_FIELDS.has(normalized)) return key;
  }
  return null;
}

function parseMaxClones(value: unknown): number {
  if (value == null || value === "") return 3;
  const raw = Number(value);
  if (!Number.isFinite(raw)) return 3;
  return Math.max(0, Math.min(20, Math.trunc(raw)));
}

function validateAddPhysicalPhonePayload(
  payload: Record<string, unknown>,
): { ok: true; payload: AddPhysicalPhonePayload } | {
  ok: false;
  error: string;
  code: ErrorCode;
  status: number;
} {
  const forbidden = validateNoAddPhoneForbiddenFields(payload);
  if (forbidden) {
    return {
      ok: false,
      error: `forbidden_field:${forbidden}`,
      code: "validation_error",
      status: 400,
    };
  }

  const displayName = requiredBoundedString(
    payload.display_name ?? payload.displayName,
    "display_name",
    MAX_PHONE_FIELD_LENGTH,
  );
  if (!displayName.ok) {
    return {
      ok: false,
      error: displayName.error,
      code: "validation_error",
      status: 400,
    };
  }

  const adbSerial = requiredBoundedString(
    payload.adb_serial ?? payload.adbSerial,
    "adb_serial",
    MAX_PHONE_FIELD_LENGTH,
  );
  if (!adbSerial.ok) {
    return {
      ok: false,
      error: adbSerial.error,
      code: "validation_error",
      status: 400,
    };
  }

  const model = optionalBoundedString(
    payload.model,
    "model",
    MAX_METADATA_FIELD_LENGTH,
  );
  if (!model.ok) {
    return { ok: false, error: model.error, code: "validation_error", status: 400 };
  }
  const product = optionalBoundedString(
    payload.product,
    "product",
    MAX_METADATA_FIELD_LENGTH,
  );
  if (!product.ok) {
    return {
      ok: false,
      error: product.error,
      code: "validation_error",
      status: 400,
    };
  }
  const device = optionalBoundedString(
    payload.device,
    "device",
    MAX_METADATA_FIELD_LENGTH,
  );
  if (!device.ok) {
    return { ok: false, error: device.error, code: "validation_error", status: 400 };
  }
  const hubLabel = optionalBoundedString(
    payload.hub_label ?? payload.hubLabel,
    "hub_label",
    MAX_METADATA_FIELD_LENGTH,
  );
  if (!hubLabel.ok) {
    return {
      ok: false,
      error: hubLabel.error,
      code: "validation_error",
      status: 400,
    };
  }
  const hubPort = optionalBoundedString(
    payload.hub_port ?? payload.hubPort,
    "hub_port",
    MAX_METADATA_FIELD_LENGTH,
  );
  if (!hubPort.ok) {
    return {
      ok: false,
      error: hubPort.error,
      code: "validation_error",
      status: 400,
    };
  }
  const hostLabel = optionalBoundedString(
    payload.host_label ?? payload.hostLabel,
    "host_label",
    MAX_METADATA_FIELD_LENGTH,
  );
  if (!hostLabel.ok) {
    return {
      ok: false,
      error: hostLabel.error,
      code: "validation_error",
      status: 400,
    };
  }

  const rawPool = typeof payload.pool === "string"
    ? payload.pool.trim()
    : "full_cycle";
  if (!ADD_PHONE_POOLS.has(rawPool)) {
    return {
      ok: false,
      error: "pool_invalid",
      code: "validation_error",
      status: 400,
    };
  }

  const maxClones = parseMaxClones(payload.max_clones ?? payload.maxClones);
  if (maxClones < 3) {
    return {
      ok: false,
      error: "max_clones_too_low_for_standard_packages",
      code: "validation_error",
      status: 400,
    };
  }

  const packagesMode = typeof payload.packages_mode === "string"
    ? payload.packages_mode.trim()
    : typeof payload.packagesMode === "string"
    ? payload.packagesMode.trim()
    : "standard_instagram_4_packages";
  if (packagesMode !== "standard_instagram_4_packages") {
    return {
      ok: false,
      error: "packages_mode_unsupported",
      code: "validation_error",
      status: 400,
    };
  }

  const actorType = optionalBoundedString(
    payload.actor_type ?? payload.actorType,
    "actor_type",
    MAX_FILTER_LENGTH,
  );
  if (!actorType.ok) {
    return {
      ok: false,
      error: actorType.error,
      code: "validation_error",
      status: 400,
    };
  }
  const actorId = optionalBoundedString(
    payload.actor_id ?? payload.actorId,
    "actor_id",
    MAX_METADATA_FIELD_LENGTH,
  );
  if (!actorId.ok) {
    return { ok: false, error: actorId.error, code: "validation_error", status: 400 };
  }

  return {
    ok: true,
    payload: {
      action: "add_physical_phone",
      displayName: displayName.value,
      adbSerial: adbSerial.value,
      model: model.value,
      product: product.value,
      device: device.value,
      pool: rawPool as "full_cycle" | "outreach_only",
      maxClones,
      hubLabel: hubLabel.value,
      hubPort: hubPort.value,
      hostLabel: hostLabel.value,
      packagesMode: "standard_instagram_4_packages",
      actorType: actorType.value,
      actorId: actorId.value,
    },
  };
}

function validateDeletePhysicalPhonePreflightPayload(
  payload: Record<string, unknown>,
): { ok: true; payload: DeletePhysicalPhonePreflightPayload } | {
  ok: false;
  error: string;
  code: ErrorCode;
  status: number;
} {
  const deviceId = requiredUuid(payload.device_id ?? payload.deviceId, "device_id");
  if (!deviceId.ok) {
    return { ok: false, error: deviceId.error, code: "validation_error", status: 400 };
  }
  const actorType = optionalBoundedString(
    payload.actor_type ?? payload.actorType,
    "actor_type",
    MAX_FILTER_LENGTH,
  );
  if (!actorType.ok) {
    return { ok: false, error: actorType.error, code: "validation_error", status: 400 };
  }
  const actorId = optionalBoundedString(
    payload.actor_id ?? payload.actorId,
    "actor_id",
    MAX_METADATA_FIELD_LENGTH,
  );
  if (!actorId.ok) {
    return { ok: false, error: actorId.error, code: "validation_error", status: 400 };
  }
  return {
    ok: true,
    payload: {
      action: "delete_physical_phone_preflight",
      deviceId: deviceId.value,
      actorType: actorType.value,
      actorId: actorId.value,
    },
  };
}

function validateDeletePhysicalPhonePayload(
  payload: Record<string, unknown>,
): { ok: true; payload: DeletePhysicalPhonePayload } | {
  ok: false;
  error: string;
  code: ErrorCode;
  status: number;
} {
  const deviceId = requiredUuid(payload.device_id ?? payload.deviceId, "device_id");
  if (!deviceId.ok) {
    return { ok: false, error: deviceId.error, code: "validation_error", status: 400 };
  }
  const confirmationName = requiredBoundedString(
    payload.confirmation_name ?? payload.confirmationName,
    "confirmation_name",
    MAX_PHONE_FIELD_LENGTH,
  );
  if (!confirmationName.ok) {
    const error = confirmationName.error === "confirmation_name_required"
      ? "device_delete_confirmation_required"
      : confirmationName.error;
    return { ok: false, error, code: "validation_error", status: 400 };
  }
  const actorType = optionalBoundedString(
    payload.actor_type ?? payload.actorType,
    "actor_type",
    MAX_FILTER_LENGTH,
  );
  if (!actorType.ok) {
    return { ok: false, error: actorType.error, code: "validation_error", status: 400 };
  }
  const actorId = optionalBoundedString(
    payload.actor_id ?? payload.actorId,
    "actor_id",
    MAX_METADATA_FIELD_LENGTH,
  );
  if (!actorId.ok) {
    return { ok: false, error: actorId.error, code: "validation_error", status: 400 };
  }
  return {
    ok: true,
    payload: {
      action: "delete_physical_phone",
      deviceId: deviceId.value,
      confirmationName: confirmationName.value,
      actorType: actorType.value,
      actorId: actorId.value,
    },
  };
}

function requiredUuid(
  value: unknown,
  fieldName: string,
): { ok: true; value: string } | { ok: false; error: string } {
  const normalized = typeof value === "string" ? value.trim() : "";
  if (!normalized) return { ok: false, error: `${fieldName}_required` };
  if (
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
      normalized,
    )
  ) {
    return { ok: false, error: `${fieldName}_invalid` };
  }
  return { ok: true, value: normalized };
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

  if (rawAction === "add_physical_phone") {
    return validateAddPhysicalPhonePayload(payload);
  }
  if (rawAction === "delete_physical_phone_preflight") {
    return validateDeletePhysicalPhonePreflightPayload(payload);
  }
  if (rawAction === "delete_physical_phone") {
    return validateDeletePhysicalPhonePayload(payload);
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
      action: rawAction as OverviewDashboardAction,
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

function createServiceRoleRestClient(deps: Dependencies = {}): RestClient {
  const url = requireEnv("SUPABASE_URL").replace(/\/+$/, "");
  const key = requireEnv("SUPABASE_SERVICE_ROLE_KEY");
  const fetcher = deps.fetch ?? fetch;

  function tableUrl(tableName: string, query: Record<string, string> = {}) {
    const params = new URLSearchParams(query);
    const suffix = params.toString() ? `?${params.toString()}` : "";
    return `${url}/rest/v1/${tableName}${suffix}`;
  }

  function headers(preferRepresentation = false) {
    const h = new Headers();
    h.set("apikey", key);
    h.set("authorization", `Bearer ${key}`);
    h.set("content-type", "application/json");
    if (preferRepresentation) h.set("prefer", "return=representation");
    return h;
  }

  return {
    select: (tableName: string, query: Record<string, string>) =>
      fetcher(tableUrl(tableName, query), {
        method: "GET",
        headers: headers(false),
      }),
    insert: (tableName: string, body: Record<string, unknown>) =>
      fetcher(tableUrl(tableName), {
        method: "POST",
        headers: headers(true),
        body: JSON.stringify(body),
      }),
    update: (
      tableName: string,
      query: Record<string, string>,
      body: Record<string, unknown>,
    ) =>
      fetcher(tableUrl(tableName, query), {
        method: "PATCH",
        headers: headers(true),
        body: JSON.stringify(body),
      }),
    delete: (tableName: string, query: Record<string, string>) =>
      fetcher(tableUrl(tableName, query), {
        method: "DELETE",
        headers: headers(true),
      }),
  };
}

async function callRpc(payload: OverviewPayload, deps: Dependencies) {
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

class AdminActionError extends Error {
  status: number;
  code: ErrorCode;

  constructor(status: number, code: ErrorCode, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function parseJsonArray(res: Response): Promise<Array<Record<string, any>>> {
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    throw new AdminActionError(502, "rpc_failed", "db_response_invalid");
  }
  if (!res.ok) {
    throw new AdminActionError(502, "rpc_failed", "db_write_failed");
  }
  return Array.isArray(body) ? body as Array<Record<string, any>> : [];
}

function appInstanceLabel(displayName: string, suffix: string): string {
  return `${displayName} ${suffix}`;
}

function deviceMetadata(payload: AddPhysicalPhonePayload) {
  return {
    model: payload.model,
    product: payload.product,
    device: payload.device,
    source: "admin_dashboard_add_physical_phone",
    packages_mode: payload.packagesMode,
    primary_app_expected: true,
    expected_instagram_clones: 3,
    add_physical_phone_v1: true,
  };
}

function adbSerialSafe(adbSerial: string) {
  return {
    adb_serial_suffix: adbSerial.slice(-4),
  };
}

async function sha12(value: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(value),
  );
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, 12);
}

async function publishPhoneAuditEvent(
  rest: RestClient,
  eventType: "phone_added" | "phone_app_instances_created",
  payload: AddPhysicalPhonePayload,
  deviceId: string,
  metadata: Record<string, unknown>,
): Promise<{ attempted: boolean; published: boolean; reason: string | null }> {
  try {
    const res = await rest.insert("runtime_events", {
      event_type: eventType,
      severity: "info",
      visibility: "admin_only",
      device_id: deviceId,
      source: "admin_dashboard",
      reason: "add_physical_phone_v1",
      message: "Physical phone inventory updated from admin dashboard.",
      metadata: {
        ...metadata,
        ...adbSerialSafe(payload.adbSerial),
        adb_serial_hash: await sha12(payload.adbSerial),
        display_name: payload.displayName,
        pool: payload.pool,
        max_clones: payload.maxClones,
        actor_type: payload.actorType,
        actor_id: payload.actorId,
        safe_audit: true,
      },
    });
    if (!res.ok) return { attempted: true, published: false, reason: "insert_failed" };
    return { attempted: true, published: true, reason: null };
  } catch {
    return { attempted: true, published: false, reason: "insert_failed" };
  }
}

async function findPhoneByAdbSerial(
  rest: RestClient,
  adbSerial: string,
): Promise<PhoneDeviceRow | null> {
  const rows = await parseJsonArray(await rest.select("phone_devices", {
    select: "id,name,adb_serial,hub_port,metadata",
    adb_serial: `eq.${adbSerial}`,
    limit: "2",
  }));
  if (rows.length > 1) {
    throw new AdminActionError(409, "conflict", "duplicate_adb_serial");
  }
  return rows.length === 1 ? rows[0] as PhoneDeviceRow : null;
}

async function savePhoneDevice(
  rest: RestClient,
  payload: AddPhysicalPhonePayload,
  warnings: string[],
  existing: PhoneDeviceRow | null,
): Promise<PhoneDeviceRow> {
  if (existing?.hub_port && payload.hubPort && existing.hub_port !== payload.hubPort) {
    warnings.push("hub_port_changed");
  }

  const body = {
    device_kind: "physical_phone",
    name: payload.displayName,
    device_name: payload.displayName,
    adb_serial: payload.adbSerial,
    device_udid: payload.adbSerial,
    host_machine: payload.hostLabel,
    hub_label: payload.hubLabel,
    hub_port: payload.hubPort,
    pool_type: payload.pool,
    max_clones: payload.maxClones,
    status: "available",
    metadata: {
      ...(existing?.metadata && typeof existing.metadata === "object"
        ? existing.metadata
        : {}),
      ...deviceMetadata(payload),
    },
  };

  const rows = existing
    ? await parseJsonArray(await rest.update("phone_devices", {
      id: `eq.${existing.id}`,
    }, body))
    : await parseJsonArray(await rest.insert("phone_devices", body));
  if (rows.length !== 1) {
    throw new AdminActionError(502, "rpc_failed", "phone_device_write_failed");
  }
  return rows[0] as PhoneDeviceRow;
}

async function loadAppInstances(
  rest: RestClient,
  deviceId: string,
): Promise<AppInstanceRow[]> {
  const rows = await parseJsonArray(await rest.select("phone_app_instances", {
    select: "id,instance_type,instance_index,package_name,status,current_account_id,metadata",
    device_id: `eq.${deviceId}`,
  }));
  return rows as AppInstanceRow[];
}

async function ensureStandardAppInstances(
  rest: RestClient,
  deviceId: string,
  payload: AddPhysicalPhonePayload,
): Promise<{ created: number; existing: number }> {
  const existingRows = await loadAppInstances(rest, deviceId);
  const byIndex = new Map<number, AppInstanceRow>();
  const byPackage = new Map<string, AppInstanceRow>();
  for (const row of existingRows) {
    byIndex.set(Number(row.instance_index), row);
    if (row.package_name) byPackage.set(String(row.package_name), row);
  }

  let created = 0;
  let existing = 0;
  for (const desired of STANDARD_INSTAGRAM_APP_INSTANCES) {
    const currentByIndex = byIndex.get(desired.instanceIndex);
    const currentByPackage = byPackage.get(desired.packageName);
    if (currentByIndex) {
      if (
        currentByIndex.package_name !== desired.packageName ||
        currentByIndex.instance_type !== desired.instanceType
      ) {
        throw new AdminActionError(
          409,
          "conflict",
          "app_instance_index_conflict",
        );
      }
      if (
        currentByIndex.current_account_id ||
        currentByIndex.status === "occupied"
      ) {
        throw new AdminActionError(
          409,
          "conflict",
          "app_instance_occupied",
        );
      }
      existing += 1;
      continue;
    }
    if (
      currentByPackage &&
      Number(currentByPackage.instance_index) !== desired.instanceIndex
    ) {
      throw new AdminActionError(
        409,
        "conflict",
        "app_instance_package_conflict",
      );
    }

    const inserted = await parseJsonArray(await rest.insert("phone_app_instances", {
      device_id: deviceId,
      instance_type: desired.instanceType,
      instance_index: desired.instanceIndex,
      visible_label: appInstanceLabel(payload.displayName, desired.labelSuffix),
      package_name: desired.packageName,
      launch_activity: null,
      is_launchable: true,
      status: "available",
      current_account_id: null,
      usable_for_auto_login: true,
      metadata: {
        inventory_source: "admin_dashboard_add_physical_phone",
        packages_mode: payload.packagesMode,
        adb_package_verified: false,
        android_clone_created_by_v1: false,
      },
    }));
    if (inserted.length !== 1) {
      throw new AdminActionError(502, "rpc_failed", "app_instance_write_failed");
    }
    created += 1;
  }
  return { created, existing };
}

async function assertExistingAppInstancesCanBeManaged(
  rest: RestClient,
  deviceId: string,
): Promise<void> {
  const existingRows = await loadAppInstances(rest, deviceId);
  const byIndex = new Map<number, AppInstanceRow>();
  const byPackage = new Map<string, AppInstanceRow>();
  for (const row of existingRows) {
    byIndex.set(Number(row.instance_index), row);
    if (row.package_name) byPackage.set(String(row.package_name), row);
  }
  for (const desired of STANDARD_INSTAGRAM_APP_INSTANCES) {
    const currentByIndex = byIndex.get(desired.instanceIndex);
    const currentByPackage = byPackage.get(desired.packageName);
    if (currentByIndex) {
      if (
        currentByIndex.package_name !== desired.packageName ||
        currentByIndex.instance_type !== desired.instanceType
      ) {
        throw new AdminActionError(
          409,
          "conflict",
          "app_instance_index_conflict",
        );
      }
      if (
        currentByIndex.current_account_id ||
        currentByIndex.status === "occupied"
      ) {
        throw new AdminActionError(
          409,
          "conflict",
          "app_instance_occupied",
        );
      }
    }
    if (
      currentByPackage &&
      Number(currentByPackage.instance_index) !== desired.instanceIndex
    ) {
      throw new AdminActionError(
        409,
        "conflict",
        "app_instance_package_conflict",
      );
    }
  }
}

async function addPhysicalPhone(
  payload: AddPhysicalPhonePayload,
  deps: Dependencies,
): Promise<AddPhysicalPhoneResult> {
  const rest = createServiceRoleRestClient(deps);
  const warnings: string[] = [];
  const existing = await findPhoneByAdbSerial(rest, payload.adbSerial);
  if (existing) await assertExistingAppInstancesCanBeManaged(rest, existing.id);
  const device = await savePhoneDevice(rest, payload, warnings, existing);
  const counts = await ensureStandardAppInstances(rest, device.id, payload);
  const audit = await publishPhoneAuditEvent(
    rest,
    counts.created > 0 ? "phone_app_instances_created" : "phone_added",
    payload,
    device.id,
    {
      app_instances_created_count: counts.created,
      app_instances_existing_count: counts.existing,
      warnings,
    },
  );
  if (!audit.published) warnings.push("audit_event_not_published");
  return {
    deviceId: device.id,
    adbSerial: payload.adbSerial,
    displayName: payload.displayName,
    appInstancesCreatedCount: counts.created,
    appInstancesExistingCount: counts.existing,
    warnings,
    audit,
  };
}

type DeletePreflightSummary = {
  deviceId: string;
  displayName: string;
  deviceKind: string;
  inventoryStatus: string;
  adbSerialSuffix: string | null;
  cloneCount: number;
  occupiedCloneCount: number;
  activeAssignmentCount: number;
  assignmentHistoryCount: number;
  releasedAssignmentCount: number;
  releasedAssignmentsInfoFr: string | null;
  linkedInstagramAccountCount: number;
  activeRunRequestCount: number;
  activeLiveViewCount: number;
  activeCredentialCount: number;
  deletable: boolean;
  blockingReasonsFr: string[];
};

function isOperationalPhoneStatus(status: string) {
  return String(status || "").toLowerCase() !== "retired";
}

function releasedAssignmentsInfoFr(count: number): string | null {
  if (count <= 0) return null;
  if (count === 1) {
    return "1 ancienne assignation terminée sera conservée dans l'historique.";
  }
  return `${count} anciennes assignations terminées seront conservées dans l'historique.`;
}

async function findPhoneById(
  rest: RestClient,
  deviceId: string,
): Promise<Record<string, any> | null> {
  const rows = await parseJsonArray(await rest.select("phone_devices", {
    select: "id,name,device_kind,status,adb_serial,metadata,retired_at",
    id: `eq.${deviceId}`,
    limit: "1",
  }));
  return rows.length === 1 ? rows[0] : null;
}

function blockingReasonLabelsFr(reasons: string[]): string[] {
  const labels: Record<string, string> = {
    occupied_clone: "Un clone est occupé ou réservé sur ce téléphone",
    active_assignment: "Une assignation active est en cours sur ce téléphone",
    linked_instagram_account: "Un compte Instagram est encore lié à ce téléphone",
    active_run_request: "Une demande de run est active pour un compte de ce téléphone",
    active_live_view: "Une session live view est active sur ce téléphone",
    active_credential: "Un identifiant Vault non révoqué est lié à un compte de ce téléphone",
    not_physical_phone: "Seuls les téléphones physiques peuvent être retirés de l'inventaire",
    already_retired: "Ce téléphone est déjà retiré de l'inventaire opérationnel",
  };
  return reasons.map((reason) => labels[reason] || reason);
}

async function buildDeletePhysicalPhonePreflight(
  rest: RestClient,
  deviceId: string,
): Promise<DeletePreflightSummary> {
  const phone = await findPhoneById(rest, deviceId);
  if (!phone) {
    throw new AdminActionError(404, "validation_error", "device_not_found");
  }

  const deviceKind = String(phone.device_kind || "physical_phone");
  const displayName = String(phone.name || phone.device_name || "Unknown phone");
  const inventoryStatus = String(phone.status || "unknown");
  const adbSerial = String(phone.adb_serial || "");
  const appInstances = await loadAppInstances(rest, deviceId);
  const occupiedCloneCount = appInstances.filter((row) =>
    Boolean(row.current_account_id) ||
    OCCUPIED_APP_INSTANCE_STATUSES.has(String(row.status || "").toLowerCase())
  ).length;
  const linkedAccountIds = new Set(
    appInstances
      .map((row) => String(row.current_account_id || "").trim())
      .filter(Boolean),
  );

  const assignmentRows = await parseJsonArray(await rest.select("account_assignments", {
    select: "id,status,account_id",
    device_id: `eq.${deviceId}`,
    limit: "1000",
  }));
  const activeAssignmentCount = assignmentRows.filter((row) =>
    ACTIVE_ASSIGNMENT_STATUSES.has(String(row.status || "").toLowerCase())
  ).length;
  const assignmentHistoryCount = assignmentRows.length;
  const releasedAssignmentCount = assignmentRows.filter((row) =>
    String(row.status || "").toLowerCase() === "released"
  ).length;

  let activeRunRequestCount = 0;
  let activeCredentialCount = 0;
  if (linkedAccountIds.size) {
    const accountFilter = `in.(${Array.from(linkedAccountIds).join(",")})`;
    const runRows = await parseJsonArray(await rest.select("account_run_requests", {
      select: "id,status,account_id",
      account_id: accountFilter,
      limit: "1000",
    }));
    activeRunRequestCount = runRows.filter((row) =>
      ACTIVE_RUN_REQUEST_STATUSES.has(String(row.status || "").toLowerCase())
    ).length;

    const credentialRows = await parseJsonArray(await rest.select("account_credentials", {
      select: "id,account_id,status",
      account_id: accountFilter,
      limit: "1000",
    }));
    activeCredentialCount = credentialRows.filter((row) =>
      String(row.status || "").toLowerCase() === "active"
    ).length;
  }

  const liveRows = await parseJsonArray(await rest.select("live_view_sessions", {
    select: "id,status",
    device_id: `eq.${deviceId}`,
    limit: "1000",
  }));
  const activeLiveViewCount = liveRows.filter((row) =>
    ACTIVE_LIVE_VIEW_STATUSES.has(String(row.status || "").toLowerCase())
  ).length;

  const blockingReasons: string[] = [];
  if (!isOperationalPhoneStatus(inventoryStatus)) blockingReasons.push("already_retired");
  if (deviceKind !== "physical_phone") blockingReasons.push("not_physical_phone");
  if (occupiedCloneCount > 0) blockingReasons.push("occupied_clone");
  if (activeAssignmentCount > 0) blockingReasons.push("active_assignment");
  if (linkedAccountIds.size > 0) blockingReasons.push("linked_instagram_account");
  if (activeRunRequestCount > 0) blockingReasons.push("active_run_request");
  if (activeLiveViewCount > 0) blockingReasons.push("active_live_view");
  if (activeCredentialCount > 0) blockingReasons.push("active_credential");

  return {
    deviceId,
    displayName,
    deviceKind,
    inventoryStatus,
    adbSerialSuffix: adbSerial ? adbSerial.slice(-4) : null,
    cloneCount: appInstances.length,
    occupiedCloneCount,
    activeAssignmentCount,
    assignmentHistoryCount,
    releasedAssignmentCount,
    releasedAssignmentsInfoFr: releasedAssignmentsInfoFr(releasedAssignmentCount),
    linkedInstagramAccountCount: linkedAccountIds.size,
    activeRunRequestCount,
    activeLiveViewCount,
    activeCredentialCount,
    deletable: blockingReasons.length === 0,
    blockingReasonsFr: blockingReasonLabelsFr(blockingReasons),
  };
}

async function publishPhoneRetiredAuditEvent(
  rest: RestClient,
  payload: DeletePhysicalPhonePayload,
  summary: DeletePreflightSummary,
): Promise<{ attempted: boolean; published: boolean; reason: string | null }> {
  try {
    const res = await rest.insert("runtime_events", {
      event_type: "phone_retired",
      severity: "warning",
      visibility: "admin_only",
      device_id: summary.deviceId,
      source: "admin_dashboard",
      reason: "operational_retirement_v1",
      message: "Physical phone retired from operational inventory.",
      metadata: {
        safe_audit: true,
        display_name: summary.displayName,
        adb_serial_suffix: summary.adbSerialSuffix,
        clone_count: summary.cloneCount,
        released_assignment_count: summary.releasedAssignmentCount,
        actor_type: payload.actorType,
        actor_id: payload.actorId,
        operational_retirement_v1: true,
      },
    });
    if (!res.ok) return { attempted: true, published: false, reason: "insert_failed" };
    return { attempted: true, published: true, reason: null };
  } catch {
    return { attempted: true, published: false, reason: "insert_failed" };
  }
}

async function retirePhysicalPhoneInventory(
  payload: DeletePhysicalPhonePayload,
  deps: Dependencies,
): Promise<{
  summary: DeletePreflightSummary;
  audit: { attempted: boolean; published: boolean; reason: string | null };
  idempotent: boolean;
}> {
  const rest = createServiceRoleRestClient(deps);
  const phone = await findPhoneById(rest, payload.deviceId);
  if (!phone) {
    throw new AdminActionError(404, "validation_error", "device_not_found");
  }

  const canonicalDisplayName = String(phone.name || phone.device_name || "").trim();
  if (!payload.confirmationName.trim()) {
    throw new AdminActionError(400, "validation_error", "device_delete_confirmation_required");
  }
  if (payload.confirmationName !== canonicalDisplayName) {
    throw new AdminActionError(400, "validation_error", "device_delete_confirmation_mismatch");
  }

  const summary = await buildDeletePhysicalPhonePreflight(rest, payload.deviceId);

  if (!isOperationalPhoneStatus(String(phone.status || ""))) {
    return {
      summary,
      audit: { attempted: false, published: false, reason: "device_already_retired" },
      idempotent: true,
    };
  }

  if (!summary.deletable) {
    throw new AdminActionError(409, "conflict", "device_delete_blocked_by_active_dependency");
  }

  const retiredAt = new Date().toISOString();
  const existingMetadata = phone.metadata && typeof phone.metadata === "object"
    ? phone.metadata as Record<string, unknown>
    : {};

  const deviceRows = await parseJsonArray(await rest.update("phone_devices", {
    id: `eq.${payload.deviceId}`,
  }, {
    status: "retired",
    retired_at: retiredAt,
    status_reason: "operational_retirement",
    metadata: {
      ...existingMetadata,
      operational_retired_at: retiredAt,
      operational_retirement_v1: true,
    },
  }));
  if (deviceRows.length !== 1 || String(deviceRows[0]?.status || "") !== "retired") {
    throw new AdminActionError(502, "rpc_failed", "device_retire_failed");
  }

  const appInstances = await loadAppInstances(rest, payload.deviceId);
  for (const appInstance of appInstances) {
    const appMetadata = appInstance.metadata && typeof appInstance.metadata === "object"
      ? appInstance.metadata as Record<string, unknown>
      : {};
    const appRows = await parseJsonArray(await rest.update("phone_app_instances", {
      id: `eq.${appInstance.id}`,
    }, {
      status: "disabled",
      usable_for_auto_login: false,
      is_launchable: false,
      metadata: {
        ...appMetadata,
        operational_retired_at: retiredAt,
        operational_retirement_v1: true,
      },
    }));
    if (appRows.length !== 1) {
      throw new AdminActionError(502, "rpc_failed", "device_retire_failed");
    }
  }

  const cloneDisable = await rest.update("phone_clones", {
    device_id: `eq.${payload.deviceId}`,
  }, {
    status: "disabled",
  });
  if (!cloneDisable.ok) {
    throw new AdminActionError(502, "rpc_failed", "device_retire_failed");
  }

  const heartbeatDelete = await rest.delete("device_heartbeats", {
    device_id: `eq.${payload.deviceId}`,
  });
  if (!heartbeatDelete.ok) {
    throw new AdminActionError(502, "rpc_failed", "device_retire_failed");
  }

  const audit = await publishPhoneRetiredAuditEvent(rest, payload, summary);
  return { summary, audit, idempotent: false };
}

async function deletePhysicalPhonePreflight(
  payload: DeletePhysicalPhonePreflightPayload,
  deps: Dependencies,
): Promise<DeletePreflightSummary> {
  const rest = createServiceRoleRestClient(deps);
  return await buildDeletePhysicalPhonePreflight(rest, payload.deviceId);
}

function safeMetadataBoolean(
  metadata: unknown,
  key: string,
): boolean | null {
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) {
    return null;
  }
  const value = (metadata as Record<string, unknown>)[key];
  return typeof value === "boolean" ? value : null;
}

function safeMetadataString(
  metadata: unknown,
  key: string,
): string | null {
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) {
    return null;
  }
  const value = (metadata as Record<string, unknown>)[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function standardClonePackages() {
  return STANDARD_INSTAGRAM_APP_INSTANCES
    .filter((item) => item.instanceType === "clone")
    .map((item) => item.packageName);
}

async function parseJsonArrayOptional(res: Response): Promise<Array<Record<string, any>>> {
  if (!res.ok) return [];
  try {
    const body = await res.json();
    return Array.isArray(body) ? body as Array<Record<string, any>> : [];
  } catch {
    return [];
  }
}

function heartbeatProjection(row: Record<string, any> | undefined) {
  if (!row) {
    return {
      last_seen_at: null,
      heartbeat_status: "unknown",
      heartbeat_issue: "adb_status_unknown",
    };
  }

  const lastSeenAt = typeof row.last_seen_at === "string" ? row.last_seen_at : null;
  const status = typeof row.status === "string" && row.status.trim()
    ? row.status.trim()
    : "unknown";
  const lastSeenMs = lastSeenAt ? new Date(lastSeenAt).getTime() : NaN;
  const stale = Number.isFinite(lastSeenMs) &&
    Date.now() - lastSeenMs > 15 * 60 * 1000;

  if (stale) {
    return {
      last_seen_at: lastSeenAt,
      heartbeat_status: "stale",
      heartbeat_issue: "stale_heartbeat",
    };
  }

  if (status === "online" || status === "busy") {
    return { last_seen_at: lastSeenAt, heartbeat_status: "online", heartbeat_issue: null };
  }
  if (["offline", "unauthorized", "maintenance", "error"].includes(status)) {
    return { last_seen_at: lastSeenAt, heartbeat_status: status, heartbeat_issue: null };
  }
  return {
    last_seen_at: lastSeenAt,
    heartbeat_status: "unknown",
    heartbeat_issue: "adb_status_unknown",
  };
}

function buildDeviceInventory(
  phoneRows: Array<Record<string, any>>,
  appRows: Array<Record<string, any>>,
  heartbeatRows: Array<Record<string, any>>,
) {
  const appsByDevice = new Map<string, Array<Record<string, any>>>();
  for (const row of appRows) {
    const deviceId = typeof row.device_id === "string" ? row.device_id : "";
    if (!deviceId) continue;
    appsByDevice.set(deviceId, [...(appsByDevice.get(deviceId) ?? []), row]);
  }

  const heartbeatByDevice = new Map<string, Record<string, any>>();
  for (const row of heartbeatRows) {
    if (typeof row.device_id === "string") heartbeatByDevice.set(row.device_id, row);
  }

  const adbSerialCounts = new Map<string, number>();
  for (const row of phoneRows) {
    const adbSerial = typeof row.adb_serial === "string" ? row.adb_serial.trim() : "";
    if (adbSerial) adbSerialCounts.set(adbSerial, (adbSerialCounts.get(adbSerial) ?? 0) + 1);
  }

  const expectedClones = standardClonePackages();
  const phoneDevices = phoneRows.map((row) => {
    const deviceId = String(row.id || "");
    const metadata = row.metadata && typeof row.metadata === "object" ? row.metadata : {};
    const apps = (appsByDevice.get(deviceId) ?? [])
      .slice()
      .sort((a, b) => Number(a.instance_index ?? 0) - Number(b.instance_index ?? 0));
    const heartbeat = heartbeatProjection(heartbeatByDevice.get(deviceId));
    const adbSerial = typeof row.adb_serial === "string" ? row.adb_serial.trim() : "";
    const status = typeof row.status === "string" ? row.status : "unknown";
    const packages = new Set(apps.map((app) => String(app.package_name || "")).filter(Boolean));
    const primaryPresent = apps.some((app) =>
      Number(app.instance_index) === 0 &&
      app.instance_type === "primary_app" &&
      app.package_name === "com.instagram.android"
    );
    const registeredClonePackages = expectedClones.filter((pkg) => packages.has(pkg));
    const occupiedCount = apps.filter((app) =>
      app.status === "occupied" || Boolean(app.current_account_id)
    ).length;
    const availableCount = apps.filter((app) =>
      app.status === "available" && !app.current_account_id
    ).length;
    const unavailableDevice = !["available", "active", "reserved"].includes(status);

    const issues: string[] = [];
    if (!adbSerial) issues.push("missing_adb_serial");
    if (adbSerial && (adbSerialCounts.get(adbSerial) ?? 0) > 1) {
      issues.push("duplicate_adb_serial");
    }
    if (apps.length === 0) issues.push("no_app_instances");
    if (!primaryPresent) issues.push("missing_primary_instance");
    if (registeredClonePackages.length < expectedClones.length) {
      issues.push("missing_standard_clone_package");
    }
    if (unavailableDevice && occupiedCount > 0) {
      issues.push("occupied_instance_on_unavailable_device");
    }
    if (heartbeat.heartbeat_issue) issues.push(heartbeat.heartbeat_issue);

    return {
      device_id: deviceId,
      display_name: String(row.name || row.device_name || "Unknown phone"),
      name: String(row.name || row.device_name || "Unknown phone"),
      adb_serial: adbSerial || null,
      device_kind: String(row.device_kind || "unknown"),
      kind: String(row.device_kind || "unknown"),
      status,
      pool: String(row.pool_type || "unknown"),
      max_clones: Number.isFinite(Number(row.max_clones)) ? Number(row.max_clones) : null,
      host_label: typeof row.host_machine === "string" ? row.host_machine : null,
      host: typeof row.host_machine === "string" ? row.host_machine : null,
      hub_label: typeof row.hub_label === "string" ? row.hub_label : null,
      hub_port: typeof row.hub_port === "string" ? row.hub_port : null,
      model: safeMetadataString(metadata, "model"),
      product: safeMetadataString(metadata, "product"),
      device: safeMetadataString(metadata, "device"),
      created_at: typeof row.created_at === "string" ? row.created_at : null,
      updated_at: typeof row.updated_at === "string" ? row.updated_at : null,
      last_seen_at: heartbeat.last_seen_at,
      heartbeat_status: heartbeat.heartbeat_status,
      app_instances_count: apps.length,
      app_instances_available_count: availableCount,
      app_instances_occupied_count: occupiedCount,
      primary_package_present_in_db: primaryPresent,
      clone_packages_expected_count: expectedClones.length,
      clone_packages_registered_count: registeredClonePackages.length,
      issues,
      app_instances: apps.map((app) => ({
        app_instance_id: String(app.id || ""),
        instance_index: Number.isFinite(Number(app.instance_index))
          ? Number(app.instance_index)
          : null,
        instance_kind: String(app.instance_type || "unknown"),
        app_role: String(app.instance_type || "unknown"),
        package_name: typeof app.package_name === "string" ? app.package_name : null,
        status: typeof app.status === "string" ? app.status : "unknown",
        current_account_id: typeof app.current_account_id === "string"
          ? app.current_account_id
          : null,
        adb_package_verified: safeMetadataBoolean(app.metadata, "adb_package_verified"),
      })),
    };
  });

  const summary = {
    total_phone_devices: phoneDevices.length,
    physical_phone_count: phoneDevices.filter((phone) => phone.device_kind === "physical_phone").length,
    emulator_count: phoneDevices.filter((phone) => phone.device_kind === "emulator").length,
    available_phone_count: phoneDevices.filter((phone) => phone.status === "available").length,
    unavailable_phone_count: phoneDevices.filter((phone) => phone.status !== "available").length,
    total_app_instances: phoneDevices.reduce((sum, phone) => sum + phone.app_instances_count, 0),
    available_app_instances: phoneDevices.reduce((sum, phone) => sum + phone.app_instances_available_count, 0),
    occupied_app_instances: phoneDevices.reduce((sum, phone) => sum + phone.app_instances_occupied_count, 0),
    problem_phone_count: phoneDevices.filter((phone) => phone.issues.length > 0).length,
    adb_status_unknown_count: phoneDevices.filter((phone) => phone.heartbeat_status === "unknown").length,
  };

  return { phoneDevices, summary };
}

async function devicesOverview(deps: Dependencies) {
  const rest = createServiceRoleRestClient(deps);
  const phoneRows = await parseJsonArray(await rest.select("phone_devices", {
    select: "id,name,device_name,device_kind,adb_serial,host_machine,hub_label,hub_port,pool_type,max_clones,status,metadata,created_at,updated_at",
    status: "neq.retired",
    order: "created_at.desc",
  }));
  const appRows = await parseJsonArray(await rest.select("phone_app_instances", {
    select: "id,device_id,instance_type,instance_index,package_name,status,current_account_id,metadata",
  }));
  const heartbeatRows = await parseJsonArrayOptional(await rest.select("device_heartbeats", {
    select: "device_id,status,last_seen_at",
  }));
  return buildDeviceInventory(phoneRows, appRows, heartbeatRows);
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

  const rid = requestId(req, deps);
  const rawAction = typeof rawPayload.action === "string"
    ? rawPayload.action.trim()
    : "";
  if (rawAction.startsWith("live_view_")) {
    const liveParsed = validateLiveViewPayload(rawPayload);
    if (!liveParsed.ok) {
      return errorResponse(
        liveParsed.status,
        liveParsed.code,
        liveParsed.error,
        headers,
      );
    }
    try {
      const rest = createServiceRoleRestClient(deps);
      const result = await handleLiveViewAction(liveParsed.payload, rest);
      const body = result.body as Record<string, unknown>;
      const errorCode = typeof body.error === "object" && body.error
        ? String((body.error as Record<string, unknown>).code || "")
        : "";
      const status = body.ok === false && errorCode === "livekit_not_configured"
        ? 503
        : 200;
      logEvent(deps, "admin_dashboard_live_view_succeeded", {
        request_id: rid,
        action: liveParsed.payload.action,
        status,
      });
      return jsonResponse(status, {
        action: liveParsed.payload.action,
        ...body,
      }, headers);
    } catch (error) {
      const actionError = error instanceof LiveViewActionError
        ? error
        : new LiveViewActionError(500, "internal_error", "live_view_failed");
      logEvent(deps, "admin_dashboard_live_view_failed", {
        request_id: rid,
        action: liveParsed.payload.action,
        status: actionError.status,
        error: actionError.message,
        code: actionError.code,
      });
      if (actionError.code === "livekit_not_configured") {
        return jsonResponse(503, {
          ok: false,
          error: {
            code: "livekit_not_configured",
            message: actionError.message,
          },
        }, headers);
      }
      return errorResponse(
        actionError.status,
        actionError.code as ErrorCode,
        actionError.message,
        headers,
      );
    }
  }

  const parsed = validatePayload(rawPayload);
  if (!parsed.ok) {
    return errorResponse(parsed.status, parsed.code, parsed.error, headers);
  }

  const payload = parsed.payload;
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
  if (payload.action === "add_physical_phone") {
    try {
      const result = await addPhysicalPhone(payload, deps);
      logEvent(deps, "admin_dashboard_add_physical_phone_succeeded", {
        request_id: rid,
        action: payload.action,
        device_id: result.deviceId,
        app_instances_created_count: result.appInstancesCreatedCount,
        app_instances_existing_count: result.appInstancesExistingCount,
        warnings: result.warnings,
        status: 200,
      });
      return jsonResponse(200, {
        ok: true,
        action: payload.action,
        phone: {
          device_id: result.deviceId,
          adb_serial: result.adbSerial,
          display_name: result.displayName,
        },
        app_instances_created_count: result.appInstancesCreatedCount,
        app_instances_existing_count: result.appInstancesExistingCount,
        warnings: result.warnings,
        audit: result.audit,
      }, headers);
    } catch (error) {
      const actionError = error instanceof AdminActionError
        ? error
        : new AdminActionError(500, "internal_error", "add_physical_phone_failed");
      logEvent(deps, "admin_dashboard_add_physical_phone_failed", {
        request_id: rid,
        action: payload.action,
        status: actionError.status,
        error: actionError.message,
      });
      return errorResponse(
        actionError.status,
        actionError.code,
        actionError.message,
        headers,
      );
    }
  }
  if (payload.action === "delete_physical_phone_preflight") {
    try {
      const summary = await deletePhysicalPhonePreflight(payload, deps);
      logEvent(deps, "admin_dashboard_delete_physical_phone_preflight_succeeded", {
        request_id: rid,
        action: payload.action,
        device_id: summary.deviceId,
        deletable: summary.deletable,
        status: 200,
      });
      return jsonResponse(200, {
        ok: true,
        action: payload.action,
        preflight: summary,
      }, headers);
    } catch (error) {
      const actionError = error instanceof AdminActionError
        ? error
        : new AdminActionError(500, "internal_error", "device_delete_preflight_failed");
      logEvent(deps, "admin_dashboard_delete_physical_phone_preflight_failed", {
        request_id: rid,
        action: payload.action,
        status: actionError.status,
        error: actionError.message,
      });
      return errorResponse(
        actionError.status,
        actionError.code,
        actionError.message,
        headers,
      );
    }
  }
  if (payload.action === "delete_physical_phone") {
    try {
      const result = await retirePhysicalPhoneInventory(payload, deps);
      logEvent(deps, "admin_dashboard_delete_physical_phone_succeeded", {
        request_id: rid,
        action: payload.action,
        device_id: result.summary.deviceId,
        idempotent: result.idempotent,
        status: 200,
      });
      return jsonResponse(200, {
        ok: true,
        action: payload.action,
        retired: {
          device_id: result.summary.deviceId,
          display_name: result.summary.displayName,
          operational_status: "retired",
        },
        deleted: {
          device_id: result.summary.deviceId,
          display_name: result.summary.displayName,
        },
        idempotent: result.idempotent,
        reason: result.idempotent ? (result.audit.reason || "device_already_retired") : null,
        audit: result.audit,
      }, headers);
    } catch (error) {
      const actionError = error instanceof AdminActionError
        ? error
        : new AdminActionError(500, "internal_error", "delete_physical_phone_failed");
      logEvent(deps, "admin_dashboard_delete_physical_phone_failed", {
        request_id: rid,
        action: payload.action,
        status: actionError.status,
        error: actionError.message,
      });
      return errorResponse(
        actionError.status,
        actionError.code,
        actionError.message,
        headers,
      );
    }
  }
  if (payload.action === "devices_overview") {
    try {
      const result = await devicesOverview(deps);
      logEvent(deps, "admin_dashboard_devices_overview_succeeded", {
        request_id: rid,
        action: payload.action,
        count: result.phoneDevices.length,
        status: 200,
      });
      return jsonResponse(200, {
        ok: true,
        action: payload.action,
        count: result.phoneDevices.length,
        phone_devices: result.phoneDevices,
        items: result.phoneDevices,
        phone_inventory_summary: result.summary,
      }, headers);
    } catch {
      logEvent(deps, "admin_dashboard_devices_overview_failed", {
        request_id: rid,
        action: payload.action,
        status: 502,
        error: "devices_overview_failed",
      });
      return errorResponse(
        502,
        "rpc_failed",
        "Device inventory projection failed.",
        headers,
      );
    }
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
