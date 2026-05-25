import {
  deriveNextAction,
  handleRequest,
  normalizeUsername,
  parseVaultSecretRef,
  safeClientMessageForNextAction,
  validateForbiddenTopLevelFields,
  validatePayload,
  validateStatusForbiddenFields,
} from "./index.ts";

const ACCOUNT_ID = "42c625c2-e761-4100-8a9d-7ae1373de97d";
const CLIENT_ID = "00000000-0000-4000-8000-000000002e2a";
const AUTH_USER_ID = "00000000-0000-4000-8000-000000000001";
const VAULT_ID = "11111111-1111-4111-8111-111111111111";
const FAKE_PASSWORD = "not-real-password";

type FetchCall = { url: string; body: Record<string, unknown> | null };

function withEnv(fn: () => Promise<void> | void) {
  return async () => {
    const previous = {
      SUPABASE_URL: Deno.env.get("SUPABASE_URL"),
      SUPABASE_SERVICE_ROLE_KEY: Deno.env.get("SUPABASE_SERVICE_ROLE_KEY"),
      INSTAGRAM_CREDENTIALS_INTERNAL_API_TOKEN: Deno.env.get("INSTAGRAM_CREDENTIALS_INTERNAL_API_TOKEN"),
    };
    Deno.env.set("SUPABASE_URL", "https://example.supabase.co");
    Deno.env.set("SUPABASE_SERVICE_ROLE_KEY", "service-role-not-real");
    Deno.env.set("INSTAGRAM_CREDENTIALS_INTERNAL_API_TOKEN", "internal-token-not-real");
    try {
      await fn();
    } finally {
      for (const [key, value] of Object.entries(previous)) {
        if (value == null) Deno.env.delete(key);
        else Deno.env.set(key, value);
      }
    }
  };
}

function request(body: Record<string, unknown>, token = "client-jwt-not-real") {
  return new Request("https://example.supabase.co/functions/v1/instagram-credentials", {
    method: "POST",
    headers: {
      "authorization": `Bearer ${token}`,
      "content-type": "application/json",
      "x-request-id": "req-test-1",
    },
    body: JSON.stringify(body),
  });
}

function validBody(overrides: Record<string, unknown> = {}) {
  return {
    action: "submit",
    account_id: ACCOUNT_ID,
    username: " @Muse_Europe ",
    password: FAKE_PASSWORD,
    external_request_id: "entry2d2b-test",
    ...overrides,
  };
}

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function makeFetch(options: {
  clientAccess?: boolean;
  clientId?: string | null;
  accountStatus?: Record<string, unknown> | null;
  activeCredential?: Record<string, unknown> | null;
  maxVersion?: number;
  rotateStatus?: number;
  rotateRow?: Record<string, unknown>;
  calls?: FetchCall[];
} = {}) {
  const calls = options.calls ?? [];
  return async (url: string | URL | Request, init?: RequestInit): Promise<Response> => {
    const href = String(url);
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ url: href, body });

    if (href.endsWith("/auth/v1/user")) {
      const auth = new Headers(init?.headers).get("authorization") || "";
      if (auth === "Bearer client-jwt-not-real") return json({ id: AUTH_USER_ID });
      return json({ error: "unauthorized" }, 401);
    }
    if (href.includes("/rest/v1/rpc/client_can_manage_instagram_account")) {
      return json(options.clientAccess ?? true);
    }
    if (href.includes("/rest/v1/rpc/client_id_for_instagram_account")) {
      return json(options.clientId === undefined ? CLIENT_ID : options.clientId);
    }
    if (href.includes("/rest/v1/client_instagram_accounts?")) {
      if (options.accountStatus === null) return json([]);
      return json([options.accountStatus ?? {
        client_id: CLIENT_ID,
        onboarding_status: "configured",
        provisioning_status: "pending",
        login_status: "pending",
      }]);
    }
    if (href.includes("/rest/v1/account_credentials?")) {
      if (href.includes("status=eq.active")) {
        if (options.activeCredential === null) return json([]);
        return json([options.activeCredential ?? {
          provider: "instagram",
          credentials_version: options.maxVersion ?? 1,
          status: "active",
          reauth_required: true,
          reauth_reason: "awaiting_login_verification",
          last_submitted_at: "2026-05-25T18:00:00Z",
          last_rotated_at: null,
          metadata: { password: "must-not-return" },
          secret_ref: `supabase_vault://${VAULT_ID}`,
        }]);
      }
      const max = options.maxVersion ?? 0;
      return json(max > 0 ? [{ credentials_version: max }] : []);
    }
    if (href.includes("/rest/v1/rpc/rotate_instagram_account_credentials")) {
      if (options.rotateStatus && options.rotateStatus >= 400) {
        return json({ error: "metadata failed" }, options.rotateStatus);
      }
      return json(options.rotateRow ?? {
        id: "22222222-2222-4222-8222-222222222222",
        account_id: ACCOUNT_ID,
        credentials_version: (options.maxVersion ?? 0) + 1,
        status: "active",
        reauth_required: true,
        submitted_via: body?.p_submitted_via ?? "client_dashboard",
      });
    }
    return json({ error: `unexpected ${href}` }, 500);
  };
}

function statusBody(overrides: Record<string, unknown> = {}) {
  return {
    action: "status",
    account_id: ACCOUNT_ID,
    ...overrides,
  };
}

function mockVault(calls: VaultWriteInput[] = []) {
  return {
    async writeInstagramCredentialsSecret(input: VaultWriteInput) {
      calls.push(input);
      return { vaultSecretId: VAULT_ID, secretRef: `supabase_vault://${VAULT_ID}` };
    },
  };
}

type VaultWriteInput = {
  accountId: string;
  version: number;
  username: string | null;
  password: string;
};

Deno.test("rejects missing auth", withEnv(async () => {
  const res = await handleRequest(new Request("https://example.test", {
    method: "POST",
    body: JSON.stringify(validBody()),
  }));
  const body = await res.json();
  if (res.status !== 401 || body.error !== "unauthorized") {
    throw new Error("missing auth was not rejected");
  }
}));

Deno.test("status rejects unauthenticated request", withEnv(async () => {
  const res = await handleRequest(new Request("https://example.test", {
    method: "POST",
    body: JSON.stringify(statusBody()),
  }));
  const body = await res.json();
  if (res.status !== 401 || body.error !== "unauthorized") {
    throw new Error("status missing auth was not rejected");
  }
}));

Deno.test("rejects invalid action", withEnv(() => {
  const result = validatePayload(validBody({ action: "rotate" }));
  if (result.ok || result.error !== "invalid_action") throw new Error("invalid action accepted");
}));

Deno.test("status rejects invalid account_id", withEnv(() => {
  const result = validatePayload(statusBody({ account_id: "not-a-uuid" }));
  if (result.ok || result.error !== "account_id_invalid") {
    throw new Error("status invalid account_id accepted");
  }
}));

Deno.test("rejects missing password", withEnv(() => {
  const result = validatePayload(validBody({ password: "" }));
  if (result.ok || result.error !== "password_invalid") throw new Error("missing password accepted");
}));

Deno.test("rejects forbidden fields", withEnv(() => {
  const top = validateForbiddenTopLevelFields(validBody({ secret_ref: "x" }));
  if (top !== "secret_ref") throw new Error("secret_ref was not rejected");
  const nested = validateForbiddenTopLevelFields(validBody({ metadata: { password: "x" } }));
  if (nested !== "metadata.password") throw new Error("metadata.password was not rejected");
}));

Deno.test("status rejects forbidden password and secret fields", withEnv(() => {
  const password = validateStatusForbiddenFields(statusBody({ password: FAKE_PASSWORD }));
  if (password !== "password") throw new Error("status password was not rejected");
  const metadata = validateStatusForbiddenFields(statusBody({ metadata: { safe: "nope" } }));
  if (metadata !== "metadata") throw new Error("status metadata was not rejected");
  const secretRef = validateStatusForbiddenFields(statusBody({ secret_ref: `supabase_vault://${VAULT_ID}` }));
  if (secretRef !== "secret_ref") throw new Error("status secret_ref was not rejected");
}));

Deno.test("client JWT ownership false returns 403", withEnv(async () => {
  const res = await handleRequest(request(validBody()), {
    fetch: makeFetch({ clientAccess: false }),
    vaultAdapter: mockVault(),
    log: () => {},
  });
  const body = await res.json();
  if (res.status !== 403 || body.error !== "account_not_allowed") {
    throw new Error("ownership false did not return account_not_allowed");
  }
}));

Deno.test("status ownership false returns 403", withEnv(async () => {
  const res = await handleRequest(request(statusBody()), {
    fetch: makeFetch({ clientAccess: false }),
    vaultAdapter: mockVault(),
    log: () => {},
  });
  const body = await res.json();
  if (res.status !== 403 || body.error !== "account_not_allowed") {
    throw new Error("status ownership false did not return account_not_allowed");
  }
}));

Deno.test("status no active credentials returns submit_credentials", withEnv(async () => {
  const res = await handleRequest(request(statusBody()), {
    fetch: makeFetch({ activeCredential: null }),
    vaultAdapter: mockVault(),
    log: () => {},
  });
  const body = await res.json();
  if (res.status !== 200 || body.credentials_configured !== false) {
    throw new Error("status without active credentials did not return configured=false");
  }
  if (body.next_action !== "submit_credentials" || body.safe_client_message !== "Instagram credentials are required.") {
    throw new Error("status without credentials did not request submit_credentials");
  }
}));

Deno.test("status active credentials returns safe version status and timestamps", withEnv(async () => {
  const res = await handleRequest(request(statusBody()), {
    fetch: makeFetch({
      activeCredential: {
        provider: "instagram",
        credentials_version: 2,
        status: "active",
        reauth_required: true,
        reauth_reason: "awaiting_login_verification",
        last_submitted_at: "2026-05-25T18:00:00Z",
        last_rotated_at: "2026-05-25T18:05:00Z",
        secret_ref: `supabase_vault://${VAULT_ID}`,
        metadata: { password: "must-not-return" },
      },
    }),
    vaultAdapter: mockVault(),
    log: () => {},
  });
  const body = await res.json();
  if (body.credentials_configured !== true || body.credentials_version !== 2 || body.credentials_status !== "active") {
    throw new Error("active credential status fields missing");
  }
  if (body.last_submitted_at !== "2026-05-25T18:00:00Z" || body.last_rotated_at !== "2026-05-25T18:05:00Z") {
    throw new Error("credential timestamps missing");
  }
}));

Deno.test("status joins onboarding provisioning and login status", withEnv(async () => {
  const res = await handleRequest(request(statusBody()), {
    fetch: makeFetch({
      accountStatus: {
        client_id: CLIENT_ID,
        onboarding_status: "ready",
        provisioning_status: "provisioning",
        login_status: "needs_2fa",
      },
    }),
    vaultAdapter: mockVault(),
    log: () => {},
  });
  const body = await res.json();
  if (body.onboarding_status !== "ready" || body.provisioning_status !== "provisioning") {
    throw new Error("account status fields were not joined");
  }
  if (body.login_status !== "needs_2fa" || body.next_action !== "complete_2fa") {
    throw new Error("login_status was not mapped to complete_2fa");
  }
}));

for (
  const [name, input, expected] of [
    ["needs_2fa", { credentialsConfigured: true, reauthRequired: false, reauthReason: null, loginStatus: "needs_2fa" }, "complete_2fa"],
    ["checkpoint", { credentialsConfigured: true, reauthRequired: false, reauthReason: null, loginStatus: "checkpoint" }, "resolve_checkpoint"],
    ["failed", { credentialsConfigured: true, reauthRequired: false, reauthReason: null, loginStatus: "failed" }, "update_password"],
    ["mismatch", { credentialsConfigured: true, reauthRequired: false, reauthReason: null, loginStatus: "mismatch" }, "contact_support"],
    ["connected", { credentialsConfigured: true, reauthRequired: false, reauthReason: null, loginStatus: "connected" }, "none"],
    ["reauth_awaiting", { credentialsConfigured: true, reauthRequired: true, reauthReason: "awaiting_login_verification", loginStatus: "pending" }, "awaiting_login_verification"],
  ] as const
) {
  Deno.test(`next_action maps ${name}`, () => {
    const actual = deriveNextAction(input);
    if (actual !== expected) throw new Error(`${name} mapped to ${actual}`);
    if (!safeClientMessageForNextAction(actual)) throw new Error("safe message missing");
  });
}

Deno.test("status response excludes password secret_ref and raw metadata", withEnv(async () => {
  const res = await handleRequest(request(statusBody()), {
    fetch: makeFetch(),
    vaultAdapter: mockVault(),
    log: () => {},
  });
  const text = await res.text();
  if (text.includes(FAKE_PASSWORD) || text.includes("supabase_vault://") || text.includes("metadata")) {
    throw new Error("status response leaked password, secret_ref, or metadata");
  }
}));

Deno.test("internal token status path works", withEnv(async () => {
  const calls: FetchCall[] = [];
  const res = await handleRequest(request(statusBody(), "internal-token-not-real"), {
    fetch: makeFetch({ calls }),
    vaultAdapter: mockVault(),
    log: () => {},
  });
  const body = await res.json();
  if (res.status !== 200 || body.ok !== true || body.provider !== "instagram") {
    throw new Error("internal status path failed");
  }
  if (calls.some((c) => c.url.endsWith("/auth/v1/user"))) {
    throw new Error("internal status path should not verify client JWT");
  }
}));


Deno.test("submit success creates Vault secret and account_credentials v1", withEnv(async () => {
  const fetchCalls: FetchCall[] = [];
  const vaultCalls: VaultWriteInput[] = [];
  const res = await handleRequest(request(validBody()), {
    fetch: makeFetch({ calls: fetchCalls, maxVersion: 0 }),
    vaultAdapter: mockVault(vaultCalls),
    log: () => {},
  });
  const body = await res.json();
  if (res.status !== 200 || body.ok !== true || body.credentials_version !== 1) {
    throw new Error("submit did not return safe v1 success");
  }
  if (vaultCalls.length !== 1 || vaultCalls[0].version !== 1 || vaultCalls[0].password !== FAKE_PASSWORD) {
    throw new Error("vault write was not called for v1");
  }
  const rotate = fetchCalls.find((c) => c.url.includes("rotate_instagram_account_credentials"));
  if (!rotate || rotate.body?.p_secret_ref !== `supabase_vault://${VAULT_ID}`) {
    throw new Error("metadata rotation did not receive vault ref");
  }
}));

Deno.test("update_password success supersedes via rotation RPC and inserts v2", withEnv(async () => {
  const fetchCalls: FetchCall[] = [];
  const vaultCalls: VaultWriteInput[] = [];
  const res = await handleRequest(request(validBody({ action: "update_password" })), {
    fetch: makeFetch({ calls: fetchCalls, maxVersion: 1 }),
    vaultAdapter: mockVault(vaultCalls),
    log: () => {},
  });
  const body = await res.json();
  if (res.status !== 200 || body.credentials_version !== 2 || body.status !== "active") {
    throw new Error("update_password did not return v2 active");
  }
  const rotate = fetchCalls.find((c) => c.url.includes("rotate_instagram_account_credentials"));
  if (!rotate || rotate.body?.p_action !== "update_password") {
    throw new Error("rotation RPC did not receive update_password");
  }
}));

Deno.test("response never contains password or full secret_ref", withEnv(async () => {
  const res = await handleRequest(request(validBody()), {
    fetch: makeFetch(),
    vaultAdapter: mockVault(),
    log: () => {},
  });
  const text = await res.text();
  if (text.includes(FAKE_PASSWORD) || text.includes("supabase_vault://")) {
    throw new Error("response leaked password or secret_ref");
  }
}));

Deno.test("logs/errors never contain password in controlled failure", withEnv(async () => {
  const logs: string[] = [];
  const res = await handleRequest(request(validBody()), {
    fetch: makeFetch(),
    vaultAdapter: {
      async writeInstagramCredentialsSecret() {
        throw new Error(`boom ${FAKE_PASSWORD}`);
      },
    },
    log: (event, payload) => logs.push(JSON.stringify({ event, ...payload })),
  });
  const text = await res.text();
  const combined = `${text}\n${logs.join("\n")}`;
  if (res.status !== 500 || !text.includes("secret_write_failed")) {
    throw new Error("vault failure did not return safe error");
  }
  if (combined.includes(FAKE_PASSWORD)) {
    throw new Error("password leaked to logs/errors");
  }
}));

Deno.test("Vault failure returns secret_write_failed and does not insert metadata", withEnv(async () => {
  const calls: FetchCall[] = [];
  const res = await handleRequest(request(validBody()), {
    fetch: makeFetch({ calls }),
    vaultAdapter: {
      async writeInstagramCredentialsSecret() {
        throw new Error("secret write failed");
      },
    },
    log: () => {},
  });
  const body = await res.json();
  if (res.status !== 500 || body.error !== "secret_write_failed") {
    throw new Error("vault failure did not return secret_write_failed");
  }
  if (calls.some((c) => c.url.includes("rotate_instagram_account_credentials"))) {
    throw new Error("metadata was inserted after vault failure");
  }
}));

Deno.test("metadata write failure returns safe error", withEnv(async () => {
  const res = await handleRequest(request(validBody()), {
    fetch: makeFetch({ rotateStatus: 500 }),
    vaultAdapter: mockVault(),
    log: () => {},
  });
  const body = await res.json();
  if (res.status !== 500 || body.error !== "credentials_metadata_write_failed") {
    throw new Error("metadata failure was not safe");
  }
}));

Deno.test("internal token path works", withEnv(async () => {
  const calls: FetchCall[] = [];
  const res = await handleRequest(request(validBody(), "internal-token-not-real"), {
    fetch: makeFetch({ calls, maxVersion: 0 }),
    vaultAdapter: mockVault(),
    log: () => {},
  });
  const body = await res.json();
  if (res.status !== 200 || body.ok !== true) throw new Error("internal token path failed");
  if (calls.some((c) => c.url.endsWith("/auth/v1/user"))) {
    throw new Error("internal token path should not verify client JWT");
  }
}));

Deno.test("client response does not expose full secret_ref", withEnv(async () => {
  const res = await handleRequest(request(validBody()), {
    fetch: makeFetch(),
    vaultAdapter: mockVault(),
    log: () => {},
  });
  const body = await res.json();
  if ("secret_ref" in body || "vault_secret_id" in body) {
    throw new Error("client response exposed vault reference");
  }
}));

Deno.test("secret_ref format supabase_vault://uuid", () => {
  if (parseVaultSecretRef(`supabase_vault://${VAULT_ID}`) !== VAULT_ID) {
    throw new Error("valid secret_ref was not parsed");
  }
  if (parseVaultSecretRef(`supabase_vault://instagram/${ACCOUNT_ID}/credentials/v1`)) {
    throw new Error("path-style secret_ref should not parse as V1 ref");
  }
});

Deno.test("username normalized and validated", () => {
  if (normalizeUsername(" @Muse_Europe ") !== "muse_europe") {
    throw new Error("username was not normalized");
  }
  const bad = validatePayload(validBody({ username: "bad@@" }));
  if (bad.ok || bad.error !== "invalid_username") {
    throw new Error("invalid username accepted");
  }
});
