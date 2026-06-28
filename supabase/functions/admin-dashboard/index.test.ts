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
type PhoneDb = {
  phoneDevices: Array<Record<string, any>>;
  appInstances: Array<Record<string, any>>;
  runtimeEvents: Array<Record<string, any>>;
  deviceHeartbeats?: Array<Record<string, any>>;
  accountAssignments?: Array<Record<string, any>>;
  accountRunRequests?: Array<Record<string, any>>;
  accountCredentials?: Array<Record<string, any>>;
  liveViewSessions?: Array<Record<string, any>>;
  phoneClones?: Array<Record<string, any>>;
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

function assertNoSecretLeakAllowOps(value: unknown) {
  const text = JSON.stringify(value).toLowerCase();
  const forbidden = [
    "must-not-return",
    SECRET_TOKEN.toLowerCase(),
    SERVICE_ROLE.toLowerCase(),
    "authorization",
    "secret_ref",
    "supabase_vault",
    "password",
    "service_role",
    "internal_api_token",
    "vault",
    "cookie",
    "token",
    "logs/screenshots",
  ];
  for (const marker of forbidden) {
    if (text.includes(marker)) {
      throw new Error(`secret leak detected: ${marker} in ${text}`);
    }
  }
}

function addPhonePayload(overrides: Record<string, unknown> = {}) {
  return {
    action: "add_physical_phone",
    display_name: "Samsung A16-03",
    adb_serial: "RFGL145TEST",
    model: "SM-A165F",
    product: "a16nsxx",
    device: "a16",
    pool: "full_cycle",
    max_clones: 3,
    hub_label: "local-usb",
    hub_port: "usb:2-1",
    host_label: "dev-mac",
    packages_mode: "standard_instagram_4_packages",
    ...overrides,
  };
}

function makePhoneFetch(db: PhoneDb, calls: FetchCall[] = []) {
  return async (
    input: string | URL | Request,
    init?: RequestInit,
  ): Promise<Response> => {
    const href = String(input);
    const parsed = new URL(href);
    const table = parsed.pathname.split("/").pop() || "";
    const headers = new Headers(init?.headers);
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({
      url: href,
      body,
      authorization: headers.get("authorization"),
    });

    if (table === "phone_devices") {
      if ((init?.method || "GET") === "GET") {
        const adbEq = parsed.searchParams.get("adb_serial") || "";
        const idEq = parsed.searchParams.get("id") || "";
        const adbSerial = adbEq.startsWith("eq.") ? adbEq.slice(3) : "";
        const id = idEq.startsWith("eq.") ? idEq.slice(3) : "";
        let rows = db.phoneDevices;
        if (adbSerial) rows = rows.filter((row) => row.adb_serial === adbSerial);
        if (id) rows = rows.filter((row) => row.id === id);
        const limit = Number(parsed.searchParams.get("limit") || rows.length);
        return json(rows.slice(0, limit));
      }
      if (init?.method === "POST") {
        const row = {
          id: `device-${db.phoneDevices.length + 1}`,
          created_at: "2026-06-02T00:00:00Z",
          updated_at: "2026-06-02T00:00:00Z",
          ...body,
        };
        db.phoneDevices.push(row);
        return json([row], 201);
      }
      if (init?.method === "PATCH") {
        const idEq = parsed.searchParams.get("id") || "";
        const id = idEq.startsWith("eq.") ? idEq.slice(3) : "";
        const index = db.phoneDevices.findIndex((row) => row.id === id);
        if (index < 0) return json([], 200);
        db.phoneDevices[index] = { ...db.phoneDevices[index], ...body };
        return json([db.phoneDevices[index]]);
      }
      if (init?.method === "DELETE") {
        const idEq = parsed.searchParams.get("id") || "";
        const id = idEq.startsWith("eq.") ? idEq.slice(3) : "";
        db.phoneDevices = db.phoneDevices.filter((row) => row.id !== id);
        return json(db.phoneDevices.filter((row) => row.id === id));
      }
    }

    if (table === "phone_app_instances") {
      if ((init?.method || "GET") === "GET") {
        const deviceEq = parsed.searchParams.get("device_id") || "";
        const deviceId = deviceEq.startsWith("eq.") ? deviceEq.slice(3) : "";
        return json(deviceId
          ? db.appInstances.filter((row) => row.device_id === deviceId)
          : db.appInstances);
      }
      if (init?.method === "POST") {
        const row = {
          id: `app-${db.appInstances.length + 1}`,
          created_at: "2026-06-02T00:00:00Z",
          updated_at: "2026-06-02T00:00:00Z",
          ...body,
        };
        db.appInstances.push(row);
        return json([row], 201);
      }
      if (init?.method === "PATCH") {
        const idEq = parsed.searchParams.get("id") || "";
        const id = idEq.startsWith("eq.") ? idEq.slice(3) : "";
        const index = db.appInstances.findIndex((row) => row.id === id);
        if (index < 0) return json([], 200);
        db.appInstances[index] = { ...db.appInstances[index], ...body };
        return json([db.appInstances[index]]);
      }
      if (init?.method === "DELETE") {
        const deviceEq = parsed.searchParams.get("device_id") || "";
        const deviceId = deviceEq.startsWith("eq.") ? deviceEq.slice(3) : "";
        db.appInstances = db.appInstances.filter((row) => row.device_id !== deviceId);
        return json([]);
      }
    }

    if (table === "account_assignments" && (init?.method || "GET") === "GET") {
      const deviceEq = parsed.searchParams.get("device_id") || "";
      const deviceId = deviceEq.startsWith("eq.") ? deviceEq.slice(3) : "";
      const rows = deviceId
        ? (db.accountAssignments ?? []).filter((row) => row.device_id === deviceId)
        : (db.accountAssignments ?? []);
      return json(rows);
    }

    if (table === "account_run_requests" && (init?.method || "GET") === "GET") {
      return json(db.accountRunRequests ?? []);
    }

    if (table === "account_credentials" && (init?.method || "GET") === "GET") {
      return json(db.accountCredentials ?? []);
    }

    if (table === "live_view_sessions" && (init?.method || "GET") === "GET") {
      const deviceEq = parsed.searchParams.get("device_id") || "";
      const deviceId = deviceEq.startsWith("eq.") ? deviceEq.slice(3) : "";
      const rows = deviceId
        ? (db.liveViewSessions ?? []).filter((row) => row.device_id === deviceId)
        : (db.liveViewSessions ?? []);
      return json(rows);
    }

    if (table === "phone_clones") {
      if (init?.method === "PATCH") {
        const deviceEq = parsed.searchParams.get("device_id") || "";
        const deviceId = deviceEq.startsWith("eq.") ? deviceEq.slice(3) : "";
        db.phoneClones = (db.phoneClones ?? []).map((row) => (
          row.device_id === deviceId ? { ...row, ...body } : row
        ));
        return json((db.phoneClones ?? []).filter((row) => row.device_id === deviceId));
      }
      if (init?.method === "DELETE") {
        const deviceEq = parsed.searchParams.get("device_id") || "";
        const deviceId = deviceEq.startsWith("eq.") ? deviceEq.slice(3) : "";
        db.phoneClones = (db.phoneClones ?? []).filter((row) => row.device_id !== deviceId);
        return json([]);
      }
    }

    if (table === "device_heartbeats") {
      if ((init?.method || "GET") === "GET") {
        return json(db.deviceHeartbeats ?? []);
      }
      if (init?.method === "DELETE") {
        const deviceEq = parsed.searchParams.get("device_id") || "";
        const deviceId = deviceEq.startsWith("eq.") ? deviceEq.slice(3) : "";
        db.deviceHeartbeats = (db.deviceHeartbeats ?? []).filter((row) => row.device_id !== deviceId);
        return json([]);
      }
    }

    return json({ error: `unexpected ${href}` }, 500);
  };
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
  "health OK avec apikey interne",
  withEnv(async () => {
    const res = await handleRequest(
      new Request("https://example.supabase.co/functions/v1/admin-dashboard", {
        method: "POST",
        headers: {
          "apikey": SECRET_TOKEN,
          "content-type": "application/json",
        },
        body: JSON.stringify({ action: "health" }),
      }),
    );
    const body = await res.json();
    if (res.status !== 200 || body.ok !== true) {
      throw new Error(`health via apikey incorrect: ${JSON.stringify(body)}`);
    }
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
    !result.ok ||
    result.payload.action !== "manage_overview" ||
    result.payload.limit !== 200 ||
    result.payload.offset !== 0
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
  "devices_overview remonte phone_devices et garde items/count compatibles",
  withEnv(async () => {
    const db: PhoneDb = {
      phoneDevices: [{
        id: "device-1",
        name: "Samsung A16-01",
        device_kind: "physical_phone",
        adb_serial: "RFGL145VCKE",
        host_machine: "mac-mini-01",
        hub_label: "hub-a",
        hub_port: "1",
        pool_type: "full_cycle",
        max_clones: 3,
        status: "available",
        metadata: { model: "SM-A165F", product: "a16nsxx", device: "a16" },
        created_at: "2026-06-02T00:00:00Z",
        updated_at: "2026-06-02T00:00:00Z",
      }],
      appInstances: [],
      runtimeEvents: [],
    };
    const res = await handleRequest(request({ action: "devices_overview" }), {
      fetch: makePhoneFetch(db),
    });
    const body = await res.json();
    if (res.status !== 200 || body.ok !== true || body.action !== "devices_overview") {
      throw new Error(`devices_overview failed: ${JSON.stringify(body)}`);
    }
    if (body.count !== 1 || body.items?.length !== 1 || body.phone_devices?.length !== 1) {
      throw new Error(`devices_overview missing compatibility fields: ${JSON.stringify(body)}`);
    }
    const phone = body.phone_devices[0];
    if (
      phone.device_id !== "device-1" ||
      phone.display_name !== "Samsung A16-01" ||
      phone.adb_serial !== "RFGL145VCKE" ||
      phone.pool !== "full_cycle" ||
      phone.host_label !== "mac-mini-01" ||
      phone.hub_label !== "hub-a" ||
      phone.hub_port !== "1" ||
      phone.model !== "SM-A165F"
    ) {
      throw new Error(`phone projection incorrect: ${JSON.stringify(phone)}`);
    }
  }),
);

Deno.test(
  "devices_overview calcule les compteurs app_instances",
  withEnv(async () => {
    const db: PhoneDb = {
      phoneDevices: [{
        id: "device-1",
        name: "Samsung A16-01",
        device_kind: "physical_phone",
        adb_serial: "RFGL145VCKE",
        pool_type: "full_cycle",
        max_clones: 3,
        status: "available",
        metadata: {},
      }],
      appInstances: [
        {
          id: "app-0",
          device_id: "device-1",
          instance_type: "primary_app",
          instance_index: 0,
          package_name: "com.instagram.android",
          status: "available",
          current_account_id: null,
          metadata: { adb_package_verified: false },
        },
        {
          id: "app-1",
          device_id: "device-1",
          instance_type: "clone",
          instance_index: 1,
          package_name: "com.instagram.androie",
          status: "occupied",
          current_account_id: "00000000-0000-4000-8000-000000000001",
          metadata: { adb_package_verified: true },
        },
        {
          id: "app-2",
          device_id: "device-1",
          instance_type: "clone",
          instance_index: 2,
          package_name: "com.instagram.androif",
          status: "available",
          current_account_id: null,
          metadata: { adb_package_verified: true },
        },
        {
          id: "app-3",
          device_id: "device-1",
          instance_type: "clone",
          instance_index: 3,
          package_name: "com.instagram.androig",
          status: "available",
          current_account_id: null,
          metadata: { adb_package_verified: true },
        },
      ],
      runtimeEvents: [],
    };
    const res = await handleRequest(request({ action: "devices_overview" }), {
      fetch: makePhoneFetch(db),
    });
    const body = await res.json();
    const phone = body.phone_devices?.[0];
    if (
      phone?.app_instances_count !== 4 ||
      phone.app_instances_available_count !== 3 ||
      phone.app_instances_occupied_count !== 1 ||
      phone.primary_package_present_in_db !== true ||
      phone.clone_packages_registered_count !== 3 ||
      body.phone_inventory_summary?.total_app_instances !== 4 ||
      body.phone_inventory_summary?.available_app_instances !== 3 ||
      body.phone_inventory_summary?.occupied_app_instances !== 1
    ) {
      throw new Error(`app instance counts incorrect: ${JSON.stringify(body)}`);
    }
  }),
);

Deno.test(
  "devices_overview signale les app instances standard manquantes",
  withEnv(async () => {
    const db: PhoneDb = {
      phoneDevices: [{
        id: "device-1",
        name: "Samsung A16-01",
        device_kind: "physical_phone",
        adb_serial: "RFGL145VCKE",
        pool_type: "full_cycle",
        max_clones: 3,
        status: "available",
        metadata: {},
      }],
      appInstances: [{
        id: "app-1",
        device_id: "device-1",
        instance_type: "clone",
        instance_index: 1,
        package_name: "com.instagram.androie",
        status: "available",
        current_account_id: null,
        metadata: {},
      }],
      runtimeEvents: [],
    };
    const res = await handleRequest(request({ action: "devices_overview" }), {
      fetch: makePhoneFetch(db),
    });
    const body = await res.json();
    const issues = body.phone_devices?.[0]?.issues ?? [];
    if (
      !issues.includes("missing_primary_instance") ||
      !issues.includes("missing_standard_clone_package")
    ) {
      throw new Error(`missing instance issues not computed: ${JSON.stringify(body)}`);
    }
  }),
);

Deno.test(
  "devices_overview sans heartbeat reste unknown et ne pretend pas online",
  withEnv(async () => {
    const db: PhoneDb = {
      phoneDevices: [{
        id: "device-1",
        name: "Samsung A16-01",
        device_kind: "physical_phone",
        adb_serial: "RFGL145VCKE",
        pool_type: "full_cycle",
        max_clones: 3,
        status: "available",
        metadata: {},
      }],
      appInstances: [],
      runtimeEvents: [],
      deviceHeartbeats: [],
    };
    const res = await handleRequest(request({ action: "devices_overview" }), {
      fetch: makePhoneFetch(db),
    });
    const body = await res.json();
    const phone = body.phone_devices?.[0];
    if (
      phone?.heartbeat_status !== "unknown" ||
      !phone.issues.includes("adb_status_unknown") ||
      body.phone_inventory_summary?.adb_status_unknown_count !== 1
    ) {
      throw new Error(`heartbeat unknown not preserved: ${JSON.stringify(body)}`);
    }
  }),
);

Deno.test(
  "devices_overview ne fuit pas metadata sensible",
  withEnv(async () => {
    const db: PhoneDb = {
      phoneDevices: [{
        id: "device-1",
        name: "Samsung A16-01",
        device_kind: "physical_phone",
        adb_serial: "RFGL145VCKE",
        pool_type: "full_cycle",
        max_clones: 3,
        status: "available",
        metadata: {
          model: "SM-A165F",
          secret_ref: "redacted-fixture",
          password: "redacted-fixture",
          token: "redacted-fixture",
        },
      }],
      appInstances: [{
        id: "app-0",
        device_id: "device-1",
        instance_type: "primary_app",
        instance_index: 0,
        package_name: "com.instagram.android",
        status: "available",
        current_account_id: null,
        metadata: {
          adb_package_verified: false,
          secret_ref: "redacted-fixture",
        },
      }],
      runtimeEvents: [],
    };
    const res = await handleRequest(request({ action: "devices_overview" }), {
      fetch: makePhoneFetch(db),
    });
    const body = await res.json();
    assertNoSecretLeakAllowOps(body);
    if (JSON.stringify(body).includes("redacted-fixture")) {
      throw new Error(`secret metadata leaked: ${JSON.stringify(body)}`);
    }
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

Deno.test(
  "add_physical_phone cree un phone physique avec 4 app instances",
  withEnv(async () => {
    const db: PhoneDb = { phoneDevices: [], appInstances: [], runtimeEvents: [] };
    const res = await handleRequest(request(addPhonePayload()), {
      fetch: makePhoneFetch(db),
      log: () => {},
    });
    const body = await res.json();
    if (res.status !== 200 || body.ok !== true) {
      throw new Error(`add phone failed: ${JSON.stringify(body)}`);
    }
    if (db.phoneDevices.length !== 1) throw new Error("phone device not created");
    const phone = db.phoneDevices[0];
    if (
      phone.device_kind !== "physical_phone" ||
      phone.name !== "Samsung A16-03" ||
      phone.adb_serial !== "RFGL145TEST" ||
      phone.pool_type !== "full_cycle" ||
      phone.max_clones !== 3 ||
      phone.status !== "available"
    ) {
      throw new Error(`phone fields incorrect: ${JSON.stringify(phone)}`);
    }
    if (db.appInstances.length !== 4) {
      throw new Error(`expected 4 app instances, got ${db.appInstances.length}`);
    }
    const packages = db.appInstances
      .sort((a, b) => a.instance_index - b.instance_index)
      .map((row) => `${row.instance_index}:${row.instance_type}:${row.package_name}`);
    const expected = [
      "0:primary_app:com.instagram.android",
      "1:clone:com.instagram.androie",
      "2:clone:com.instagram.androif",
      "3:clone:com.instagram.androig",
    ];
    if (JSON.stringify(packages) !== JSON.stringify(expected)) {
      throw new Error(`packages incorrect: ${JSON.stringify(packages)}`);
    }
    for (const app of db.appInstances) {
      if (app.status !== "available" || app.current_account_id !== null) {
        throw new Error(`app should be free: ${JSON.stringify(app)}`);
      }
    }
    if (
      body.app_instances_created_count !== 4 ||
      body.app_instances_existing_count !== 0
    ) {
      throw new Error(`summary incorrect: ${JSON.stringify(body)}`);
    }
    if (db.runtimeEvents.length !== 1) throw new Error("audit event not written");
    assertNoSecretLeakAllowOps({ body, events: db.runtimeEvents });
  }),
);

Deno.test(
  "add_physical_phone est idempotent au second save",
  withEnv(async () => {
    const db: PhoneDb = { phoneDevices: [], appInstances: [], runtimeEvents: [] };
    const fetcher = makePhoneFetch(db);
    await handleRequest(request(addPhonePayload()), { fetch: fetcher, log: () => {} });
    const second = await handleRequest(request(addPhonePayload()), {
      fetch: fetcher,
      log: () => {},
    });
    const body = await second.json();
    if (
      second.status !== 200 ||
      body.app_instances_created_count !== 0 ||
      body.app_instances_existing_count !== 4
    ) {
      throw new Error(`second save not idempotent: ${JSON.stringify(body)}`);
    }
    if (db.phoneDevices.length !== 1 || db.appInstances.length !== 4) {
      throw new Error("idempotent save duplicated rows");
    }
  }),
);

Deno.test(
  "add_physical_phone update le meme adb_serial sans creer de doublon",
  withEnv(async () => {
    const db: PhoneDb = { phoneDevices: [], appInstances: [], runtimeEvents: [] };
    const fetcher = makePhoneFetch(db);
    await handleRequest(request(addPhonePayload()), { fetch: fetcher, log: () => {} });
    const res = await handleRequest(
      request(addPhonePayload({
        display_name: "Samsung A16-03 Renamed",
        pool: "outreach_only",
        hub_port: "usb:9-1",
      })),
      { fetch: fetcher, log: () => {} },
    );
    const body = await res.json();
    if (res.status !== 200 || db.phoneDevices.length !== 1) {
      throw new Error(`same serial update failed: ${JSON.stringify(body)}`);
    }
    if (
      db.phoneDevices[0].name !== "Samsung A16-03 Renamed" ||
      db.phoneDevices[0].pool_type !== "outreach_only"
    ) {
      throw new Error(`same serial not updated: ${JSON.stringify(db.phoneDevices[0])}`);
    }
    if (!body.warnings.includes("hub_port_changed")) {
      throw new Error(`hub port warning missing: ${JSON.stringify(body)}`);
    }
  }),
);

Deno.test(
  "add_physical_phone bloque une app_instance occupee",
  withEnv(async () => {
    const db: PhoneDb = {
      phoneDevices: [{
        id: "device-1",
        name: "Samsung A16-03",
        adb_serial: "RFGL145TEST",
        hub_port: "usb:2-1",
        metadata: {},
      }],
      appInstances: [{
        id: "app-1",
        device_id: "device-1",
        instance_type: "primary_app",
        instance_index: 0,
        package_name: "com.instagram.android",
        status: "occupied",
        current_account_id: "account-1",
      }],
      runtimeEvents: [],
    };
    const res = await handleRequest(request(addPhonePayload()), {
      fetch: makePhoneFetch(db),
      log: () => {},
    });
    const body = await res.json();
    if (res.status !== 409 || body.error?.message !== "app_instance_occupied") {
      throw new Error(`occupied conflict not blocked: ${JSON.stringify(body)}`);
    }
    if (db.appInstances.length !== 1) {
      throw new Error("should not create more instances after occupied conflict");
    }
  }),
);

Deno.test("add_physical_phone accepte full_cycle et outreach_only", () => {
  const full = validatePayload(addPhonePayload({ pool: "full_cycle" }));
  const outreach = validatePayload(addPhonePayload({ pool: "outreach_only" }));
  const bad = validatePayload(addPhonePayload({ pool: "shared" }));
  if (!full.ok || full.payload.action !== "add_physical_phone") {
    throw new Error("full_cycle rejected");
  }
  if (!outreach.ok || outreach.payload.action !== "add_physical_phone") {
    throw new Error("outreach_only rejected");
  }
  if (bad.ok || bad.error !== "pool_invalid") {
    throw new Error("bad pool accepted");
  }
});

Deno.test(
  "add_physical_phone sauvegarde hub/port sans les utiliser comme cle runtime",
  withEnv(async () => {
    const db: PhoneDb = { phoneDevices: [], appInstances: [], runtimeEvents: [] };
    const res = await handleRequest(
      request(addPhonePayload({ hub_label: "rack-a", hub_port: "usb:4-2" })),
      { fetch: makePhoneFetch(db), log: () => {} },
    );
    const body = await res.json();
    if (res.status !== 200) throw new Error(`save failed: ${JSON.stringify(body)}`);
    if (
      db.phoneDevices[0].hub_label !== "rack-a" ||
      db.phoneDevices[0].hub_port !== "usb:4-2" ||
      db.phoneDevices[0].adb_serial !== "RFGL145TEST"
    ) {
      throw new Error(`hub metadata not saved: ${JSON.stringify(db.phoneDevices[0])}`);
    }
    if (body.phone.adb_serial !== "RFGL145TEST") {
      throw new Error("runtime serial missing from response");
    }
  }),
);

Deno.test(
  "delete_physical_phone_preflight refuse un device avec assignation active",
  withEnv(async () => {
    const db: PhoneDb = {
      phoneDevices: [{
        id: "device-legacy",
        name: "Entry 2C Physical Outreach Phone",
        device_kind: "physical_phone",
        status: "available",
        adb_serial: "phys-001",
      }],
      appInstances: [{
        id: "app-1",
        device_id: "device-legacy",
        instance_type: "primary_app",
        instance_index: 0,
        package_name: "com.instagram.android",
        status: "available",
        current_account_id: null,
      }],
      runtimeEvents: [],
      accountAssignments: [{
        id: "assign-1",
        device_id: "device-legacy",
        status: "active",
        account_id: "42c625c2-e761-4100-8a9d-7ae1373de97d",
      }],
    };
    const res = await handleRequest(request({
      action: "delete_physical_phone_preflight",
      device_id: "device-legacy",
    }), {
      fetch: makePhoneFetch(db),
      log: () => {},
    });
    const body = await res.json();
    if (res.status !== 200 || body.preflight?.deletable !== false) {
      throw new Error(`preflight should block: ${JSON.stringify(body)}`);
    }
    if (!body.preflight?.blockingReasonsFr?.length) {
      throw new Error("blocking reasons missing");
    }
    assertNoSecretLeakAllowOps(body);
  }),
);

Deno.test(
  "delete_physical_phone refuse sans confirmation exacte",
  withEnv(async () => {
    const db: PhoneDb = {
      phoneDevices: [{
        id: "device-empty",
        name: "Empty Phone",
        device_kind: "physical_phone",
        status: "available",
        adb_serial: "RFGL000EMPTY",
      }],
      appInstances: [],
      runtimeEvents: [],
      accountAssignments: [],
    };
    const res = await handleRequest(request({
      action: "delete_physical_phone",
      device_id: "device-empty",
      confirmation_name: "Wrong Name",
    }), {
      fetch: makePhoneFetch(db),
      log: () => {},
    });
    const body = await res.json();
    if (res.status !== 400 || body.error?.message !== "confirmation_name_mismatch") {
      throw new Error(`confirmation mismatch not enforced: ${JSON.stringify(body)}`);
    }
    if (db.phoneDevices.length !== 1) {
      throw new Error("device mutated on mismatch");
    }
  }),
);

Deno.test(
  "delete_physical_phone_preflight autorise un device avec historique released",
  withEnv(async () => {
    const db: PhoneDb = {
      phoneDevices: [{
        id: "device-legacy",
        name: "Entry 2C Physical Outreach Phone",
        device_kind: "physical_phone",
        status: "available",
        adb_serial: "phys-001",
      }],
      appInstances: [{
        id: "app-1",
        device_id: "device-legacy",
        instance_type: "primary_app",
        instance_index: 0,
        package_name: "com.instagram.android",
        status: "available",
        current_account_id: null,
      }],
      runtimeEvents: [],
      accountAssignments: Array.from({ length: 5 }, (_value, index) => ({
        id: `assign-${index + 1}`,
        device_id: "device-legacy",
        status: "released",
        account_id: "42c625c2-e761-4100-8a9d-7ae1373de97d",
      })),
    };
    const res = await handleRequest(request({
      action: "delete_physical_phone_preflight",
      device_id: "device-legacy",
    }), {
      fetch: makePhoneFetch(db),
      log: () => {},
    });
    const body = await res.json();
    if (res.status !== 200 || body.preflight?.deletable !== true) {
      throw new Error(`released history should allow retire: ${JSON.stringify(body)}`);
    }
    if (body.preflight?.releasedAssignmentCount !== 5) {
      throw new Error("released assignment count missing");
    }
    if (!String(body.preflight?.releasedAssignmentsInfoFr || "").includes("5 anciennes assignations")) {
      throw new Error("released info copy missing");
    }
    assertNoSecretLeakAllowOps(body);
  }),
);

Deno.test(
  "delete_physical_phone retire un device vide avec confirmation exacte",
  withEnv(async () => {
    const db: PhoneDb = {
      phoneDevices: [{
        id: "device-empty",
        name: "Empty Phone",
        device_kind: "physical_phone",
        status: "available",
        adb_serial: "RFGL000EMPTY",
        metadata: {},
      }],
      appInstances: [{
        id: "app-1",
        device_id: "device-empty",
        instance_type: "primary_app",
        instance_index: 0,
        package_name: "com.instagram.android",
        status: "available",
        current_account_id: null,
      }],
      runtimeEvents: [],
      accountAssignments: [],
      deviceHeartbeats: [{ device_id: "device-empty", status: "offline" }],
      phoneClones: [{ id: "clone-1", device_id: "device-empty", status: "available" }],
    };
    const res = await handleRequest(request({
      action: "delete_physical_phone",
      device_id: "device-empty",
      confirmation_name: "Empty Phone",
    }), {
      fetch: makePhoneFetch(db),
      log: () => {},
    });
    const body = await res.json();
    if (res.status !== 200 || body.ok !== true) {
      throw new Error(`retire failed: ${JSON.stringify(body)}`);
    }
    if (db.phoneDevices.length !== 1 || db.phoneDevices[0].status !== "retired") {
      throw new Error("device should remain archived as retired");
    }
    if (db.appInstances[0].status !== "disabled") {
      throw new Error("app instances should be disabled not deleted");
    }
    if ((db.accountAssignments ?? []).length !== 0) {
      throw new Error("assignments must remain untouched");
    }
    if (!db.runtimeEvents.some((event) => event.event_type === "phone_retired")) {
      throw new Error("audit event missing");
    }
    assertNoSecretLeakAllowOps(body);
  }),
);

Deno.test(
  "delete_physical_phone est idempotent apres retrait",
  withEnv(async () => {
    const db: PhoneDb = {
      phoneDevices: [{
        id: "device-empty",
        name: "Empty Phone",
        device_kind: "physical_phone",
        status: "retired",
        adb_serial: "RFGL000EMPTY",
        metadata: { operational_retirement_v1: true },
      }],
      appInstances: [],
      runtimeEvents: [{ id: "event-1", event_type: "phone_retired" }],
      accountAssignments: [],
    };
    const res = await handleRequest(request({
      action: "delete_physical_phone",
      device_id: "device-empty",
      confirmation_name: "Empty Phone",
    }), {
      fetch: makePhoneFetch(db),
      log: () => {},
    });
    const body = await res.json();
    if (res.status !== 200 || body.idempotent !== true) {
      throw new Error(`idempotent retire expected: ${JSON.stringify(body)}`);
    }
    if (db.runtimeEvents.length !== 1) {
      throw new Error("duplicate audit event emitted");
    }
  }),
);

Deno.test("add_physical_phone refuse les champs credential/secret", () => {
  const result = validatePayload(addPhonePayload({
    password: "must-not-return",
  }));
  if (result.ok || !result.error.includes("forbidden_field")) {
    throw new Error("credential field accepted");
  }
});
