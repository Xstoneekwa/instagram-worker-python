import {
  clampLimit,
  handleRequest,
  normalizeOffset,
  sanitizeForResponse,
  validatePayload,
} from "./index.ts";

const SECRET_TOKEN = "admin-dashboard-token-not-real";
const SERVICE_ROLE = "service-role-not-real-admin-dashboard";

type FetchCall = {
  url: string;
  body: Record<string, unknown> | null;
  authorization: string | null;
};

const MANAGE_ROW = {
  account_id: "42c625c2-e761-4100-8a9d-7ae1373de97d",
  client_id: "00000000-0000-4000-8000-000000002e2a",
  client_name: "XSTONE",
  username: "footballer_dreamers",
  email_display: "e***@example.com",
  admin_status: "active",
  customer_status: "active",
  subscription_status: "active",
  package_label: "growth",
  entitlement_summary: ["welcome_dm"],
  credentials_configured: true,
  credentials_status: "active",
  reauth_required: false,
  login_status: "connected",
  provisioning_status: "ready",
  onboarding_status: "ready",
  password_display: "configured",
  two_factor_display: "unknown",
  last_7d_growth: 12,
  pending_actions_count: 0,
  blocking_campaign: false,
  secret_ref: "supabase_vault://11111111-1111-4111-8111-111111111111",
  password: "must-not-return",
  password_hash: "hash-must-not-return",
  device_udid: "emulator-5554",
  metadata: { token: "must-not-return" },
  screenshot_path: "logs/screenshots/secret.png",
};

const RADAR_ROW = {
  account_id: "42c625c2-e761-4100-8a9d-7ae1373de97d",
  username: "footballer_dreamers",
  email_display: "e***@example.com",
  health_status: "ok",
  health_reason: "connected",
  admin_status: "active",
  password_display: "configured",
  two_factor_display: "unknown",
  actions_2d: 3,
  actions_7d: 12,
  actions_30d: 44,
  fbr_percent: 22.5,
  quick_rule_flags: [],
  special_care_active: false,
  raw_logs: "must-not-return",
  raw_metadata: { secret_ref: "must-not-return" },
  adb_serial: "device-secret",
};

function withEnv(fn: () => Promise<void> | void) {
  return async () => {
    const previous = {
      SUPABASE_URL: Deno.env.get("SUPABASE_URL"),
      SUPABASE_SERVICE_ROLE_KEY: Deno.env.get("SUPABASE_SERVICE_ROLE_KEY"),
      ADMIN_DASHBOARD_INTERNAL_API_TOKEN: Deno.env.get(
        "ADMIN_DASHBOARD_INTERNAL_API_TOKEN",
      ),
    };
    Deno.env.set("SUPABASE_URL", "https://example.supabase.co");
    Deno.env.set("SUPABASE_SERVICE_ROLE_KEY", SERVICE_ROLE);
    Deno.env.set("ADMIN_DASHBOARD_INTERNAL_API_TOKEN", SECRET_TOKEN);
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

function request(body: Record<string, unknown>, token = SECRET_TOKEN) {
  return new Request(
    "https://example.supabase.co/functions/v1/admin-dashboard",
    {
      method: "POST",
      headers: {
        "authorization": `Bearer ${token}`,
        "content-type": "application/json",
        "x-request-id": "req-admin-dashboard-test",
      },
      body: JSON.stringify(body),
    },
  );
}

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function makeFetch(options: {
  calls?: FetchCall[];
  manageStatus?: number;
  radarStatus?: number;
} = {}) {
  const calls = options.calls ?? [];
  return async (
    input: string | URL | Request,
    init?: RequestInit,
  ): Promise<Response> => {
    const href = String(input);
    const headers = new Headers(init?.headers);
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({
      url: href,
      body,
      authorization: headers.get("authorization"),
    });

    if (href.includes("/rest/v1/rpc/get_admin_account_overview")) {
      if (options.manageStatus && options.manageStatus >= 400) {
        return json({
          message: "private SQL detail must not leak",
          code: "XX000",
        }, options.manageStatus);
      }
      return json([MANAGE_ROW]);
    }

    if (href.includes("/rest/v1/rpc/get_admin_radar_overview")) {
      if (options.radarStatus && options.radarStatus >= 400) {
        return json({
          message: "private SQL detail must not leak",
          code: "XX000",
        }, options.radarStatus);
      }
      return json([RADAR_ROW]);
    }

    return json({ error: `unexpected ${href}` }, 500);
  };
}

function assertNoLeak(value: unknown) {
  const text = JSON.stringify(value).toLowerCase();
  const forbidden = [
    "must-not-return",
    SECRET_TOKEN.toLowerCase(),
    SERVICE_ROLE.toLowerCase(),
    "authorization",
    "secret_ref",
    "supabase_vault",
    "password_hash",
    "device_udid",
    "emulator-5554",
    "raw_logs",
    "raw_metadata",
    "adb_serial",
    "logs/screenshots",
  ];
  for (const marker of forbidden) {
    if (text.includes(marker)) {
      throw new Error(`leak detected: ${marker} in ${text}`);
    }
  }
}

Deno.test(
  "health OK avec token interne",
  withEnv(async () => {
    const logs: Array<{ event: string; payload: Record<string, unknown> }> = [];
    const res = await handleRequest(request({ action: "health" }), {
      log: (event, payload) => logs.push({ event, payload }),
    });
    const body = await res.json();
    if (
      res.status !== 200 || body.ok !== true ||
      body.service !== "admin-dashboard" || body.version !== "df-1b"
    ) {
      throw new Error(`health incorrect: ${JSON.stringify(body)}`);
    }
    assertNoLeak({ body, logs });
  }),
);

Deno.test(
  "missing Authorization -> 401",
  withEnv(async () => {
    const res = await handleRequest(
      new Request("https://example.test", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ action: "health" }),
      }),
    );
    const body = await res.json();
    if (
      res.status !== 401 || body.error?.code !== "unauthorized"
    ) throw new Error("expected unauthorized");
  }),
);

Deno.test(
  "bad token -> 401",
  withEnv(async () => {
    const res = await handleRequest(
      request({ action: "health" }, "wrong-token"),
    );
    const body = await res.json();
    if (
      res.status !== 401 || body.error?.code !== "unauthorized"
    ) throw new Error("expected unauthorized");
  }),
);

Deno.test(
  "unsupported action -> 400",
  withEnv(async () => {
    const result = validatePayload({ action: "write_settings" });
    if (result.ok || result.code !== "unsupported_action") {
      throw new Error("unsupported action accepted");
    }
    const res = await handleRequest(request({ action: "write_settings" }));
    const body = await res.json();
    if (res.status !== 400 || body.error?.code !== "unsupported_action") {
      throw new Error(
        `expected unsupported_action, got ${JSON.stringify(body)}`,
      );
    }
  }),
);

Deno.test("limit trop grand et offset negatif sont clamps", () => {
  if (clampLimit(500) !== 200) throw new Error("limit not clamped to 200");
  if (clampLimit(0) !== 1) throw new Error("limit not clamped to 1");
  if (normalizeOffset(-10) !== 0) throw new Error("offset not clamped to 0");
  const result = validatePayload({
    action: "manage_overview",
    limit: 999,
    offset: -5,
  });
  if (
    !result.ok || result.payload.limit !== 200 || result.payload.offset !== 0
  ) {
    throw new Error("payload clamp failed");
  }
});

Deno.test("search/status/health validation", () => {
  const tooLong = validatePayload({
    action: "manage_overview",
    search: "x".repeat(201),
  });
  if (tooLong.ok || tooLong.error !== "search_too_long") {
    throw new Error("long search accepted");
  }
  const healthWrongAction = validatePayload({
    action: "manage_overview",
    health: "ok",
  });
  if (
    healthWrongAction.ok ||
    healthWrongAction.error !== "health_filter_requires_radar_overview"
  ) {
    throw new Error("health accepted outside radar");
  }
});

Deno.test(
  "manage_overview appelle RPC attendue avec bons params",
  withEnv(async () => {
    const calls: FetchCall[] = [];
    const res = await handleRequest(
      request({
        action: "manage_overview",
        limit: 500,
        offset: -1,
        search: "footballer",
        status: "active",
      }),
      {
        fetch: makeFetch({ calls }),
        log: () => {},
      },
    );
    const body = await res.json();
    if (
      res.status !== 200 || body.ok !== true ||
      body.action !== "manage_overview" || body.count !== 1
    ) {
      throw new Error(`manage response incorrect: ${JSON.stringify(body)}`);
    }
    const rpc = calls.find((call) =>
      call.url.includes("get_admin_account_overview")
    );
    if (!rpc) throw new Error("manage RPC not called");
    if (
      rpc.body?.p_limit !== 200 ||
      rpc.body?.p_offset !== 0 ||
      rpc.body?.p_search !== "footballer" ||
      rpc.body?.p_status !== "active"
    ) {
      throw new Error(`manage params incorrect: ${JSON.stringify(rpc.body)}`);
    }
    assertNoLeak(body);
    if (body.items[0].password_display !== "configured") {
      throw new Error("safe password_display missing");
    }
  }),
);

Deno.test(
  "radar_overview appelle RPC attendue avec bons params",
  withEnv(async () => {
    const calls: FetchCall[] = [];
    const res = await handleRequest(
      request({
        action: "radar_overview",
        limit: 20,
        offset: 3,
        search: "dreamers",
        status: "active",
        health: "ok",
      }),
      {
        fetch: makeFetch({ calls }),
        log: () => {},
      },
    );
    const body = await res.json();
    if (
      res.status !== 200 || body.ok !== true ||
      body.action !== "radar_overview" || body.count !== 1
    ) {
      throw new Error(`radar response incorrect: ${JSON.stringify(body)}`);
    }
    const rpc = calls.find((call) =>
      call.url.includes("get_admin_radar_overview")
    );
    if (!rpc) throw new Error("radar RPC not called");
    if (
      rpc.body?.p_limit !== 20 ||
      rpc.body?.p_offset !== 3 ||
      rpc.body?.p_search !== "dreamers" ||
      rpc.body?.p_status !== "active" ||
      rpc.body?.p_health !== "ok"
    ) {
      throw new Error(`radar params incorrect: ${JSON.stringify(rpc.body)}`);
    }
    assertNoLeak(body);
  }),
);

Deno.test(
  "RPC error -> reponse safe rpc_failed",
  withEnv(async () => {
    const logs: Array<{ event: string; payload: Record<string, unknown> }> = [];
    const res = await handleRequest(request({ action: "manage_overview" }), {
      fetch: makeFetch({ manageStatus: 500 }),
      log: (event, payload) => logs.push({ event, payload }),
    });
    const body = await res.json();
    if (res.status !== 502 || body.error?.code !== "rpc_failed") {
      throw new Error(`expected rpc_failed, got ${JSON.stringify(body)}`);
    }
    const text = JSON.stringify({ body, logs }).toLowerCase();
    if (text.includes("private sql detail")) {
      throw new Error("RPC detail leaked");
    }
    assertNoLeak({ body, logs });
  }),
);

Deno.test("sanitizeForResponse retire champs et valeurs interdits", () => {
  const sanitized = sanitizeForResponse({
    password_display: "configured",
    password: "must-not-return",
    nested: {
      secret_ref: "must-not-return",
      screenshot_path: "logs/screenshots/a.png",
      ok: "safe",
    },
  }) as Record<string, unknown>;
  const text = JSON.stringify(sanitized);
  if (!text.includes("password_display") || !text.includes("safe")) {
    throw new Error("safe fields removed");
  }
  assertNoLeak(sanitized);
});
