// deno-lint-ignore-file no-explicit-any
/**
 * Secure Instagram credentials submit/update API.
 *
 * Passwords are accepted as write-only request fields, written to Supabase
 * Vault through a service-role RPC, and never returned or stored in app tables.
 */

type CredentialsAction =
  | "submit"
  | "update_password"
  | "submit_add_profile_credentials"
  | "status";
type CredentialWriteAction = Exclude<CredentialsAction, "status">;
type ProducerAuth =
  | { ok: true; mode: "internal"; authUserId: null }
  | { ok: true; mode: "client"; authUserId: string }
  | { ok: false; response: Response };
type VaultWriteInput = {
  accountId: string;
  version: number;
  username: string | null;
  password: string;
};
type VaultWriteResult = { secretRef: string; vaultSecretId: string };
type VaultAdapter = {
  writeInstagramCredentialsSecret(
    input: VaultWriteInput,
  ): Promise<VaultWriteResult>;
};
type Fetcher = (
  input: string | URL | Request,
  init?: RequestInit,
) => Promise<Response>;
type Dependencies = {
  fetch?: Fetcher;
  vaultAdapter?: VaultAdapter;
  log?: (event: string, payload: Record<string, unknown>) => void;
  now?: () => Date;
  requestId?: () => string;
};
type ValidPayload = {
  action: CredentialWriteAction;
  accountId: string;
  username: string | null;
  password: string;
  externalRequestId: string | null;
  metadataSafe: Record<string, unknown>;
  actorType: string | null;
  actorId: string | null;
};
type StatusPayload = {
  action: "status";
  accountId: string;
};
type ParsedPayload = ValidPayload | StatusPayload;
type AccountStatusRow = {
  client_id: string | null;
  onboarding_status: string | null;
  provisioning_status: string | null;
  login_status: string | null;
};
type CredentialStatusRow = {
  provider: string;
  credentials_version: number;
  status: string;
  reauth_required: boolean;
  reauth_reason: string | null;
  last_submitted_at: string | null;
  last_rotated_at: string | null;
};
type AccountIdentityRow = {
  id: string;
  username: string | null;
};

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const USERNAME_RE = /^[a-z0-9._]{1,30}$/;
const SAFE_REQUEST_ID_RE = /^[a-zA-Z0-9._:-]{1,120}$/;
const FORBIDDEN_TOP_LEVEL_FIELDS = new Set([
  "secret_ref",
  "secret_provider",
  "credentials_version",
  "status",
  "raw_secret",
  "token",
  "cookie",
  "webhook_url",
  "service_role",
  "password_confirm",
  "encrypted_password",
]);
const ADD_PROFILE_ACTOR_TYPES = new Set(["admin", "backend", "client", "system"]);
const STATUS_FORBIDDEN_TOP_LEVEL_FIELDS = new Set([
  "password",
  "secret_ref",
  "secret_provider",
  "credentials_version",
  "status",
  "metadata",
  "raw_secret",
  "secret",
  "token",
  "cookie",
  "webhook_url",
  "service_role",
  "password_confirm",
  "encrypted_password",
]);
const FORBIDDEN_METADATA_FIELDS = new Set([
  "password",
  "raw_secret",
  "secret",
  "token",
  "cookie",
  "webhook_url",
  "service_role",
]);

export function normalizeUsername(value: unknown): string | null {
  if (value == null || String(value).trim() === "") return null;
  return String(value).trim().replace(/^@+/, "").toLowerCase();
}

export function validateUsername(
  value: unknown,
): { ok: true; username: string | null } | {
  ok: false;
  error: string;
} {
  const username = normalizeUsername(value);
  if (username == null) return { ok: true, username: null };
  if (!USERNAME_RE.test(username)) {
    return { ok: false, error: "invalid_username" };
  }
  if (username.includes("..")) return { ok: false, error: "invalid_username" };
  return { ok: true, username };
}

export function validateForbiddenTopLevelFields(
  payload: Record<string, unknown>,
): string | null {
  for (const [key, value] of Object.entries(payload || {})) {
    if (FORBIDDEN_TOP_LEVEL_FIELDS.has(key)) return key;
    if (key === "metadata" && value && typeof value === "object") {
      const nested = validateSafeMetadataObject(value);
      if (nested) return `${key}.${nested}`;
    }
  }
  return null;
}

export function validateSafeMetadataObject(
  value: unknown,
  prefix = "",
): string | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return "invalid";
  }
  for (
    const [key, nestedValue] of Object.entries(value as Record<string, unknown>)
  ) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (FORBIDDEN_METADATA_FIELDS.has(key.toLowerCase())) return path;
    if (nestedValue && typeof nestedValue === "object") {
      if (Array.isArray(nestedValue)) {
        for (let i = 0; i < nestedValue.length; i += 1) {
          const item = nestedValue[i];
          if (item && typeof item === "object") {
            const nested = validateSafeMetadataObject(item, `${path}.${i}`);
            if (nested) return nested;
          }
        }
      } else {
        const nested = validateSafeMetadataObject(nestedValue, path);
        if (nested) return nested;
      }
    }
  }
  return null;
}

export function validateStatusForbiddenFields(
  payload: Record<string, unknown>,
): string | null {
  for (const key of Object.keys(payload || {})) {
    if (STATUS_FORBIDDEN_TOP_LEVEL_FIELDS.has(key)) return key;
  }
  return null;
}

export function validatePayload(
  payload: Record<string, unknown>,
): { ok: true; payload: ParsedPayload } | {
  ok: false;
  error: string;
  status?: number;
} {
  const action = String(payload.action || "").trim();
  if (
    action !== "submit" &&
    action !== "update_password" &&
    action !== "submit_add_profile_credentials" &&
    action !== "status"
  ) {
    return { ok: false, error: "invalid_action", status: 400 };
  }
  if (!isUuid(payload.account_id)) {
    return { ok: false, error: "account_id_invalid", status: 400 };
  }
  const accountId = String(payload.account_id).trim();

  if (action === "status") {
    const forbidden = validateStatusForbiddenFields(payload);
    if (forbidden) {
      return { ok: false, error: `field_forbidden:${forbidden}`, status: 400 };
    }
    return {
      ok: true,
      payload: {
        action,
        accountId,
      },
    };
  }

  const forbidden = validateForbiddenTopLevelFields(payload);
  if (forbidden) {
    return { ok: false, error: `field_forbidden:${forbidden}`, status: 400 };
  }

  const rawPassword = payload.password;
  if (
    typeof rawPassword !== "string" || rawPassword.length < 6 ||
    rawPassword.trim().length < 6
  ) {
    return { ok: false, error: "password_invalid", status: 400 };
  }

  const usernameInput = action === "submit_add_profile_credentials"
    ? (payload.expected_username == null ||
        String(payload.expected_username).trim() === ""
      ? payload.username
      : payload.expected_username)
    : payload.username;
  const usernameResult = validateUsername(usernameInput);
  if (!usernameResult.ok) {
    return { ok: false, error: usernameResult.error, status: 400 };
  }
  if (action === "submit_add_profile_credentials" && !usernameResult.username) {
    return { ok: false, error: "expected_username_required", status: 400 };
  }

  const metadataSafe = payload.metadata_safe == null
    ? {}
    : payload.metadata_safe;
  const metadataError = validateSafeMetadataObject(metadataSafe);
  if (metadataError) {
    return {
      ok: false,
      error: `metadata_safe_forbidden:${metadataError}`,
      status: 400,
    };
  }

  const actorTypeRaw = payload.actor_type == null
    ? null
    : String(payload.actor_type).trim().toLowerCase();
  if (
    action === "submit_add_profile_credentials" &&
    actorTypeRaw != null &&
    actorTypeRaw !== "" &&
    !ADD_PROFILE_ACTOR_TYPES.has(actorTypeRaw)
  ) {
    return { ok: false, error: "actor_type_invalid", status: 400 };
  }
  const actorIdRaw = payload.actor_id == null
    ? null
    : String(payload.actor_id).trim();
  if (actorIdRaw != null && actorIdRaw.length > 120) {
    return { ok: false, error: "actor_id_invalid", status: 400 };
  }

  const rawExternal = payload.external_request_id == null
    ? null
    : String(payload.external_request_id).trim();
  if (
    rawExternal != null && rawExternal !== "" &&
    !SAFE_REQUEST_ID_RE.test(rawExternal)
  ) {
    return { ok: false, error: "external_request_id_invalid", status: 400 };
  }

  return {
    ok: true,
    payload: {
      action,
      accountId,
      username: usernameResult.username,
      password: rawPassword,
      externalRequestId: rawExternal || null,
      metadataSafe: metadataSafe as Record<string, unknown>,
      actorType: actorTypeRaw || null,
      actorId: actorIdRaw || null,
    },
  };
}

export function parseVaultSecretRef(secretRef: string): string | null {
  const prefix = "supabase_vault://";
  if (!secretRef.startsWith(prefix)) return null;
  const id = secretRef.slice(prefix.length).trim();
  return isUuid(id) ? id : null;
}

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

function corsHeaders(req: Request): HeadersInit {
  const allowed = (Deno.env.get("INSTAGRAM_CREDENTIALS_ALLOWED_ORIGINS") || "")
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

function isUuid(value: unknown): value is string {
  return typeof value === "string" && UUID_RE.test(value.trim());
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

async function verifyClientJwt(
  token: string,
  deps: Dependencies,
): Promise<string | null> {
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

async function assertAuthenticated(
  req: Request,
  deps: Dependencies,
): Promise<ProducerAuth> {
  const expected =
    (Deno.env.get("INSTAGRAM_CREDENTIALS_INTERNAL_API_TOKEN") || "").trim();
  const auth = req.headers.get("authorization") || "";
  const got = auth.toLowerCase().startsWith("bearer ")
    ? auth.slice(7).trim()
    : "";
  const headers = corsHeaders(req);
  if (!got) {
    return {
      ok: false,
      response: jsonResponse(
        401,
        { ok: false, error: "unauthorized" },
        headers,
      ),
    };
  }
  if (expected && got === expected) {
    return { ok: true, mode: "internal", authUserId: null };
  }
  const authUserId = await verifyClientJwt(got, deps);
  if (!authUserId) {
    return {
      ok: false,
      response: jsonResponse(
        401,
        { ok: false, error: "unauthorized" },
        headers,
      ),
    };
  }
  return { ok: true, mode: "client", authUserId };
}

async function supabaseFetch(
  path: string,
  init: RequestInit = {},
  deps: Dependencies = {},
) {
  const url = requireEnv("SUPABASE_URL").replace(/\/+$/, "");
  const key = requireEnv("SUPABASE_SERVICE_ROLE_KEY");
  const headers = new Headers(init.headers || {});
  headers.set("apikey", key);
  headers.set("authorization", `Bearer ${key}`);
  if (!headers.has("content-type") && init.body) {
    headers.set("content-type", "application/json");
  }
  const fetcher = deps.fetch ?? fetch;
  return await fetcher(`${url}${path}`, { ...init, headers });
}

async function supabaseJson(
  path: string,
  init: RequestInit = {},
  deps: Dependencies = {},
): Promise<any> {
  const res = await supabaseFetch(path, init, deps);
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) throw new Error(`supabase_error:${res.status}`);
  return body;
}

async function rpcBoolean(
  path: string,
  payload: Record<string, unknown>,
  deps: Dependencies,
): Promise<boolean> {
  const result = await supabaseJson(path, {
    method: "POST",
    body: JSON.stringify(payload),
  }, deps);
  return result === true;
}

async function hasClientAccountAccess(
  authUserId: string,
  accountId: string,
  deps: Dependencies,
): Promise<boolean> {
  return await rpcBoolean("/rest/v1/rpc/client_can_manage_instagram_account", {
    p_auth_user_id: authUserId,
    p_account_id: accountId,
  }, deps);
}

async function getClientIdForAccount(
  accountId: string,
  deps: Dependencies,
): Promise<string | null> {
  const result = await supabaseJson(
    "/rest/v1/rpc/client_id_for_instagram_account",
    {
      method: "POST",
      body: JSON.stringify({ p_account_id: accountId }),
    },
    deps,
  );
  return isUuid(result) ? result : null;
}

async function getClientAccountStatus(
  accountId: string,
  deps: Dependencies,
): Promise<AccountStatusRow | null> {
  const rows = await supabaseJson(
    `/rest/v1/client_instagram_accounts?account_id=eq.${
      encodeURIComponent(accountId)
    }&select=client_id,onboarding_status,provisioning_status,login_status&limit=1`,
    { method: "GET" },
    deps,
  );
  if (!Array.isArray(rows) || !rows[0]) return null;
  const row = rows[0];
  return {
    client_id: isUuid(row.client_id) ? row.client_id : null,
    onboarding_status: typeof row.onboarding_status === "string"
      ? row.onboarding_status
      : null,
    provisioning_status: typeof row.provisioning_status === "string"
      ? row.provisioning_status
      : null,
    login_status: typeof row.login_status === "string"
      ? row.login_status
      : null,
  };
}

async function getAccountIdentity(
  accountId: string,
  deps: Dependencies,
): Promise<AccountIdentityRow | null> {
  const rows = await supabaseJson(
    `/rest/v1/ig_accounts?id=eq.${
      encodeURIComponent(accountId)
    }&select=id,username&limit=1`,
    { method: "GET" },
    deps,
  );
  if (!Array.isArray(rows) || !rows[0]) return null;
  const row = rows[0];
  return {
    id: typeof row.id === "string" ? row.id : accountId,
    username: typeof row.username === "string"
      ? normalizeUsername(row.username)
      : null,
  };
}

async function getActiveCredentialStatus(
  accountId: string,
  deps: Dependencies,
): Promise<CredentialStatusRow | null> {
  const rows = await supabaseJson(
    `/rest/v1/account_credentials?account_id=eq.${
      encodeURIComponent(accountId)
    }&provider=eq.instagram&status=eq.active&select=provider,credentials_version,status,reauth_required,reauth_reason,last_submitted_at,last_rotated_at&limit=1`,
    { method: "GET" },
    deps,
  );
  if (!Array.isArray(rows) || !rows[0]) return null;
  const row = rows[0];
  return {
    provider: "instagram",
    credentials_version: Number(row.credentials_version),
    status: typeof row.status === "string" ? row.status : "active",
    reauth_required: row.reauth_required === true,
    reauth_reason: typeof row.reauth_reason === "string"
      ? row.reauth_reason
      : null,
    last_submitted_at: typeof row.last_submitted_at === "string"
      ? row.last_submitted_at
      : null,
    last_rotated_at: typeof row.last_rotated_at === "string"
      ? row.last_rotated_at
      : null,
  };
}

async function maxCredentialsVersion(
  accountId: string,
  deps: Dependencies,
): Promise<number> {
  const rows = await supabaseJson(
    `/rest/v1/account_credentials?account_id=eq.${
      encodeURIComponent(accountId)
    }&provider=eq.instagram&select=credentials_version&order=credentials_version.desc&limit=1`,
    { method: "GET" },
    deps,
  );
  const raw = Array.isArray(rows) && rows[0]
    ? Number(rows[0].credentials_version)
    : 0;
  return Number.isFinite(raw) && raw > 0 ? Math.trunc(raw) : 0;
}

export function deriveNextAction(input: {
  credentialsConfigured: boolean;
  reauthRequired: boolean;
  reauthReason: string | null;
  loginStatus: string | null;
}): string {
  if (!input.credentialsConfigured) return "submit_credentials";
  if (input.loginStatus === "needs_2fa") return "complete_2fa";
  if (input.loginStatus === "checkpoint") return "resolve_checkpoint";
  if (input.loginStatus === "mismatch") return "contact_support";
  if (input.loginStatus === "failed") return "update_password";
  if (
    input.reauthRequired && input.reauthReason === "awaiting_login_verification"
  ) {
    return "awaiting_login_verification";
  }
  if (input.reauthRequired) return "update_password";
  if (input.loginStatus === "connected") return "none";
  return "awaiting_login_verification";
}

export function safeClientMessageForNextAction(nextAction: string): string {
  switch (nextAction) {
    case "submit_credentials":
      return "Instagram credentials are required.";
    case "awaiting_login_verification":
      return "Credentials saved. Login verification is pending.";
    case "update_password":
      return "Please update your Instagram password.";
    case "complete_2fa":
      return "Two-factor authentication is required.";
    case "resolve_checkpoint":
      return "Instagram requires a checkpoint verification.";
    case "contact_support":
      return "Your account requires review by support.";
    case "none":
      return "Instagram connection is active.";
    default:
      return "Credentials saved. Login verification is pending.";
  }
}

async function rotateMetadata(input: {
  payload: ValidPayload;
  auth: Extract<ProducerAuth, { ok: true }>;
  clientId: string | null;
  secretRef: string;
  credentialsVersion: number;
  requestId: string;
}, deps: Dependencies): Promise<Record<string, any>> {
  const isAddProfile =
    input.payload.action === "submit_add_profile_credentials";
  const row = await supabaseJson(
    "/rest/v1/rpc/rotate_instagram_account_credentials",
    {
      method: "POST",
      body: JSON.stringify({
        p_account_id: input.payload.accountId,
        p_client_id: input.clientId,
        p_username_at_submission: input.payload.username,
        p_secret_ref: input.secretRef,
        p_secret_provider: "supabase_vault",
        p_credentials_version: input.credentialsVersion,
        p_action: isAddProfile ? "submit" : input.payload.action,
        p_external_request_id: input.payload.externalRequestId,
        p_request_id: input.requestId,
        p_submitted_by: input.auth.mode === "client"
          ? input.auth.authUserId
          : null,
        p_submitted_via: isAddProfile
          ? "add_profile"
          : input.auth.mode === "client"
          ? "client_dashboard"
          : "api",
      }),
    },
    deps,
  );
  if (!row || !row.id) throw new Error("metadata_write_failed");
  return row;
}

async function syncCredentialDashboardAction(input: {
  payload: ValidPayload;
  clientId: string | null;
  credentialsVersion: number;
  requestId: string;
}, deps: Dependencies): Promise<void> {
  const isPasswordUpdate = input.payload.action === "update_password";
  const isAddProfile =
    input.payload.action === "submit_add_profile_credentials";
  const actionType = isPasswordUpdate
    ? "update_instagram_password"
    : "submit_instagram_credentials";
  const metadata: Record<string, unknown> = {
    ...input.payload.metadataSafe,
    source: isAddProfile ? "add_profile" : "instagram_credentials",
    action: isAddProfile ? "submit" : input.payload.action,
    credentials_version: input.credentialsVersion,
    request_id: input.requestId,
  };
  if (isAddProfile) {
    metadata.actor_type = input.payload.actorType || "backend";
    if (input.payload.actorId) metadata.actor_id = input.payload.actorId;
  }
  if (input.payload.externalRequestId) {
    metadata.external_request_id = input.payload.externalRequestId;
  }

  await supabaseJson("/rest/v1/rpc/upsert_account_dashboard_action", {
    method: "POST",
    body: JSON.stringify({
      p_account_id: input.payload.accountId,
      p_client_id: input.clientId,
      p_incident_id: null,
      p_action_type: actionType,
      p_status: "pending_verification",
      p_severity: "info",
      p_audience: "client",
      p_requires_client_action: false,
      p_blocking_campaign: true,
      p_title: isPasswordUpdate
        ? "Mot de passe Instagram en vérification"
        : "Connexion Instagram en vérification",
      p_safe_client_message: isPasswordUpdate
        ? "Votre mot de passe a été mis à jour. Nous vérifions maintenant la connexion."
        : "Vos identifiants Instagram ont été enregistrés. Nous vérifions maintenant la connexion.",
      p_assistant_message: null,
      p_admin_message: null,
      p_action_label: "Voir le statut",
      p_action_deep_link: isPasswordUpdate
        ? `/accounts/${input.payload.accountId}/credentials#password`
        : `/accounts/${input.payload.accountId}/connect-instagram`,
      p_dedupe_key:
        `account:${input.payload.accountId}:dashboard_action:${actionType}`,
      p_metadata: metadata,
    }),
  }, deps);
}

async function syncAddProfileAccountStatusIfPresent(input: {
  payload: ValidPayload;
  requestId: string;
}, deps: Dependencies): Promise<void> {
  if (input.payload.action !== "submit_add_profile_credentials") return;
  const current = await getClientAccountStatus(input.payload.accountId, deps);
  if (!current) return;
  await supabaseJson("/rest/v1/rpc/update_client_instagram_account_status", {
    method: "POST",
    body: JSON.stringify({
      p_account_id: input.payload.accountId,
      p_login_status: "verification_pending",
      p_provisioning_status: "login_pending",
      p_onboarding_status: "credentials_submitted",
      p_reauth_required: true,
      p_reauth_reason: "awaiting_login_verification",
      p_actor_type: "internal",
      p_reason: "add_profile_credentials_submitted",
      p_external_request_id: input.payload.externalRequestId,
      p_metadata: {
        ...input.payload.metadataSafe,
        source: "add_profile",
        action: "submit",
        request_id: input.requestId,
        actor_type: input.payload.actorType || "backend",
      },
    }),
  }, deps);
}

function defaultVaultAdapter(deps: Dependencies): VaultAdapter {
  return {
    async writeInstagramCredentialsSecret(
      input: VaultWriteInput,
    ): Promise<VaultWriteResult> {
      const now = (deps.now?.() ?? new Date()).toISOString();
      const secretPayload = JSON.stringify({
        provider: "instagram",
        username: input.username,
        password: input.password,
        account_id: input.accountId,
        credentials_version: input.version,
        created_at: now,
      });
      const name =
        `phonefarm/instagram/${input.accountId}/credentials/v${input.version}`;
      const description =
        `Phone Farm Instagram credentials for account ${input.accountId} version ${input.version}`;
      const vaultSecretId = await supabaseJson(
        "/rest/v1/rpc/create_instagram_credentials_vault_secret",
        {
          method: "POST",
          body: JSON.stringify({
            p_secret_payload: secretPayload,
            p_secret_name: name,
            p_secret_description: description,
          }),
        },
        deps,
      );
      if (!isUuid(vaultSecretId)) throw new Error("vault_secret_id_invalid");
      return {
        vaultSecretId,
        secretRef: `supabase_vault://${vaultSecretId}`,
      };
    },
  };
}

async function validateAccess(
  payload: { accountId: string },
  auth: Extract<ProducerAuth, { ok: true }>,
  deps: Dependencies,
): Promise<
  { ok: true; clientId: string | null } | {
    ok: false;
    status: number;
    error: string;
  }
> {
  try {
    if (auth.mode === "client") {
      const hasAccess = await hasClientAccountAccess(
        auth.authUserId,
        payload.accountId,
        deps,
      );
      if (!hasAccess) {
        return { ok: false, status: 403, error: "account_not_allowed" };
      }
    }
    const clientId = await getClientIdForAccount(payload.accountId, deps);
    if (!clientId) {
      return { ok: false, status: 403, error: "account_not_allowed" };
    }
    return { ok: true, clientId };
  } catch {
    return { ok: false, status: 503, error: "account_ownership_check_failed" };
  }
}

async function validateAddProfileAccess(
  payload: ValidPayload,
  auth: Extract<ProducerAuth, { ok: true }>,
  deps: Dependencies,
): Promise<
  { ok: true; clientId: string | null } | {
    ok: false;
    status: number;
    error: string;
  }
> {
  if (auth.mode !== "internal") {
    return { ok: false, status: 403, error: "internal_token_required" };
  }
  try {
    const account = await getAccountIdentity(payload.accountId, deps);
    if (!account) {
      return { ok: false, status: 404, error: "account_not_found" };
    }
    if (
      account.username && payload.username &&
      account.username !== payload.username
    ) {
      return { ok: false, status: 409, error: "expected_username_mismatch" };
    }
    return {
      ok: true,
      clientId: await getClientIdForAccount(payload.accountId, deps),
    };
  } catch {
    return { ok: false, status: 503, error: "account_lookup_failed" };
  }
}

async function handleCredentialsStatus(
  req: Request,
  payload: StatusPayload,
  auth: Extract<ProducerAuth, { ok: true }>,
  deps: Dependencies,
) {
  const headers = corsHeaders(req);
  const rid = requestId(req, deps);
  const access = await validateAccess(payload, auth, deps);
  if (!access.ok) {
    logEvent(deps, "instagram_credentials_status_rejected", {
      request_id: rid,
      account_id: payload.accountId,
      action: payload.action,
      error: access.error,
    });
    return jsonResponse(access.status, {
      ok: false,
      error: access.error,
      request_id: rid,
    }, headers);
  }

  let accountStatus: AccountStatusRow | null;
  let credentialStatus: CredentialStatusRow | null;
  try {
    accountStatus = await getClientAccountStatus(payload.accountId, deps);
    credentialStatus = await getActiveCredentialStatus(payload.accountId, deps);
  } catch {
    return jsonResponse(500, {
      ok: false,
      error: "credentials_status_read_failed",
      request_id: rid,
    }, headers);
  }

  const credentialsConfigured = credentialStatus !== null;
  const nextAction = deriveNextAction({
    credentialsConfigured,
    reauthRequired: credentialStatus?.reauth_required === true,
    reauthReason: credentialStatus?.reauth_reason ?? null,
    loginStatus: accountStatus?.login_status ?? null,
  });

  return jsonResponse(200, {
    ok: true,
    request_id: rid,
    account_id: payload.accountId,
    provider: "instagram",
    credentials_configured: credentialsConfigured,
    credentials_version: credentialStatus?.credentials_version ?? null,
    credentials_status: credentialStatus?.status ?? null,
    reauth_required: credentialStatus?.reauth_required ?? false,
    reauth_reason: credentialStatus?.reauth_reason ?? null,
    last_submitted_at: credentialStatus?.last_submitted_at ?? null,
    last_rotated_at: credentialStatus?.last_rotated_at ?? null,
    onboarding_status: accountStatus?.onboarding_status ?? null,
    provisioning_status: accountStatus?.provisioning_status ?? null,
    login_status: accountStatus?.login_status ?? null,
    next_action: nextAction,
    safe_client_message: safeClientMessageForNextAction(nextAction),
  }, headers);
}

async function handleCredentialsSubmit(
  req: Request,
  payload: ValidPayload,
  auth: Extract<ProducerAuth, { ok: true }>,
  deps: Dependencies,
) {
  const headers = corsHeaders(req);
  const rid = requestId(req, deps);
  const access = payload.action === "submit_add_profile_credentials"
    ? await validateAddProfileAccess(payload, auth, deps)
    : await validateAccess(payload, auth, deps);
  if (!access.ok) {
    logEvent(deps, "instagram_credentials_rejected", {
      request_id: rid,
      account_id: payload.accountId,
      action: payload.action,
      error: access.error,
    });
    return jsonResponse(access.status, {
      ok: false,
      error: access.error,
      request_id: rid,
    }, headers);
  }

  let version: number;
  try {
    version = (await maxCredentialsVersion(payload.accountId, deps)) + 1;
  } catch {
    return jsonResponse(500, {
      ok: false,
      error: "credentials_metadata_write_failed",
      request_id: rid,
    }, headers);
  }

  let vaultResult: VaultWriteResult;
  try {
    const vaultAdapter = deps.vaultAdapter ?? defaultVaultAdapter(deps);
    vaultResult = await vaultAdapter.writeInstagramCredentialsSecret({
      accountId: payload.accountId,
      version,
      username: payload.username,
      password: payload.password,
    });
    if (!parseVaultSecretRef(vaultResult.secretRef)) {
      throw new Error("secret_ref_invalid");
    }
  } catch {
    logEvent(deps, "instagram_credentials_secret_write_failed", {
      request_id: rid,
      account_id: payload.accountId,
      action: payload.action,
      version,
      error: "secret_write_failed",
    });
    return jsonResponse(500, {
      ok: false,
      error: "secret_write_failed",
      request_id: rid,
    }, headers);
  }

  let row: Record<string, any>;
  try {
    row = await rotateMetadata({
      payload,
      auth,
      clientId: access.clientId,
      secretRef: vaultResult.secretRef,
      credentialsVersion: version,
      requestId: rid,
    }, deps);
  } catch {
    logEvent(deps, "instagram_credentials_metadata_write_failed", {
      request_id: rid,
      account_id: payload.accountId,
      action: payload.action,
      version,
      error: "credentials_metadata_write_failed",
    });
    return jsonResponse(500, {
      ok: false,
      error: "credentials_metadata_write_failed",
      request_id: rid,
    }, headers);
  }

  logEvent(deps, "instagram_credentials_completed", {
    request_id: rid,
    account_id: payload.accountId,
    action: payload.action,
    credentials_version: row.credentials_version,
    submitted_via: row.submitted_via,
    ok: true,
  });
  try {
    await syncCredentialDashboardAction({
      payload,
      clientId: access.clientId,
      credentialsVersion: Number(row.credentials_version),
      requestId: rid,
    }, deps);
  } catch {
    logEvent(deps, "instagram_credentials_dashboard_action_sync_failed", {
      request_id: rid,
      account_id: payload.accountId,
      action: payload.action,
      credentials_version: row.credentials_version,
      error: "dashboard_action_sync_failed",
    });
  }
  try {
    await syncAddProfileAccountStatusIfPresent(
      { payload, requestId: rid },
      deps,
    );
  } catch {
    logEvent(deps, "instagram_credentials_account_status_sync_failed", {
      request_id: rid,
      account_id: payload.accountId,
      action: payload.action,
      credentials_version: row.credentials_version,
      error: "account_status_sync_failed",
    });
  }
  return jsonResponse(200, {
    ok: true,
    request_id: rid,
    account_id: row.account_id,
    provider: "instagram",
    credentials_version: row.credentials_version,
    credentials_status: row.status,
    status: row.status,
    reauth_required: row.reauth_required,
    next_action: "awaiting_login_verification",
    password_status: "write_only",
  }, headers);
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
    return jsonResponse(
      405,
      { ok: false, error: "method_not_allowed" },
      headers,
    );
  }

  const auth = await assertAuthenticated(req, deps);
  if (!auth.ok) return auth.response;

  let rawPayload: Record<string, unknown>;
  try {
    rawPayload = await req.json();
  } catch {
    return jsonResponse(400, { ok: false, error: "invalid_json" }, headers);
  }
  if (
    !rawPayload || typeof rawPayload !== "object" || Array.isArray(rawPayload)
  ) {
    return jsonResponse(
      400,
      { ok: false, error: "payload_must_be_object" },
      headers,
    );
  }

  const payloadResult = validatePayload(rawPayload);
  if (!payloadResult.ok) {
    return jsonResponse(payloadResult.status || 400, {
      ok: false,
      error: payloadResult.error,
    }, headers);
  }

  try {
    if (payloadResult.payload.action === "status") {
      return await handleCredentialsStatus(
        req,
        payloadResult.payload,
        auth,
        deps,
      );
    }
    return await handleCredentialsSubmit(
      req,
      payloadResult.payload,
      auth,
      deps,
    );
  } catch {
    logEvent(deps, "instagram_credentials_unhandled_error", {
      request_id: requestId(req, deps),
      account_id: payloadResult.payload.accountId,
      action: payloadResult.payload.action,
      error: "internal_error",
    });
    return jsonResponse(500, { ok: false, error: "internal_error" }, headers);
  }
}

if (import.meta.main) {
  Deno.serve((req) => handleRequest(req));
}
