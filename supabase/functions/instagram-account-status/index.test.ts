import {
  handleRequest,
  validateForbiddenFields,
  validatePayload,
} from "./index.ts";

const ACCOUNT_ID = "42c625c2-e761-4100-8a9d-7ae1373de97d";
const OTHER_ACCOUNT_ID = "ba027e65-e024-4e02-b8ad-5d188507c04e";
const SECRET_TOKEN = "internal-token-not-real";

type FetchCall = { url: string; body: Record<string, unknown> | null };

function withEnv(fn: () => Promise<void> | void) {
  return async () => {
    const previous = {
      SUPABASE_URL: Deno.env.get("SUPABASE_URL"),
      SUPABASE_SERVICE_ROLE_KEY: Deno.env.get("SUPABASE_SERVICE_ROLE_KEY"),
      INSTAGRAM_ACCOUNT_STATUS_INTERNAL_API_TOKEN: Deno.env.get("INSTAGRAM_ACCOUNT_STATUS_INTERNAL_API_TOKEN"),
    };
    Deno.env.set("SUPABASE_URL", "https://example.supabase.co");
    Deno.env.set("SUPABASE_SERVICE_ROLE_KEY", "service-role-not-real");
    Deno.env.set("INSTAGRAM_ACCOUNT_STATUS_INTERNAL_API_TOKEN", SECRET_TOKEN);
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

function validBody(overrides: Record<string, unknown> = {}) {
  return {
    action: "update_status",
    account_id: ACCOUNT_ID,
    login_status: "connected",
    provisioning_status: "ready",
    onboarding_status: "ready",
    reauth_required: false,
    reason: "login_connected",
    external_request_id: "run-1",
    metadata: { source: "provisioner", stage: "login_check" },
    ...overrides,
  };
}

function request(body: Record<string, unknown>, token = SECRET_TOKEN) {
  return new Request("https://example.supabase.co/functions/v1/instagram-account-status", {
    method: "POST",
    headers: {
      "authorization": `Bearer ${token}`,
      "content-type": "application/json",
      "x-request-id": "req-status-1",
    },
    body: JSON.stringify(body),
  });
}

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function rpcSuccess(body: Record<string, unknown> = {}) {
  return {
    ok: true,
    account_id: ACCOUNT_ID,
    login_status: "connected",
    provisioning_status: "ready",
    onboarding_status: "ready",
    credentials_configured: true,
    reauth_required: false,
    reauth_reason: null,
    runtime_settings_sync: {
      ok: true,
      applied: true,
      reason: "runtime_settings_synced_after_provisioning",
      package_name: "com.instagram.androif",
      settings_updated: true,
      dm_settings_updated: true,
      unfollow_settings_updated: false,
      follow_enabled: true,
      like_enabled: true,
      mute_posts_after_follow: true,
      mute_stories_after_follow: true,
      welcome_enabled: false,
      outreach_enabled: false,
      unfollow_enabled: false,
      device_id: "must-not-return",
      app_instance_id: "must-not-return",
    },
    actions_upserted: [],
    actions_resolved: [],
    secret_ref: "supabase_vault://11111111-1111-4111-8111-111111111111",
    password: "must-not-return",
    vault_payload: { secret: "must-not-return" },
    metadata: { password: "must-not-return" },
    ...body,
  };
}

function makeFetch(options: {
  calls?: FetchCall[];
  rpcStatus?: number;
  rpcBody?: Record<string, unknown>;
} = {}) {
  const calls = options.calls ?? [];
  return async (url: string | URL | Request, init?: RequestInit): Promise<Response> => {
    const href = String(url);
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ url: href, body });
    if (href.includes("/rest/v1/rpc/update_client_instagram_account_status")) {
      return json(options.rpcBody ?? rpcSuccess(), options.rpcStatus ?? 200);
    }
    return json({ error: `unexpected ${href}` }, 500);
  };
}

Deno.test("OPTIONS CORS retourne 204", async () => {
  const res = await handleRequest(new Request("https://example.test", { method: "OPTIONS" }));
  if (res.status !== 204) throw new Error(`expected 204, got ${res.status}`);
});

Deno.test("rejette auth manquante", withEnv(async () => {
  const res = await handleRequest(new Request("https://example.test", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(validBody()),
  }));
  const body = await res.json();
  if (res.status !== 401 || body.error !== "unauthorized") throw new Error("expected unauthorized");
}));

Deno.test("rejette mauvais token", withEnv(async () => {
  const res = await handleRequest(request(validBody(), "wrong-token"));
  const body = await res.json();
  if (res.status !== 401 || body.error !== "unauthorized") throw new Error("expected unauthorized");
}));

Deno.test("rejette action inconnue", () => {
  const result = validatePayload(validBody({ action: "sync_status" }));
  if (result.ok || result.error !== "invalid_action") throw new Error("expected invalid_action");
});

Deno.test("rejette account_id manquant", () => {
  const body = validBody();
  delete (body as Record<string, unknown>).account_id;
  const result = validatePayload(body);
  if (result.ok || result.error !== "account_id_invalid") throw new Error("expected account_id_invalid");
});

Deno.test("rejette account_id invalide", () => {
  const result = validatePayload(validBody({ account_id: "not-a-uuid" }));
  if (result.ok || result.error !== "account_id_invalid") throw new Error("expected account_id_invalid");
});

Deno.test("rejette payload sans status ni reauth", () => {
  const result = validatePayload({
    action: "update_status",
    account_id: ACCOUNT_ID,
    reason: "noop",
    metadata: {},
  });
  if (result.ok || result.error !== "no_status_fields") throw new Error("expected no_status_fields");
});

Deno.test("rejette password top-level", () => {
  const forbidden = validateForbiddenFields(validBody({ password: "secret" }));
  if (forbidden !== "password") throw new Error(`expected password, got ${forbidden}`);
});

Deno.test("rejette metadata.password", () => {
  const forbidden = validateForbiddenFields(validBody({ metadata: { password: "secret" } }));
  if (forbidden !== "metadata.password") throw new Error(`expected metadata.password, got ${forbidden}`);
});

Deno.test("rejette reason > 500", () => {
  const result = validatePayload(validBody({ reason: "x".repeat(501) }));
  if (result.ok || result.error !== "reason_too_long") throw new Error("expected reason_too_long");
});

Deno.test("rejette external_request_id invalide", () => {
  const result = validatePayload(validBody({ external_request_id: "bad id with spaces" }));
  if (result.ok || result.error !== "external_request_id_invalid") {
    throw new Error("expected external_request_id_invalid");
  }
});

Deno.test("update_status connected appelle RPC avec connected et reauth false", withEnv(async () => {
  const calls: FetchCall[] = [];
  const res = await handleRequest(request(validBody()), { fetch: makeFetch({ calls }) });
  if (res.status !== 200) throw new Error(`expected 200, got ${res.status}`);
  const rpc = calls.find((call) => call.url.includes("update_client_instagram_account_status"));
  if (!rpc) throw new Error("missing rpc call");
  if (rpc.body?.p_login_status !== "connected") throw new Error("missing connected login status");
  if (rpc.body?.p_reauth_required !== false) throw new Error("missing reauth false");
  if (rpc.body?.p_actor_type !== "provisioner") throw new Error("expected provisioner actor");
}));

Deno.test("needs_2fa appelle RPC correctement", withEnv(async () => {
  const calls: FetchCall[] = [];
  const res = await handleRequest(request(validBody({
    login_status: "needs_2fa",
    provisioning_status: "login_verification_pending",
    onboarding_status: "verification_pending",
    reauth_required: undefined,
    reason: "two_factor_required",
  })), { fetch: makeFetch({ calls }) });
  if (res.status !== 200) throw new Error(`expected 200, got ${res.status}`);
  const rpc = calls[0];
  if (rpc.body?.p_login_status !== "needs_2fa") throw new Error("expected needs_2fa");
  if (rpc.body?.p_provisioning_status !== "login_verification_pending") {
    throw new Error("expected login_verification_pending");
  }
}));

Deno.test("checkpoint appelle RPC correctement", withEnv(async () => {
  const calls: FetchCall[] = [];
  await handleRequest(request(validBody({
    login_status: "checkpoint",
    provisioning_status: "login_verification_pending",
    onboarding_status: "verification_pending",
    reason: "checkpoint_required",
  })), { fetch: makeFetch({ calls }) });
  if (calls[0].body?.p_login_status !== "checkpoint") throw new Error("expected checkpoint");
  if (calls[0].body?.p_reason !== "checkpoint_required") throw new Error("expected checkpoint reason");
}));

Deno.test("failed appelle RPC correctement", withEnv(async () => {
  const calls: FetchCall[] = [];
  await handleRequest(request(validBody({
    login_status: "failed",
    provisioning_status: "failed",
    onboarding_status: "blocked",
    reason: "login_failed",
  })), { fetch: makeFetch({ calls }) });
  if (calls[0].body?.p_login_status !== "failed") throw new Error("expected failed");
  if (calls[0].body?.p_onboarding_status !== "blocked") throw new Error("expected blocked");
}));

Deno.test("metadata.source=provisioner donne p_actor_type=provisioner", withEnv(async () => {
  const calls: FetchCall[] = [];
  await handleRequest(request(validBody({ metadata: { source: "provisioner" } })), { fetch: makeFetch({ calls }) });
  if (calls[0].body?.p_actor_type !== "provisioner") throw new Error("expected provisioner");
}));

Deno.test("metadata.source=worker donne p_actor_type=worker", withEnv(async () => {
  const calls: FetchCall[] = [];
  await handleRequest(request(validBody({ metadata: { source: "worker" } })), { fetch: makeFetch({ calls }) });
  if (calls[0].body?.p_actor_type !== "worker") throw new Error("expected worker");
}));

Deno.test("réponse safe exclut secret_ref password vault", withEnv(async () => {
  const res = await handleRequest(request(validBody()), { fetch: makeFetch() });
  const text = await res.text();
  if (text.includes("secret_ref") || text.includes("must-not-return") || text.includes("vault_payload")) {
    throw new Error(`unsafe response: ${text}`);
  }
  const body = JSON.parse(text);
  if (body.request_id !== "req-status-1") throw new Error("missing request_id");
}));

Deno.test("réponse safe inclut runtime_settings_sync sans ids internes", withEnv(async () => {
  const res = await handleRequest(request(validBody()), { fetch: makeFetch() });
  const body = await res.json();
  const sync = body.runtime_settings_sync;
  if (!sync?.applied) throw new Error("expected runtime settings sync applied");
  if (sync.package_name !== "com.instagram.androif") throw new Error("expected synced package name");
  if (sync.follow_enabled !== true || sync.like_enabled !== true) throw new Error("expected follow/like enabled");
  if (sync.mute_posts_after_follow !== true || sync.mute_stories_after_follow !== true) {
    throw new Error("expected mute settings enabled");
  }
  const text = JSON.stringify(body);
  if (text.includes("must-not-return") || text.includes("device_id") || text.includes("app_instance_id")) {
    throw new Error(`unsafe runtime sync response: ${text}`);
  }
}));

Deno.test("RPC account not found -> 404 account_not_found", withEnv(async () => {
  const res = await handleRequest(request(validBody({ account_id: OTHER_ACCOUNT_ID })), {
    fetch: makeFetch({
      rpcStatus: 404,
      rpcBody: { code: "P0002", message: "client_instagram_account_not_found" },
    }),
  });
  const body = await res.json();
  if (res.status !== 404 || body.error !== "account_not_found") throw new Error("expected account_not_found");
}));

Deno.test("RPC invalid status -> 400 invalid_status", withEnv(async () => {
  const res = await handleRequest(request(validBody({ login_status: "bad_status" })), {
    fetch: makeFetch({
      rpcStatus: 400,
      rpcBody: { message: 'violates check constraint "client_instagram_accounts_login_status_check"' },
    }),
  });
  const body = await res.json();
  if (res.status !== 400 || body.error !== "invalid_status") throw new Error("expected invalid_status");
}));

Deno.test("RPC generic error -> 500 status_update_failed", withEnv(async () => {
  const res = await handleRequest(request(validBody()), {
    fetch: makeFetch({ rpcStatus: 500, rpcBody: { message: "unexpected failure" } }),
  });
  const body = await res.json();
  if (res.status !== 500 || body.error !== "status_update_failed") {
    throw new Error("expected status_update_failed");
  }
}));

Deno.test("logs safe sans Authorization token ni body complet", withEnv(async () => {
  const logs: Array<{ event: string; payload: Record<string, unknown> }> = [];
  const res = await handleRequest(request(validBody({ password: "must-not-log" })), {
    log: (event, payload) => logs.push({ event, payload }),
  });
  if (res.status !== 400) throw new Error(`expected 400, got ${res.status}`);
  const serialized = JSON.stringify(logs);
  if (serialized.includes(SECRET_TOKEN) || serialized.includes("authorization") || serialized.includes("must-not-log")) {
    throw new Error(`unsafe logs: ${serialized}`);
  }
}));
