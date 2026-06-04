// deno-lint-ignore-file no-explicit-any
/** Live Phone View Web V1 foundation — session model, safe API, LiveKit token. */

import type { RestClient } from "./live_view_types.ts";

export type LiveViewAction =
  | "live_view_start"
  | "live_view_stop"
  | "live_view_status"
  | "live_view_token";

export type LiveViewMode = "view_only" | "interactive";
export type LiveViewStatus =
  | "pending"
  | "starting"
  | "active"
  | "stopped"
  | "failed"
  | "expired";

export type LiveViewStartPayload = {
  action: "live_view_start";
  accountId: string;
  mode: LiveViewMode;
  source: string;
  actorId: string | null;
  requestedBy: string;
};

export type LiveViewStopPayload = {
  action: "live_view_stop";
  accountId: string | null;
  liveViewSessionId: string | null;
  actorId: string | null;
};

export type LiveViewStatusPayload = {
  action: "live_view_status";
  accountId: string;
};

export type LiveViewTokenPayload = {
  action: "live_view_token";
  liveViewSessionId: string;
  actorId: string | null;
};

export type LiveViewParsedPayload =
  | LiveViewStartPayload
  | LiveViewStopPayload
  | LiveViewStatusPayload
  | LiveViewTokenPayload;

export type LiveViewErrorCode =
  | "validation_error"
  | "conflict"
  | "rpc_failed"
  | "internal_error"
  | "assignment_not_found"
  | "device_unavailable"
  | "session_not_found"
  | "session_not_active"
  | "livekit_not_configured";

export class LiveViewActionError extends Error {
  status: number;
  code: LiveViewErrorCode;

  constructor(status: number, code: LiveViewErrorCode, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

const LIVE_VIEW_ACTIONS = new Set([
  "live_view_start",
  "live_view_stop",
  "live_view_status",
  "live_view_token",
]);
const LIVE_VIEW_MODES = new Set(["view_only", "interactive"]);
const ACTIVE_SESSION_STATUSES = ["pending", "starting", "active"];
const OPEN_ASSIGNMENT_STATUSES = ["pending", "reserved", "active"];
const ACTIVE_IG_RUN_STATUSES = [
  "running",
  "queued",
  "pending",
  "in_progress",
  "active",
  "starting",
];
const ACTIVE_REQUEST_STATUSES = [
  "queued",
  "claimed",
  "starting",
  "running",
];
const SESSION_TTL_SECONDS = 600;
const LIVEKIT_TOKEN_TTL_SECONDS = 300;
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const LIVE_VIEW_FORBIDDEN_TOP_LEVEL = new Set([
  "password",
  "secret",
  "secret_ref",
  "token",
  "adb_serial",
  "device_udid",
  "hub_port",
  "usb_port",
  "metadata",
  "raw_metadata",
]);

export function isUuid(value: unknown): boolean {
  return typeof value === "string" && UUID_RE.test(value.trim());
}

function requiredUuid(
  value: unknown,
  key: string,
): { ok: true; value: string } | { ok: false; error: string } {
  if (!isUuid(value)) return { ok: false, error: `${key}_invalid` };
  return { ok: true, value: String(value).trim() };
}

function optionalUuid(
  value: unknown,
  key: string,
): { ok: true; value: string | null } | { ok: false; error: string } {
  if (value == null || String(value).trim() === "") {
    return { ok: true, value: null };
  }
  if (!isUuid(value)) return { ok: false, error: `${key}_invalid` };
  return { ok: true, value: String(value).trim() };
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
  if (trimmed.length > maxLength) return { ok: false, error: `${key}_too_long` };
  return { ok: true, value: trimmed };
}

export function validateLiveViewPayload(
  payload: Record<string, unknown>,
): { ok: true; payload: LiveViewParsedPayload } | {
  ok: false;
  error: string;
  code: LiveViewErrorCode;
  status: number;
} {
  for (const key of Object.keys(payload || {})) {
    if (LIVE_VIEW_FORBIDDEN_TOP_LEVEL.has(key.trim().toLowerCase())) {
      return {
        ok: false,
        error: `forbidden_field:${key}`,
        code: "validation_error",
        status: 400,
      };
    }
  }

  const action = typeof payload.action === "string"
    ? payload.action.trim()
    : "";
  if (!LIVE_VIEW_ACTIONS.has(action)) {
    return {
      ok: false,
      error: "unsupported_action",
      code: "validation_error",
      status: 400,
    };
  }

  const actorId = optionalBoundedString(
    payload.actor_id ?? payload.actorId,
    "actor_id",
    120,
  );
  if (!actorId.ok) {
    return {
      ok: false,
      error: actorId.error,
      code: "validation_error",
      status: 400,
    };
  }

  if (action === "live_view_start") {
    const accountId = requiredUuid(payload.account_id ?? payload.accountId, "account_id");
    if (!accountId.ok) {
      return {
        ok: false,
        error: accountId.error,
        code: "validation_error",
        status: 400,
      };
    }
    const rawMode = typeof payload.mode === "string"
      ? payload.mode.trim()
      : "view_only";
    if (!LIVE_VIEW_MODES.has(rawMode)) {
      return {
        ok: false,
        error: "mode_invalid",
        code: "validation_error",
        status: 400,
      };
    }
    const source = optionalBoundedString(payload.source, "source", 80);
    if (!source.ok) {
      return {
        ok: false,
        error: source.error,
        code: "validation_error",
        status: 400,
      };
    }
    const requestedBy = optionalBoundedString(
      payload.requested_by ?? payload.requestedBy,
      "requested_by",
      120,
    );
    if (!requestedBy.ok) {
      return {
        ok: false,
        error: requestedBy.error,
        code: "validation_error",
        status: 400,
      };
    }
    const effectiveRequestedBy = requestedBy.value || actorId.value || "admin_dashboard";
    return {
      ok: true,
      payload: {
        action: "live_view_start",
        accountId: accountId.value,
        mode: rawMode as LiveViewMode,
        source: source.value || "manager_row_eye",
        actorId: actorId.value,
        requestedBy: effectiveRequestedBy,
      },
    };
  }

  if (action === "live_view_stop") {
    const sessionId = optionalUuid(
      payload.live_view_session_id ?? payload.liveViewSessionId,
      "live_view_session_id",
    );
    if (!sessionId.ok) {
      return {
        ok: false,
        error: sessionId.error,
        code: "validation_error",
        status: 400,
      };
    }
    const accountId = optionalUuid(payload.account_id ?? payload.accountId, "account_id");
    if (!accountId.ok) {
      return {
        ok: false,
        error: accountId.error,
        code: "validation_error",
        status: 400,
      };
    }
    if (!sessionId.value && !accountId.value) {
      return {
        ok: false,
        error: "live_view_session_id_or_account_id_required",
        code: "validation_error",
        status: 400,
      };
    }
    return {
      ok: true,
      payload: {
        action: "live_view_stop",
        liveViewSessionId: sessionId.value,
        accountId: accountId.value,
        actorId: actorId.value,
      },
    };
  }

  if (action === "live_view_status") {
    const accountId = requiredUuid(payload.account_id ?? payload.accountId, "account_id");
    if (!accountId.ok) {
      return {
        ok: false,
        error: accountId.error,
        code: "validation_error",
        status: 400,
      };
    }
    return {
      ok: true,
      payload: {
        action: "live_view_status",
        accountId: accountId.value,
      },
    };
  }

  const sessionId = requiredUuid(
    payload.live_view_session_id ?? payload.liveViewSessionId,
    "live_view_session_id",
  );
  if (!sessionId.ok) {
    return {
      ok: false,
      error: sessionId.error,
      code: "validation_error",
      status: 400,
    };
  }
  return {
    ok: true,
    payload: {
      action: "live_view_token",
      liveViewSessionId: sessionId.value,
      actorId: actorId.value,
    },
  };
}

const LIVE_VIEW_BLOCKED_KEYS = new Set([
  "adb_serial",
  "device_udid",
  "hub_port",
  "usb_port",
  "host_machine",
  "host_id",
  "metadata",
  "raw_metadata",
  "secret",
  "secret_ref",
  "password",
  "password_hash",
  "internal_api_token",
  "service_role",
  "service_role_key",
  "authorization",
  "cookie",
  "vault_payload",
]);

export function sanitizeLiveViewPublic(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sanitizeLiveViewPublic);
  if (!value || typeof value !== "object") return value;
  const output: Record<string, unknown> = {};
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    const normalized = key.trim().toLowerCase();
    if (LIVE_VIEW_BLOCKED_KEYS.has(normalized)) continue;
    output[key] = sanitizeLiveViewPublic(child);
  }
  return output;
}

export function cloneLabelFromInstance(
  instanceIndex: number | null | undefined,
  visibleLabel: string | null | undefined,
): string {
  const label = typeof visibleLabel === "string" ? visibleLabel.trim() : "";
  if (label) {
    const match = label.match(/clone\s*(\d+)/i);
    if (match) return `clone ${match[1]}`;
  }
  if (Number.isFinite(Number(instanceIndex)) && Number(instanceIndex) > 0) {
    return `clone ${Number(instanceIndex)}`;
  }
  if (Number(instanceIndex) === 0) return "primary";
  return "unknown clone";
}

function utcNowIso(): string {
  return new Date().toISOString();
}

function expiresAtIso(seconds: number): string {
  return new Date(Date.now() + seconds * 1000).toISOString();
}

async function parseJsonArray(res: Response): Promise<Array<Record<string, any>>> {
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    throw new LiveViewActionError(502, "rpc_failed", "db_response_invalid");
  }
  if (!res.ok) {
    throw new LiveViewActionError(502, "rpc_failed", "db_read_failed");
  }
  return Array.isArray(body) ? body as Array<Record<string, any>> : [];
}

async function parseJsonSingle(
  res: Response,
): Promise<Record<string, any> | null> {
  const rows = await parseJsonArray(res);
  return rows.length ? rows[0] : null;
}

async function insertAuditEvent(
  rest: RestClient,
  event: {
    sessionId?: string | null;
    eventType: string;
    actorId?: string | null;
    accountId?: string | null;
    deviceId?: string | null;
    appInstanceId?: string | null;
    action: string;
    metadataSafe?: Record<string, unknown>;
  },
) {
  await rest.insert("live_view_audit_events", {
    session_id: event.sessionId ?? null,
    event_type: event.eventType,
    actor_id: event.actorId ?? null,
    account_id: event.accountId ?? null,
    device_id: event.deviceId ?? null,
    app_instance_id: event.appInstanceId ?? null,
    action: event.action,
    metadata_safe: event.metadataSafe ?? {},
  });
}

async function accountHasActiveRun(
  rest: RestClient,
  accountId: string,
): Promise<boolean> {
  const igRuns = await parseJsonArray(await rest.select("ig_runs", {
    select: "id,status",
    account_id: `eq.${accountId}`,
    status: `in.(${ACTIVE_IG_RUN_STATUSES.join(",")})`,
    limit: "1",
  }));
  if (igRuns.length > 0) return true;

  const requests = await parseJsonArray(await rest.select("account_run_requests", {
    select: "id,status",
    account_id: `eq.${accountId}`,
    status: `in.(${ACTIVE_REQUEST_STATUSES.join(",")})`,
    limit: "1",
  }));
  return requests.length > 0;
}

type ResolvedLiveViewContext = {
  accountId: string;
  username: string;
  assignmentId: string;
  deviceId: string;
  deviceLabel: string;
  hostId: string;
  appInstanceId: string;
  packageName: string | null;
  cloneLabel: string;
  instanceIndex: number | null;
};

async function resolveLiveViewContext(
  rest: RestClient,
  accountId: string,
): Promise<ResolvedLiveViewContext> {
  const account = await parseJsonSingle(await rest.select("ig_accounts", {
    select: "id,username,status",
    id: `eq.${accountId}`,
    limit: "1",
  }));
  if (!account?.id) {
    throw new LiveViewActionError(404, "assignment_not_found", "account_not_found");
  }

  const assignments = await parseJsonArray(await rest.select("account_assignments", {
    select: "id,account_id,device_id,app_instance_id,status,created_at",
    account_id: `eq.${accountId}`,
    status: `in.(${OPEN_ASSIGNMENT_STATUSES.join(",")})`,
    order: "created_at.desc",
    limit: "1",
  }));
  const assignment = assignments[0];
  if (!assignment?.id || !assignment.device_id || !assignment.app_instance_id) {
    throw new LiveViewActionError(
      409,
      "assignment_not_found",
      "assignment_not_found",
    );
  }

  const device = await parseJsonSingle(await rest.select("phone_devices", {
    select: "id,name,device_name,status,host_machine",
    id: `eq.${assignment.device_id}`,
    limit: "1",
  }));
  if (!device?.id) {
    throw new LiveViewActionError(409, "device_unavailable", "device_not_found");
  }

  const appInstance = await parseJsonSingle(await rest.select("phone_app_instances", {
    select: "id,device_id,instance_index,visible_label,package_name,status,is_launchable",
    id: `eq.${assignment.app_instance_id}`,
    limit: "1",
  }));
  if (!appInstance?.id) {
    throw new LiveViewActionError(
      409,
      "device_unavailable",
      "app_instance_not_found",
    );
  }

  const hostId = typeof device.host_machine === "string"
    ? device.host_machine.trim()
    : "";
  if (!hostId) {
    throw new LiveViewActionError(409, "device_unavailable", "host_not_configured");
  }

  const deviceStatus = String(device.status || "").trim().toLowerCase();
  const appStatus = String(appInstance.status || "").trim().toLowerCase();
  if (
    ["offline", "unauthorized", "maintenance", "disabled"].includes(deviceStatus) ||
    appStatus === "disabled" ||
    appInstance.is_launchable === false
  ) {
    throw new LiveViewActionError(409, "device_unavailable", "phone_unavailable");
  }

  return {
    accountId,
    username: String(account.username || "").trim() || "unknown",
    assignmentId: String(assignment.id),
    deviceId: String(device.id),
    deviceLabel: String(device.name || device.device_name || "Unknown phone"),
    hostId,
    appInstanceId: String(appInstance.id),
    packageName: typeof appInstance.package_name === "string"
      ? appInstance.package_name
      : null,
    cloneLabel: cloneLabelFromInstance(
      Number(appInstance.instance_index),
      appInstance.visible_label,
    ),
    instanceIndex: Number.isFinite(Number(appInstance.instance_index))
      ? Number(appInstance.instance_index)
      : null,
  };
}

function sessionToSafeResponse(
  row: Record<string, any>,
  context?: Partial<ResolvedLiveViewContext>,
) {
  return sanitizeLiveViewPublic({
    ok: true,
    live_view_session_id: row.id,
    status: row.status,
    mode: row.mode,
    stream_transport: row.stream_transport,
    username: context?.username ?? null,
    device_label: context?.deviceLabel ?? null,
    clone_label: context?.cloneLabel ?? null,
    package_name: context?.packageName ?? null,
    package_label: context?.cloneLabel ?? context?.packageName ?? null,
    run_active_at_start: Boolean(row.run_active_at_start),
    interaction_enabled: Boolean(row.interaction_enabled),
    expires_at: row.expires_at,
    failure_reason: row.failure_reason ?? null,
    livekit_room_name: row.livekit_room_name ?? null,
  }) as Record<string, unknown>;
}

async function loadSessionContext(
  rest: RestClient,
  row: Record<string, any>,
): Promise<Partial<ResolvedLiveViewContext>> {
  const account = await parseJsonSingle(await rest.select("ig_accounts", {
    select: "id,username",
    id: `eq.${row.account_id}`,
    limit: "1",
  }));
  const device = await parseJsonSingle(await rest.select("phone_devices", {
    select: "id,name,device_name",
    id: `eq.${row.device_id}`,
    limit: "1",
  }));
  const appInstance = await parseJsonSingle(await rest.select("phone_app_instances", {
    select: "id,instance_index,visible_label,package_name",
    id: `eq.${row.app_instance_id}`,
    limit: "1",
  }));
  return {
    username: String(account?.username || "").trim() || "unknown",
    deviceLabel: String(device?.name || device?.device_name || "Unknown phone"),
    packageName: typeof appInstance?.package_name === "string"
      ? appInstance.package_name
      : null,
    cloneLabel: cloneLabelFromInstance(
      Number(appInstance?.instance_index),
      appInstance?.visible_label,
    ),
  };
}

async function findActiveSessionForAccount(
  rest: RestClient,
  accountId: string,
): Promise<Record<string, any> | null> {
  const rows = await parseJsonArray(await rest.select("live_view_sessions", {
    select: "*",
    account_id: `eq.${accountId}`,
    status: `in.(${ACTIVE_SESSION_STATUSES.join(",")})`,
    order: "created_at.desc",
    limit: "1",
  }));
  return rows[0] ?? null;
}

async function findActiveSessionForAppInstance(
  rest: RestClient,
  appInstanceId: string,
): Promise<Record<string, any> | null> {
  const rows = await parseJsonArray(await rest.select("live_view_sessions", {
    select: "*",
    app_instance_id: `eq.${appInstanceId}`,
    status: `in.(${ACTIVE_SESSION_STATUSES.join(",")})`,
    order: "created_at.desc",
    limit: "1",
  }));
  return rows[0] ?? null;
}

export async function liveViewStart(
  payload: LiveViewStartPayload,
  rest: RestClient,
) {
  const context = await resolveLiveViewContext(rest, payload.accountId);
  const runActive = await accountHasActiveRun(rest, payload.accountId);
  const effectiveMode: LiveViewMode = runActive ? "view_only" : payload.mode;
  const interactionEnabled = effectiveMode === "interactive" && !runActive;

  const existingForAccount = await findActiveSessionForAccount(
    rest,
    payload.accountId,
  );
  if (existingForAccount) {
    const existingContext = await loadSessionContext(rest, existingForAccount);
    return {
      reused: true,
      body: sessionToSafeResponse(existingForAccount, {
        ...context,
        ...existingContext,
      }),
    };
  }

  const existingForInstance = await findActiveSessionForAppInstance(
    rest,
    context.appInstanceId,
  );
  if (
    existingForInstance &&
    String(existingForInstance.account_id) !== payload.accountId
  ) {
    throw new LiveViewActionError(
      409,
      "conflict",
      "live_view_session_already_active",
    );
  }

  const sessionId = crypto.randomUUID();
  const now = utcNowIso();
  const inserted = await parseJsonSingle(await rest.insert("live_view_sessions", {
    id: sessionId,
    account_id: context.accountId,
    device_id: context.deviceId,
    app_instance_id: context.appInstanceId,
    host_id: context.hostId,
    requested_by: payload.requestedBy,
    source: payload.source,
    mode: effectiveMode,
    status: "pending",
    stream_transport: "webrtc",
    livekit_room_name: `lv-${sessionId}`,
    run_active_at_start: runActive,
    interaction_enabled: interactionEnabled,
    metadata_safe: {
      assignment_id: context.assignmentId,
      source: payload.source,
      requested_mode: payload.mode,
      effective_mode: effectiveMode,
    },
    expires_at: expiresAtIso(SESSION_TTL_SECONDS),
    created_at: now,
    updated_at: now,
  }));
  if (!inserted?.id) {
    throw new LiveViewActionError(502, "rpc_failed", "live_view_session_create_failed");
  }

  await insertAuditEvent(rest, {
    sessionId: inserted.id,
    eventType: "live_view_started",
    actorId: payload.actorId,
    accountId: context.accountId,
    deviceId: context.deviceId,
    appInstanceId: context.appInstanceId,
    action: "start",
    metadataSafe: {
      source: payload.source,
      mode: effectiveMode,
      run_active_at_start: runActive,
    },
  });

  return {
    reused: false,
    body: sessionToSafeResponse(inserted, context),
  };
}

export async function liveViewStop(
  payload: LiveViewStopPayload,
  rest: RestClient,
) {
  let row: Record<string, any> | null = null;
  if (payload.liveViewSessionId) {
    row = await parseJsonSingle(await rest.select("live_view_sessions", {
      select: "*",
      id: `eq.${payload.liveViewSessionId}`,
      limit: "1",
    }));
  } else if (payload.accountId) {
    row = await findActiveSessionForAccount(rest, payload.accountId);
  }
  if (!row?.id) {
    throw new LiveViewActionError(404, "session_not_found", "session_not_found");
  }
  if (!ACTIVE_SESSION_STATUSES.includes(String(row.status))) {
    const context = await loadSessionContext(rest, row);
    return {
      body: sessionToSafeResponse(row, context),
    };
  }

  const updated = await parseJsonSingle(await rest.update(
    "live_view_sessions",
    { id: `eq.${row.id}` },
    {
      status: "stopped",
      stopped_at: utcNowIso(),
      updated_at: utcNowIso(),
    },
  ));
  const finalRow = updated ?? row;
  const context = await loadSessionContext(rest, finalRow);

  await insertAuditEvent(rest, {
    sessionId: finalRow.id,
    eventType: "live_view_stopped",
    actorId: payload.actorId,
    accountId: finalRow.account_id,
    deviceId: finalRow.device_id,
    appInstanceId: finalRow.app_instance_id,
    action: "stop",
    metadataSafe: { source: "manager_row_eye" },
  });

  return { body: sessionToSafeResponse(finalRow, context) };
}

export async function liveViewStatus(
  payload: LiveViewStatusPayload,
  rest: RestClient,
) {
  const row = await findActiveSessionForAccount(rest, payload.accountId);
  if (!row) {
    return {
      body: sanitizeLiveViewPublic({
        ok: true,
        account_id: payload.accountId,
        active: false,
        status: "inactive",
      }) as Record<string, unknown>,
    };
  }
  const context = await loadSessionContext(rest, row);
  await insertAuditEvent(rest, {
    sessionId: row.id,
    eventType: "live_view_status",
    accountId: row.account_id,
    deviceId: row.device_id,
    appInstanceId: row.app_instance_id,
    action: "status",
    metadataSafe: { status: row.status },
  });
  return {
    body: {
      ...(sessionToSafeResponse(row, context) as Record<string, unknown>),
      active: true,
      account_id: payload.accountId,
    },
  };
}

type LiveKitConfig = {
  url: string;
  apiKey: string;
  apiSecret: string;
};

export function getLiveKitConfig(): LiveKitConfig | null {
  const url = (Deno.env.get("LIVEKIT_URL") || "").trim();
  const apiKey = (Deno.env.get("LIVEKIT_API_KEY") || "").trim();
  const apiSecret = (Deno.env.get("LIVEKIT_API_SECRET") || "").trim();
  if (!url || !apiKey || !apiSecret) return null;
  return { url, apiKey, apiSecret };
}

function base64UrlEncode(data: Uint8Array | string): string {
  const bytes = typeof data === "string"
    ? new TextEncoder().encode(data)
    : data;
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

async function signLiveKitJwt(
  apiKey: string,
  apiSecret: string,
  claims: Record<string, unknown>,
): Promise<string> {
  const header = { alg: "HS256", typ: "JWT" };
  const encodedHeader = base64UrlEncode(JSON.stringify(header));
  const encodedPayload = base64UrlEncode(JSON.stringify(claims));
  const signingInput = `${encodedHeader}.${encodedPayload}`;
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(apiSecret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(signingInput),
  );
  return `${signingInput}.${base64UrlEncode(new Uint8Array(signature))}`;
}

export async function createLiveKitViewerToken(options: {
  roomName: string;
  identity: string;
  name?: string;
  ttlSeconds?: number;
}): Promise<{ token: string; expiresInSeconds: number }> {
  const config = getLiveKitConfig();
  if (!config) {
    throw new LiveViewActionError(
      503,
      "livekit_not_configured",
      "livekit_not_configured",
    );
  }
  const ttlSeconds = options.ttlSeconds ?? LIVEKIT_TOKEN_TTL_SECONDS;
  const now = Math.floor(Date.now() / 1000);
  const claims = {
    iss: config.apiKey,
    sub: options.identity,
    name: options.name || options.identity,
    nbf: now,
    exp: now + ttlSeconds,
    video: {
      roomJoin: true,
      room: options.roomName,
      canPublish: false,
      canSubscribe: true,
      canPublishData: false,
    },
  };
  const token = await signLiveKitJwt(config.apiKey, config.apiSecret, claims);
  return { token, expiresInSeconds: ttlSeconds };
}

export async function liveViewToken(
  payload: LiveViewTokenPayload,
  rest: RestClient,
) {
  const row = await parseJsonSingle(await rest.select("live_view_sessions", {
    select: "*",
    id: `eq.${payload.liveViewSessionId}`,
    limit: "1",
  }));
  if (!row?.id) {
    throw new LiveViewActionError(404, "session_not_found", "session_not_found");
  }
  if (!ACTIVE_SESSION_STATUSES.includes(String(row.status))) {
    throw new LiveViewActionError(
      409,
      "session_not_active",
      "session_not_active",
    );
  }

  const config = getLiveKitConfig();
  if (!config) {
    return {
      body: {
        ok: false,
        error: {
          code: "livekit_not_configured",
          message: "LiveKit is not configured for this environment.",
        },
        live_view_session_id: row.id,
        status: row.status,
      },
    };
  }

  const roomName = String(row.livekit_room_name || `lv-${row.id}`);
  const identity = payload.actorId || `admin-${row.id.slice(0, 8)}`;
  const token = await createLiveKitViewerToken({
    roomName,
    identity,
    name: identity,
  });

  await insertAuditEvent(rest, {
    sessionId: row.id,
    eventType: "live_view_token_issued",
    actorId: payload.actorId,
    accountId: row.account_id,
    deviceId: row.device_id,
    appInstanceId: row.app_instance_id,
    action: "token",
    metadataSafe: {
      room_name: roomName,
      subscribe_only: true,
      expires_in_seconds: token.expiresInSeconds,
    },
  });

  return {
    body: sanitizeLiveViewPublic({
      ok: true,
      live_view_session_id: row.id,
      status: row.status,
      livekit_url: config.url,
      livekit_token: token.token,
      livekit_room_name: roomName,
      expires_in_seconds: token.expiresInSeconds,
      subscribe_only: true,
    }) as Record<string, unknown>,
  };
}

export async function handleLiveViewAction(
  payload: LiveViewParsedPayload,
  rest: RestClient,
) {
  if (payload.action === "live_view_start") {
    return await liveViewStart(payload, rest);
  }
  if (payload.action === "live_view_stop") {
    return await liveViewStop(payload, rest);
  }
  if (payload.action === "live_view_status") {
    return await liveViewStatus(payload, rest);
  }
  return await liveViewToken(payload, rest);
}
