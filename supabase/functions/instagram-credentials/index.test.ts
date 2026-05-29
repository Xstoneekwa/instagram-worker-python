import {
  deriveNextAction,
  handleRequest,
  normalizeUsername,
  parseVaultSecretRef,
  safeClientMessageForNextAction,
  validateForbiddenTopLevelFields,
  validatePayload,
  validateSafeMetadataObject,
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
      INSTAGRAM_CREDENTIALS_INTERNAL_API_TOKEN: Deno.env.get(
        "INSTAGRAM_CREDENTIALS_INTERNAL_API_TOKEN",
      ),
    };
    Deno.env.set("SUPABASE_URL", "https://example.supabase.co");
    Deno.env.set("SUPABASE_SERVICE_ROLE_KEY", "service-role-not-real");
    Deno.env.set(
      "INSTAGRAM_CREDENTIALS_INTERNAL_API_TOKEN",
      "internal-token-not-real",
    );
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
  return new Request(
    "https://example.supabase.co/functions/v1/instagram-credentials",
    {
      method: "POST",
      headers: {
        "authorization": `Bearer ${token}`,
        "content-type": "application/json",
        "x-request-id": "req-test-1",
      },
      body: JSON.stringify(body),
    },
  );
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
  accountIdentity?: Record<string, unknown> | null;
  maxVersion?: number;
  rotateStatus?: number;
  rotateRow?: Record<string, unknown>;
  dashboardActionStatus?: number;
  dashboardActionRow?: Record<string, unknown>;
  calls?: FetchCall[];
} = {}) {
  const calls = options.calls ?? [];
  return async (
    url: string | URL | Request,
    init?: RequestInit,
  ): Promise<Response> => {
    await Promise.resolve();
    const href = String(url);
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ url: href, body });

    if (href.endsWith("/auth/v1/user")) {
      const auth = new Headers(init?.headers).get("authorization") || "";
      if (auth === "Bearer client-jwt-not-real") {
        return json({ id: AUTH_USER_ID });
      }
      return json({ error: "unauthorized" }, 401);
    }
    if (href.includes("/rest/v1/rpc/client_can_manage_instagram_account")) {
      return json(options.clientAccess ?? true);
    }
    if (href.includes("/rest/v1/rpc/client_id_for_instagram_account")) {
      return json(
        options.clientId === undefined ? CLIENT_ID : options.clientId,
      );
    }
    if (href.includes("/rest/v1/client_instagram_accounts?")) {
      if (options.accountStatus === null) return json([]);
      return json([
        options.accountStatus ?? {
          client_id: CLIENT_ID,
          onboarding_status: "configured",
          provisioning_status: "pending",
          login_status: "pending",
        },
      ]);
    }
    if (href.includes("/rest/v1/ig_accounts?")) {
      if (options.accountIdentity === null) return json([]);
      return json([
        options.accountIdentity ?? {
          id: ACCOUNT_ID,
          username: "muse_europe",
        },
      ]);
    }
    if (href.includes("/rest/v1/account_credentials?")) {
      if (href.includes("status=eq.active")) {
        if (options.activeCredential === null) return json([]);
        return json([
          options.activeCredential ?? {
            provider: "instagram",
            credentials_version: options.maxVersion ?? 1,
            status: "active",
            reauth_required: true,
            reauth_reason: "awaiting_login_verification",
            last_submitted_at: "2026-05-25T18:00:00Z",
            last_rotated_at: null,
            metadata: { password: "must-not-return" },
            secret_ref: `supabase_vault://${VAULT_ID}`,
          },
        ]);
      }
      const max = options.maxVersion ?? 0;
      return json(max > 0 ? [{ credentials_version: max }] : []);
    }
    if (href.includes("/rest/v1/rpc/rotate_instagram_account_credentials")) {
      if (options.rotateStatus && options.rotateStatus >= 400) {
        return json({ error: "metadata failed" }, options.rotateStatus);
      }
      return json(
        options.rotateRow ?? {
          id: "22222222-2222-4222-8222-222222222222",
          account_id: ACCOUNT_ID,
          credentials_version: (options.maxVersion ?? 0) + 1,
          status: "active",
          reauth_required: true,
          submitted_via: body?.p_submitted_via ?? "client_dashboard",
        },
      );
    }
    if (href.includes("/rest/v1/rpc/upsert_account_dashboard_action")) {
      if (
        options.dashboardActionStatus && options.dashboardActionStatus >= 400
      ) {
        return json(
          { error: "dashboard action failed" },
          options.dashboardActionStatus,
        );
      }
      return json(
        options.dashboardActionRow ?? {
          id: "33333333-3333-4333-8333-333333333333",
          account_id: body?.p_account_id ?? ACCOUNT_ID,
          action_type: body?.p_action_type,
          status: body?.p_status,
        },
      );
    }
    if (href.includes("/rest/v1/rpc/update_client_instagram_account_status")) {
      return json({
        ok: true,
        account_id: body?.p_account_id,
        login_status: body?.p_login_status,
        provisioning_status: body?.p_provisioning_status,
        onboarding_status: body?.p_onboarding_status,
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
      await Promise.resolve();
      calls.push(input);
      return {
        vaultSecretId: VAULT_ID,
        secretRef: `supabase_vault://${VAULT_ID}`,
      };
    },
  };
}

type VaultWriteInput = {
  accountId: string;
  version: number;
  username: string | null;
  password: string;
};

Deno.test(
  "rejects missing auth",
  withEnv(async () => {
    const res = await handleRequest(
      new Request("https://example.test", {
        method: "POST",
        body: JSON.stringify(validBody()),
      }),
    );
    const body = await res.json();
    if (res.status !== 401 || body.error !== "unauthorized") {
      throw new Error("missing auth was not rejected");
    }
  }),
);

Deno.test(
  "status rejects unauthenticated request",
  withEnv(async () => {
    const res = await handleRequest(
      new Request("https://example.test", {
        method: "POST",
        body: JSON.stringify(statusBody()),
      }),
    );
    const body = await res.json();
    if (res.status !== 401 || body.error !== "unauthorized") {
      throw new Error("status missing auth was not rejected");
    }
  }),
);

Deno.test(
  "rejects invalid action",
  withEnv(() => {
    const result = validatePayload(validBody({ action: "rotate" }));
    if (result.ok || result.error !== "invalid_action") {
      throw new Error("invalid action accepted");
    }
  }),
);

Deno.test(
  "status rejects invalid account_id",
  withEnv(() => {
    const result = validatePayload(statusBody({ account_id: "not-a-uuid" }));
    if (result.ok || result.error !== "account_id_invalid") {
      throw new Error("status invalid account_id accepted");
    }
  }),
);

Deno.test(
  "rejects missing password",
  withEnv(() => {
    const result = validatePayload(validBody({ password: "" }));
    if (result.ok || result.error !== "password_invalid") {
      throw new Error("missing password accepted");
    }
  }),
);

Deno.test(
  "rejects forbidden fields",
  withEnv(() => {
    const top = validateForbiddenTopLevelFields(validBody({ secret_ref: "x" }));
    if (top !== "secret_ref") throw new Error("secret_ref was not rejected");
    const nested = validateForbiddenTopLevelFields(
      validBody({ metadata: { password: "x" } }),
    );
    if (nested !== "metadata.password") {
      throw new Error("metadata.password was not rejected");
    }
    const nestedSafe = validateSafeMetadataObject({ safe: { token: "x" } });
    if (nestedSafe !== "safe.token") {
      throw new Error("nested metadata_safe token was not rejected");
    }
  }),
);

Deno.test(
  "status rejects forbidden password and secret fields",
  withEnv(() => {
    const password = validateStatusForbiddenFields(
      statusBody({ password: FAKE_PASSWORD }),
    );
    if (password !== "password") {
      throw new Error("status password was not rejected");
    }
    const metadata = validateStatusForbiddenFields(
      statusBody({ metadata: { safe: "nope" } }),
    );
    if (metadata !== "metadata") {
      throw new Error("status metadata was not rejected");
    }
    const secretRef = validateStatusForbiddenFields(
      statusBody({ secret_ref: `supabase_vault://${VAULT_ID}` }),
    );
    if (secretRef !== "secret_ref") {
      throw new Error("status secret_ref was not rejected");
    }
  }),
);

Deno.test(
  "client JWT ownership false returns 403",
  withEnv(async () => {
    const res = await handleRequest(request(validBody()), {
      fetch: makeFetch({ clientAccess: false }),
      vaultAdapter: mockVault(),
      log: () => {},
    });
    const body = await res.json();
    if (res.status !== 403 || body.error !== "account_not_allowed") {
      throw new Error("ownership false did not return account_not_allowed");
    }
  }),
);

Deno.test(
  "status ownership false returns 403",
  withEnv(async () => {
    const res = await handleRequest(request(statusBody()), {
      fetch: makeFetch({ clientAccess: false }),
      vaultAdapter: mockVault(),
      log: () => {},
    });
    const body = await res.json();
    if (res.status !== 403 || body.error !== "account_not_allowed") {
      throw new Error(
        "status ownership false did not return account_not_allowed",
      );
    }
  }),
);

Deno.test(
  "status no active credentials returns submit_credentials",
  withEnv(async () => {
    const res = await handleRequest(request(statusBody()), {
      fetch: makeFetch({ activeCredential: null }),
      vaultAdapter: mockVault(),
      log: () => {},
    });
    const body = await res.json();
    if (res.status !== 200 || body.credentials_configured !== false) {
      throw new Error(
        "status without active credentials did not return configured=false",
      );
    }
    if (
      body.next_action !== "submit_credentials" ||
      body.safe_client_message !== "Instagram credentials are required."
    ) {
      throw new Error(
        "status without credentials did not request submit_credentials",
      );
    }
  }),
);

Deno.test(
  "status active credentials returns safe version status and timestamps",
  withEnv(async () => {
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
    if (
      body.credentials_configured !== true || body.credentials_version !== 2 ||
      body.credentials_status !== "active"
    ) {
      throw new Error("active credential status fields missing");
    }
    if (
      body.last_submitted_at !== "2026-05-25T18:00:00Z" ||
      body.last_rotated_at !== "2026-05-25T18:05:00Z"
    ) {
      throw new Error("credential timestamps missing");
    }
  }),
);

Deno.test(
  "status joins onboarding provisioning and login status",
  withEnv(async () => {
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
    if (
      body.onboarding_status !== "ready" ||
      body.provisioning_status !== "provisioning"
    ) {
      throw new Error("account status fields were not joined");
    }
    if (
      body.login_status !== "needs_2fa" || body.next_action !== "complete_2fa"
    ) {
      throw new Error("login_status was not mapped to complete_2fa");
    }
  }),
);

for (
  const [name, input, expected] of [
    ["needs_2fa", {
      credentialsConfigured: true,
      reauthRequired: false,
      reauthReason: null,
      loginStatus: "needs_2fa",
    }, "complete_2fa"],
    ["checkpoint", {
      credentialsConfigured: true,
      reauthRequired: false,
      reauthReason: null,
      loginStatus: "checkpoint",
    }, "resolve_checkpoint"],
    ["failed", {
      credentialsConfigured: true,
      reauthRequired: false,
      reauthReason: null,
      loginStatus: "failed",
    }, "update_password"],
    ["mismatch", {
      credentialsConfigured: true,
      reauthRequired: false,
      reauthReason: null,
      loginStatus: "mismatch",
    }, "contact_support"],
    ["connected", {
      credentialsConfigured: true,
      reauthRequired: false,
      reauthReason: null,
      loginStatus: "connected",
    }, "none"],
    ["reauth_awaiting", {
      credentialsConfigured: true,
      reauthRequired: true,
      reauthReason: "awaiting_login_verification",
      loginStatus: "pending",
    }, "awaiting_login_verification"],
  ] as const
) {
  Deno.test(`next_action maps ${name}`, () => {
    const actual = deriveNextAction(input);
    if (actual !== expected) throw new Error(`${name} mapped to ${actual}`);
    if (!safeClientMessageForNextAction(actual)) {
      throw new Error("safe message missing");
    }
  });
}

Deno.test(
  "status response excludes password secret_ref and raw metadata",
  withEnv(async () => {
    const res = await handleRequest(request(statusBody()), {
      fetch: makeFetch(),
      vaultAdapter: mockVault(),
      log: () => {},
    });
    const text = await res.text();
    if (
      text.includes(FAKE_PASSWORD) || text.includes("supabase_vault://") ||
      text.includes("metadata")
    ) {
      throw new Error(
        "status response leaked password, secret_ref, or metadata",
      );
    }
  }),
);

Deno.test(
  "internal token status path works",
  withEnv(async () => {
    const calls: FetchCall[] = [];
    const res = await handleRequest(
      request(statusBody(), "internal-token-not-real"),
      {
        fetch: makeFetch({ calls }),
        vaultAdapter: mockVault(),
        log: () => {},
      },
    );
    const body = await res.json();
    if (
      res.status !== 200 || body.ok !== true || body.provider !== "instagram"
    ) {
      throw new Error("internal status path failed");
    }
    if (calls.some((c) => c.url.endsWith("/auth/v1/user"))) {
      throw new Error("internal status path should not verify client JWT");
    }
  }),
);

Deno.test(
  "submit success creates Vault secret and account_credentials v1",
  withEnv(async () => {
    const fetchCalls: FetchCall[] = [];
    const vaultCalls: VaultWriteInput[] = [];
    const res = await handleRequest(request(validBody()), {
      fetch: makeFetch({ calls: fetchCalls, maxVersion: 0 }),
      vaultAdapter: mockVault(vaultCalls),
      log: () => {},
    });
    const body = await res.json();
    if (
      res.status !== 200 || body.ok !== true || body.credentials_version !== 1
    ) {
      throw new Error("submit did not return safe v1 success");
    }
    if (
      vaultCalls.length !== 1 || vaultCalls[0].version !== 1 ||
      vaultCalls[0].password !== FAKE_PASSWORD
    ) {
      throw new Error("vault write was not called for v1");
    }
    const rotate = fetchCalls.find((c) =>
      c.url.includes("rotate_instagram_account_credentials")
    );
    if (
      !rotate || rotate.body?.p_secret_ref !== `supabase_vault://${VAULT_ID}`
    ) {
      throw new Error("metadata rotation did not receive vault ref");
    }
  }),
);

Deno.test(
  "submit success syncs dashboard action pending_verification with safe metadata",
  withEnv(async () => {
    const fetchCalls: FetchCall[] = [];
    const res = await handleRequest(request(validBody()), {
      fetch: makeFetch({ calls: fetchCalls, maxVersion: 0 }),
      vaultAdapter: mockVault(),
      log: () => {},
    });
    const body = await res.json();
    if (res.status !== 200 || body.ok !== true) {
      throw new Error("submit failed");
    }

    const action = fetchCalls.find((c) =>
      c.url.includes("upsert_account_dashboard_action")
    );
    if (!action) throw new Error("dashboard action RPC was not called");
    if (action.body?.p_action_type !== "submit_instagram_credentials") {
      throw new Error("submit action_type incorrect");
    }
    if (action.body?.p_status !== "pending_verification") {
      throw new Error("submit status incorrect");
    }
    if (
      action.body?.p_requires_client_action !== false ||
      action.body?.p_blocking_campaign !== true
    ) {
      throw new Error("submit action flags incorrect");
    }
    if (
      action.body?.p_dedupe_key !==
        `account:${ACCOUNT_ID}:dashboard_action:submit_instagram_credentials`
    ) {
      throw new Error("submit dedupe_key incorrect");
    }

    const metadata = action.body?.p_metadata as Record<string, unknown>;
    if (
      metadata?.source !== "instagram_credentials" ||
      metadata?.action !== "submit" ||
      metadata?.credentials_version !== 1 ||
      metadata?.request_id !== "req-test-1" ||
      metadata?.external_request_id !== "entry2d2b-test"
    ) {
      throw new Error(`submit metadata incorrect: ${JSON.stringify(metadata)}`);
    }
    const rpcText = JSON.stringify(action.body);
    if (
      rpcText.includes(FAKE_PASSWORD) || rpcText.includes("secret_ref") ||
      rpcText.includes("supabase_vault://") || rpcText.includes(VAULT_ID)
    ) {
      throw new Error(
        "dashboard action RPC leaked password or vault reference",
      );
    }
  }),
);

Deno.test(
  "update_password success supersedes via rotation RPC and inserts v2",
  withEnv(async () => {
    const fetchCalls: FetchCall[] = [];
    const vaultCalls: VaultWriteInput[] = [];
    const res = await handleRequest(
      request(validBody({ action: "update_password" })),
      {
        fetch: makeFetch({ calls: fetchCalls, maxVersion: 1 }),
        vaultAdapter: mockVault(vaultCalls),
        log: () => {},
      },
    );
    const body = await res.json();
    if (
      res.status !== 200 || body.credentials_version !== 2 ||
      body.status !== "active"
    ) {
      throw new Error("update_password did not return v2 active");
    }
    const rotate = fetchCalls.find((c) =>
      c.url.includes("rotate_instagram_account_credentials")
    );
    if (!rotate || rotate.body?.p_action !== "update_password") {
      throw new Error("rotation RPC did not receive update_password");
    }
  }),
);

Deno.test(
  "update_password success syncs dashboard action pending_verification",
  withEnv(async () => {
    const fetchCalls: FetchCall[] = [];
    const res = await handleRequest(
      request(validBody({ action: "update_password" })),
      {
        fetch: makeFetch({ calls: fetchCalls, maxVersion: 1 }),
        vaultAdapter: mockVault(),
        log: () => {},
      },
    );
    const body = await res.json();
    if (res.status !== 200 || body.credentials_version !== 2) {
      throw new Error("update_password failed");
    }

    const action = fetchCalls.find((c) =>
      c.url.includes("upsert_account_dashboard_action")
    );
    if (!action) throw new Error("dashboard action RPC was not called");
    if (
      action.body?.p_action_type !== "update_instagram_password"
    ) throw new Error("update action_type incorrect");
    if (action.body?.p_status !== "pending_verification") {
      throw new Error("update status incorrect");
    }
    if (
      action.body?.p_action_deep_link !==
        `/accounts/${ACCOUNT_ID}/credentials#password`
    ) {
      throw new Error("update deep link incorrect");
    }

    const metadata = action.body?.p_metadata as Record<string, unknown>;
    if (
      metadata?.action !== "update_password" ||
      metadata?.credentials_version !== 2
    ) {
      throw new Error("update metadata incorrect");
    }
    const rpcText = JSON.stringify(action.body);
    if (
      rpcText.includes(FAKE_PASSWORD) || rpcText.includes("secret_ref") ||
      rpcText.includes("supabase_vault://") || rpcText.includes(VAULT_ID)
    ) {
      throw new Error(
        "dashboard action RPC leaked password or vault reference",
      );
    }
  }),
);

Deno.test(
  "dashboard action sync failure is fail-open for submit",
  withEnv(async () => {
    const logs: string[] = [];
    const res = await handleRequest(request(validBody()), {
      fetch: makeFetch({ maxVersion: 0, dashboardActionStatus: 500 }),
      vaultAdapter: mockVault(),
      log: (event, payload) => logs.push(JSON.stringify({ event, ...payload })),
    });
    const text = await res.text();
    if (res.status !== 200 || !text.includes('"ok":true')) {
      throw new Error("dashboard sync failure should not fail submit");
    }
    const combined = `${text}\n${logs.join("\n")}`;
    if (
      !combined.includes("instagram_credentials_dashboard_action_sync_failed")
    ) {
      throw new Error("dashboard sync failure was not logged");
    }
    if (
      combined.includes(FAKE_PASSWORD) ||
      combined.includes("supabase_vault://") || combined.includes("secret_ref")
    ) {
      throw new Error("dashboard sync failure leaked sensitive data");
    }
  }),
);

Deno.test(
  "status action remains read-only and does not sync dashboard action",
  withEnv(async () => {
    const calls: FetchCall[] = [];
    const res = await handleRequest(request(statusBody()), {
      fetch: makeFetch({ calls }),
      vaultAdapter: mockVault(),
      log: () => {},
    });
    const body = await res.json();
    if (res.status !== 200 || body.ok !== true) {
      throw new Error("status failed");
    }
    if (calls.some((c) => c.url.includes("upsert_account_dashboard_action"))) {
      throw new Error("status should not write dashboard actions");
    }
  }),
);

Deno.test(
  "response never contains password or full secret_ref",
  withEnv(async () => {
    const res = await handleRequest(request(validBody()), {
      fetch: makeFetch(),
      vaultAdapter: mockVault(),
      log: () => {},
    });
    const text = await res.text();
    if (text.includes(FAKE_PASSWORD) || text.includes("supabase_vault://")) {
      throw new Error("response leaked password or secret_ref");
    }
  }),
);

Deno.test(
  "logs/errors never contain password in controlled failure",
  withEnv(async () => {
    const logs: string[] = [];
    const res = await handleRequest(request(validBody()), {
      fetch: makeFetch(),
      vaultAdapter: {
        async writeInstagramCredentialsSecret() {
          await Promise.resolve();
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
  }),
);

Deno.test(
  "Vault failure returns secret_write_failed and does not insert metadata",
  withEnv(async () => {
    const calls: FetchCall[] = [];
    const res = await handleRequest(request(validBody()), {
      fetch: makeFetch({ calls }),
      vaultAdapter: {
        async writeInstagramCredentialsSecret() {
          await Promise.resolve();
          throw new Error("secret write failed");
        },
      },
      log: () => {},
    });
    const body = await res.json();
    if (res.status !== 500 || body.error !== "secret_write_failed") {
      throw new Error("vault failure did not return secret_write_failed");
    }
    if (
      calls.some((c) => c.url.includes("rotate_instagram_account_credentials"))
    ) {
      throw new Error("metadata was inserted after vault failure");
    }
  }),
);

Deno.test(
  "metadata write failure returns safe error",
  withEnv(async () => {
    const res = await handleRequest(request(validBody()), {
      fetch: makeFetch({ rotateStatus: 500 }),
      vaultAdapter: mockVault(),
      log: () => {},
    });
    const body = await res.json();
    if (
      res.status !== 500 || body.error !== "credentials_metadata_write_failed"
    ) {
      throw new Error("metadata failure was not safe");
    }
  }),
);

Deno.test(
  "internal token path works",
  withEnv(async () => {
    const calls: FetchCall[] = [];
    const res = await handleRequest(
      request(validBody(), "internal-token-not-real"),
      {
        fetch: makeFetch({ calls, maxVersion: 0 }),
        vaultAdapter: mockVault(),
        log: () => {},
      },
    );
    const body = await res.json();
    if (res.status !== 200 || body.ok !== true) {
      throw new Error("internal token path failed");
    }
    if (calls.some((c) => c.url.endsWith("/auth/v1/user"))) {
      throw new Error("internal token path should not verify client JWT");
    }
  }),
);

Deno.test(
  "client response does not expose full secret_ref",
  withEnv(async () => {
    const res = await handleRequest(request(validBody()), {
      fetch: makeFetch(),
      vaultAdapter: mockVault(),
      log: () => {},
    });
    const body = await res.json();
    if ("secret_ref" in body || "vault_secret_id" in body) {
      throw new Error("client response exposed vault reference");
    }
  }),
);

Deno.test(
  "submit_add_profile_credentials requires internal token",
  withEnv(async () => {
    const res = await handleRequest(
      request(validBody({
        action: "submit_add_profile_credentials",
        expected_username: "muse_europe",
        actor_type: "admin",
      })),
      {
        fetch: makeFetch(),
        vaultAdapter: mockVault(),
        log: () => {},
      },
    );
    const body = await res.json();
    if (res.status !== 403 || body.error !== "internal_token_required") {
      throw new Error("add profile action accepted client JWT");
    }
  }),
);

Deno.test(
  "submit_add_profile_credentials writes Vault and active metadata with safe response",
  withEnv(async () => {
    const fetchCalls: FetchCall[] = [];
    const vaultCalls: VaultWriteInput[] = [];
    const logs: string[] = [];
    const res = await handleRequest(
      request(
        validBody({
          action: "submit_add_profile_credentials",
          username: undefined,
          expected_username: "Muse_Europe",
          actor_type: "admin",
          actor_id: "admin-console",
          metadata_safe: { flow: "add_profile" },
        }),
        "internal-token-not-real",
      ),
      {
        fetch: makeFetch({ calls: fetchCalls, maxVersion: 1 }),
        vaultAdapter: mockVault(vaultCalls),
        log: (event, payload) =>
          logs.push(JSON.stringify({ event, ...payload })),
      },
    );
    const text = await res.text();
    const body = JSON.parse(text);
    if (res.status !== 200 || body.ok !== true) {
      throw new Error("add profile credential submit failed");
    }
    if (
      body.credentials_status !== "active" || body.status !== "active" ||
      body.credentials_version !== 2
    ) {
      throw new Error("add profile did not return active v2 credentials");
    }
    if (
      body.password_status !== "write_only" ||
      body.next_action !== "awaiting_login_verification"
    ) {
      throw new Error("add profile safe fields missing");
    }
    if (
      text.includes(FAKE_PASSWORD) || text.includes("supabase_vault://") ||
      text.includes(VAULT_ID)
    ) {
      throw new Error(
        "add profile response leaked password or vault reference",
      );
    }
    if (
      vaultCalls.length !== 1 || vaultCalls[0].username !== "muse_europe" ||
      vaultCalls[0].password !== FAKE_PASSWORD
    ) {
      throw new Error("add profile vault write input incorrect");
    }
    const rotate = fetchCalls.find((c) =>
      c.url.includes("rotate_instagram_account_credentials")
    );
    if (
      !rotate || rotate.body?.p_action !== "submit" ||
      rotate.body?.p_submitted_via !== "add_profile"
    ) {
      throw new Error("add profile rotate RPC did not use submit/add_profile");
    }
    const status = fetchCalls.find((c) =>
      c.url.includes("update_client_instagram_account_status")
    );
    if (
      !status ||
      status.body?.p_onboarding_status !== "credentials_submitted" ||
      status.body?.p_provisioning_status !== "login_pending" ||
      status.body?.p_login_status !== "verification_pending"
    ) {
      throw new Error("add profile status sync missing");
    }
    if (fetchCalls.some((c) => c.url.includes("ig_account_settings"))) {
      throw new Error("add profile attempted a legacy settings password write");
    }
    const combined = `${text}\n${logs.join("\n")}`;
    if (
      combined.includes(FAKE_PASSWORD) ||
      combined.includes("supabase_vault://") || combined.includes("secret_ref")
    ) {
      throw new Error("add profile logs/response leaked sensitive data");
    }
  }),
);

Deno.test(
  "submit_add_profile_credentials rotate failure returns safe error after Vault write",
  withEnv(async () => {
    const vaultCalls: VaultWriteInput[] = [];
    const logs: string[] = [];
    const res = await handleRequest(
      request(
        validBody({
          action: "submit_add_profile_credentials",
          expected_username: "muse_europe",
        }),
        "internal-token-not-real",
      ),
      {
        fetch: makeFetch({ rotateStatus: 500 }),
        vaultAdapter: mockVault(vaultCalls),
        log: (event, payload) =>
          logs.push(JSON.stringify({ event, ...payload })),
      },
    );
    const text = await res.text();
    const combined = `${text}\n${logs.join("\n")}`;
    if (
      res.status !== 500 ||
      !text.includes("credentials_metadata_write_failed")
    ) {
      throw new Error(
        "add profile rotate failure did not return safe metadata error",
      );
    }
    if (vaultCalls.length !== 1) {
      throw new Error("vault write should happen before rotate failure");
    }
    if (
      combined.includes(FAKE_PASSWORD) ||
      combined.includes("supabase_vault://") ||
      combined.includes(VAULT_ID) || combined.includes("secret_ref")
    ) {
      throw new Error("add profile rotate failure leaked sensitive data");
    }
  }),
);

Deno.test(
  "submit_add_profile_credentials rejects missing expected username",
  withEnv(() => {
    const result = validatePayload(validBody({
      action: "submit_add_profile_credentials",
      username: "",
      expected_username: "",
    }));
    if (result.ok || result.error !== "expected_username_required") {
      throw new Error("add profile accepted missing expected username");
    }
  }),
);

Deno.test(
  "submit_add_profile_credentials rejects username mismatch before Vault write",
  withEnv(async () => {
    const vaultCalls: VaultWriteInput[] = [];
    const fetchCalls: FetchCall[] = [];
    const res = await handleRequest(
      request(
        validBody({
          action: "submit_add_profile_credentials",
          expected_username: "different_account",
        }),
        "internal-token-not-real",
      ),
      {
        fetch: makeFetch({
          calls: fetchCalls,
          accountIdentity: { id: ACCOUNT_ID, username: "muse_europe" },
        }),
        vaultAdapter: mockVault(vaultCalls),
        log: () => {},
      },
    );
    const body = await res.json();
    if (res.status !== 409 || body.error !== "expected_username_mismatch") {
      throw new Error("add profile username mismatch was not rejected");
    }
    if (vaultCalls.length !== 0) {
      throw new Error("vault write was attempted after username mismatch");
    }
    if (
      fetchCalls.some((c) =>
        c.url.includes("rotate_instagram_account_credentials")
      )
    ) {
      throw new Error(
        "metadata rotation was attempted after username mismatch",
      );
    }
  }),
);

Deno.test(
  "submit_add_profile_credentials rejects forbidden metadata_safe",
  withEnv(() => {
    const result = validatePayload(validBody({
      action: "submit_add_profile_credentials",
      expected_username: "muse_europe",
      metadata_safe: { nested: { password: "nope" } },
    }));
    if (
      result.ok || result.error !== "metadata_safe_forbidden:nested.password"
    ) {
      throw new Error("add profile accepted forbidden metadata_safe");
    }
  }),
);

Deno.test("secret_ref format supabase_vault://uuid", () => {
  if (parseVaultSecretRef(`supabase_vault://${VAULT_ID}`) !== VAULT_ID) {
    throw new Error("valid secret_ref was not parsed");
  }
  if (
    parseVaultSecretRef(
      `supabase_vault://instagram/${ACCOUNT_ID}/credentials/v1`,
    )
  ) {
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
