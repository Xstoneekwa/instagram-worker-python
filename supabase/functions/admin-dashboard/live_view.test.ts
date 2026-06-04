import {
  cloneLabelFromInstance,
  createLiveKitViewerToken,
  getLiveKitConfig,
  handleLiveViewAction,
  liveViewStart,
  sanitizeLiveViewPublic,
  validateLiveViewPayload,
} from "./live_view.ts";
import type { RestClient } from "./live_view_types.ts";
import { handleRequest } from "./index.ts";

const ACCOUNT_ID = "83de9cc9-5c37-42d1-8edc-c924352b17b1";
const DEVICE_ID = "11111111-1111-4111-8111-111111111111";
const APP_INSTANCE_ID = "7637db9a-3581-4099-8068-d5eb1ed86f96";
const SESSION_ID = "22222222-2222-4222-8222-222222222222";
const SECRET_TOKEN = "admin-dashboard-token-not-real";

type LiveViewDb = {
  accounts: Array<Record<string, unknown>>;
  assignments: Array<Record<string, unknown>>;
  devices: Array<Record<string, unknown>>;
  appInstances: Array<Record<string, unknown>>;
  sessions: Array<Record<string, unknown>>;
  audits: Array<Record<string, unknown>>;
  igRuns: Array<Record<string, unknown>>;
  requests: Array<Record<string, unknown>>;
};

function withEnv(fn: () => Promise<void> | void) {
  return async () => {
    const previous = {
      SUPABASE_URL: Deno.env.get("SUPABASE_URL"),
      SUPABASE_SERVICE_ROLE_KEY: Deno.env.get("SUPABASE_SERVICE_ROLE_KEY"),
      ADMIN_DASHBOARD_INTERNAL_API_TOKEN: Deno.env.get(
        "ADMIN_DASHBOARD_INTERNAL_API_TOKEN",
      ),
      LIVEKIT_URL: Deno.env.get("LIVEKIT_URL"),
      LIVEKIT_API_KEY: Deno.env.get("LIVEKIT_API_KEY"),
      LIVEKIT_API_SECRET: Deno.env.get("LIVEKIT_API_SECRET"),
    };
    Deno.env.set("SUPABASE_URL", "https://example.supabase.co");
    Deno.env.set("SUPABASE_SERVICE_ROLE_KEY", "service-role-live-view");
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

function baseDb(): LiveViewDb {
  return {
    accounts: [{
      id: ACCOUNT_ID,
      username: "i_m_your_traker",
      status: "active",
    }],
    assignments: [{
      id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      account_id: ACCOUNT_ID,
      device_id: DEVICE_ID,
      app_instance_id: APP_INSTANCE_ID,
      status: "reserved",
      created_at: "2026-06-04T12:00:00Z",
    }],
    devices: [{
      id: DEVICE_ID,
      name: "Samsung A16-01",
      status: "available",
      host_machine: "mac-mini-01",
      adb_serial: "RFGL145VCKE",
      device_udid: "RFGL145VCKE",
      hub_port: "usb:2-1",
    }],
    appInstances: [{
      id: APP_INSTANCE_ID,
      device_id: DEVICE_ID,
      instance_index: 1,
      visible_label: "Samsung A16-01 clone 1",
      package_name: "com.instagram.androie",
      status: "occupied",
      is_launchable: true,
    }],
    sessions: [],
    audits: [],
    igRuns: [],
    requests: [],
  };
}

function makeRest(db: LiveViewDb): RestClient {
  return {
    select: (tableName, query) => {
      const rows = (() => {
        if (tableName === "ig_accounts") return db.accounts;
        if (tableName === "account_assignments") return db.assignments;
        if (tableName === "phone_devices") return db.devices;
        if (tableName === "phone_app_instances") return db.appInstances;
        if (tableName === "live_view_sessions") return db.sessions;
        if (tableName === "ig_runs") return db.igRuns;
        if (tableName === "account_run_requests") return db.requests;
        return [];
      })();
      let filtered = [...rows];
      for (const [key, value] of Object.entries(query)) {
        if (key === "select" || key === "order" || key === "limit") continue;
        if (value.startsWith("eq.")) {
          const expected = value.slice(3);
          filtered = filtered.filter((row) => String(row[key]) === expected);
        }
        if (value.startsWith("in.(")) {
          const allowed = value.slice(4, -1).split(",");
          filtered = filtered.filter((row) => allowed.includes(String(row[key])));
        }
      }
      if (query.limit) {
        filtered = filtered.slice(0, Number(query.limit));
      }
      return Promise.resolve(new Response(JSON.stringify(filtered), { status: 200 }));
    },
    insert: (tableName, body) => {
      if (tableName === "live_view_sessions") {
        db.sessions.push({ ...body });
        return Promise.resolve(new Response(JSON.stringify([body]), { status: 201 }));
      }
      if (tableName === "live_view_audit_events") {
        db.audits.push({ ...body });
        return Promise.resolve(new Response(JSON.stringify([body]), { status: 201 }));
      }
      return Promise.resolve(new Response("[]", { status: 201 }));
    },
    update: (tableName, query, body) => {
      if (tableName !== "live_view_sessions") {
        return Promise.resolve(new Response("[]", { status: 200 }));
      }
      const id = query.id?.slice(4);
      const index = db.sessions.findIndex((row) => row.id === id);
      if (index < 0) return Promise.resolve(new Response("[]", { status: 200 }));
      db.sessions[index] = { ...db.sessions[index], ...body };
      return Promise.resolve(new Response(JSON.stringify([db.sessions[index]]), {
        status: 200,
      }));
    },
  };
}

function assertNoLeak(value: unknown) {
  const text = JSON.stringify(value).toLowerCase();
  for (const marker of [
    "adb_serial",
    "device_udid",
    "hub_port",
    "rfgl145",
    "host_machine",
    "mac-mini-01",
  ]) {
    if (text.includes(marker)) {
      throw new Error(`leak detected: ${marker}`);
    }
  }
}

Deno.test("validateLiveViewPayload accepts start payload", () => {
  const parsed = validateLiveViewPayload({
    action: "live_view_start",
    account_id: ACCOUNT_ID,
    mode: "view_only",
    source: "manager_row_eye",
    requested_by: "admin-1",
  });
  if (!parsed.ok || parsed.payload.action !== "live_view_start") {
    throw new Error("start payload rejected");
  }
});

Deno.test("sanitizeLiveViewPublic removes sensitive keys", () => {
  const sanitized = sanitizeLiveViewPublic({
    username: "i_m_your_traker",
    adb_serial: "RFGL145VCKE",
    nested: { hub_port: "1", package_name: "com.instagram.androie" },
  }) as Record<string, unknown>;
  assertNoLeak(sanitized);
  if (!JSON.stringify(sanitized).includes("i_m_your_traker")) {
    throw new Error("safe fields removed");
  }
});

Deno.test("cloneLabelFromInstance derives clone label", () => {
  if (cloneLabelFromInstance(1, "Samsung A16-01 clone 1") !== "clone 1") {
    throw new Error("clone label incorrect");
  }
});

Deno.test(
  "live_view_start creates pending session with safe response",
  async () => {
    const db = baseDb();
    const rest = makeRest(db);
    const result = await liveViewStart({
      action: "live_view_start",
      accountId: ACCOUNT_ID,
      mode: "view_only",
      source: "manager_row_eye",
      actorId: "admin-1",
      requestedBy: "admin-1",
    }, rest);
    assertNoLeak(result.body);
    if (
      result.reused ||
      result.body.status !== "pending" ||
      result.body.username !== "i_m_your_traker" ||
      result.body.device_label !== "Samsung A16-01" ||
      result.body.clone_label !== "clone 1" ||
      result.body.package_name !== "com.instagram.androie"
    ) {
      throw new Error(`unexpected start body: ${JSON.stringify(result.body)}`);
    }
    if (db.sessions.length !== 1 || db.audits.length !== 1) {
      throw new Error("session or audit not persisted");
    }
  },
);

Deno.test(
  "live_view_start refuses missing assignment",
  async () => {
    const db = baseDb();
    db.assignments = [];
    const rest = makeRest(db);
    let failed = false;
    try {
      await liveViewStart({
        action: "live_view_start",
        accountId: ACCOUNT_ID,
        mode: "view_only",
        source: "manager_row_eye",
        actorId: null,
        requestedBy: "admin-1",
      }, rest);
    } catch (error) {
      failed = error instanceof Error &&
        error.message === "assignment_not_found";
    }
    if (!failed) throw new Error("expected assignment_not_found");
  },
);

Deno.test(
  "live_view_start reuses active session for same account",
  async () => {
    const db = baseDb();
    db.sessions.push({
      id: SESSION_ID,
      account_id: ACCOUNT_ID,
      device_id: DEVICE_ID,
      app_instance_id: APP_INSTANCE_ID,
      status: "active",
      mode: "view_only",
      stream_transport: "webrtc",
      run_active_at_start: false,
      interaction_enabled: false,
      expires_at: "2026-06-04T13:00:00Z",
    });
    const rest = makeRest(db);
    const result = await liveViewStart({
      action: "live_view_start",
      accountId: ACCOUNT_ID,
      mode: "interactive",
      source: "manager_row_eye",
      actorId: null,
      requestedBy: "admin-1",
    }, rest);
    if (!result.reused || result.body.live_view_session_id !== SESSION_ID) {
      throw new Error(`expected reused session: ${JSON.stringify(result.body)}`);
    }
    if (db.sessions.length !== 1) {
      throw new Error("reused start created duplicate session");
    }
  },
);

Deno.test(
  "live_view_start forces view_only when run active",
  async () => {
    const db = baseDb();
    db.igRuns.push({
      id: "run-1",
      account_id: ACCOUNT_ID,
      status: "running",
    });
    const rest = makeRest(db);
    const result = await liveViewStart({
      action: "live_view_start",
      accountId: ACCOUNT_ID,
      mode: "interactive",
      source: "manager_row_eye",
      actorId: null,
      requestedBy: "admin-1",
    }, rest);
    if (result.body.mode !== "view_only" || result.body.run_active_at_start !== true) {
      throw new Error(`run guard failed: ${JSON.stringify(result.body)}`);
    }
  },
);

Deno.test(
  "live_view_token returns livekit_not_configured safely",
  async () => {
    const db = baseDb();
    db.sessions.push({
      id: SESSION_ID,
      account_id: ACCOUNT_ID,
      device_id: DEVICE_ID,
      app_instance_id: APP_INSTANCE_ID,
      status: "pending",
      livekit_room_name: `lv-${SESSION_ID}`,
    });
    const rest = makeRest(db);
    const result = await handleLiveViewAction({
      action: "live_view_token",
      liveViewSessionId: SESSION_ID,
      actorId: "admin-1",
    }, rest);
    if (
      result.body.ok !== false ||
      (result.body.error as Record<string, unknown>)?.code !== "livekit_not_configured"
    ) {
      throw new Error(`expected livekit_not_configured: ${JSON.stringify(result.body)}`);
    }
    assertNoLeak(result.body);
  },
);

Deno.test(
  "createLiveKitViewerToken works when env configured",
  withEnv(async () => {
    Deno.env.set("LIVEKIT_URL", "wss://livekit.example.com");
    Deno.env.set("LIVEKIT_API_KEY", "lk_api_key");
    Deno.env.set("LIVEKIT_API_SECRET", "lk_api_secret");
    if (!getLiveKitConfig()) throw new Error("livekit config missing");
    const token = await createLiveKitViewerToken({
      roomName: "lv-test",
      identity: "admin-viewer",
    });
    if (!token.token || token.expiresInSeconds !== 300) {
      throw new Error("livekit token not generated");
    }
    const parts = token.token.split(".");
    if (parts.length !== 3) throw new Error("jwt format invalid");
  }),
);

Deno.test(
  "handleRequest live_view_start via admin-dashboard",
  withEnv(async () => {
    const db = baseDb();
    const fetcher = async (
      input: string | URL | Request,
      init?: RequestInit,
    ): Promise<Response> => {
      const href = String(input);
      if (!href.includes("/rest/v1/")) {
        return new Response("{}", { status: 404 });
      }
      const rest = makeRest(db);
      const table = href.split("/rest/v1/")[1].split("?")[0];
      const query = Object.fromEntries(new URL(href).searchParams.entries());
      if ((init?.method || "GET") === "GET") return rest.select(table, query);
      if (init?.method === "POST") {
        return rest.insert(table, JSON.parse(String(init.body || "{}")));
      }
      if (init?.method === "PATCH") {
        return rest.update(table, query, JSON.parse(String(init.body || "{}")));
      }
      return new Response("[]", { status: 200 });
    };

    const res = await handleRequest(
      new Request("https://example.supabase.co/functions/v1/admin-dashboard", {
        method: "POST",
        headers: {
          authorization: `Bearer ${SECRET_TOKEN}`,
          "content-type": "application/json",
        },
        body: JSON.stringify({
          action: "live_view_start",
          account_id: ACCOUNT_ID,
          mode: "view_only",
          source: "manager_row_eye",
          requested_by: "admin-1",
        }),
      }),
      { fetch: fetcher, log: () => {} },
    );
    const body = await res.json();
    assertNoLeak(body);
    if (res.status !== 200 || body.status !== "pending") {
      throw new Error(`handleRequest start failed: ${JSON.stringify(body)}`);
    }
  }),
);

Deno.test("migration enables RLS and service_role only grants", async () => {
  const sql = await Deno.readTextFile(
    new URL("../../migrations/20260604142000_live_view_web_v1_foundation.sql", import.meta.url),
  );
  if (
    !sql.includes("enable row level security") ||
    !sql.includes("revoke all on public.live_view_sessions from anon") ||
    !sql.includes("revoke all on public.live_view_sessions from authenticated") ||
    !sql.includes("grant all on public.live_view_sessions to service_role")
  ) {
    throw new Error("migration security statements missing");
  }
});
