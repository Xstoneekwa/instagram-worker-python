import {
  clampLimit,
  handleRequest,
  normalizeOffset,
  validateForbiddenFields,
  validatePayload,
} from "./index.ts";

const ACCOUNT_ID = "42c625c2-e761-4100-8a9d-7ae1373de97d";
const OTHER_ACCOUNT_ID = "ba027e65-e024-4e02-b8ad-5d188507c04e";
const CLIENT_ID = "00000000-0000-4000-8000-000000002e2a";
const AUTH_USER_ID = "00000000-0000-4000-8000-000000000001";
const FAKE_SECRET = "not-real-password-dashboard-actions";

type FetchCall = { url: string; body: Record<string, unknown> | null };
type Row = Record<string, unknown>;

const FIXTURE_ROWS: Row[] = [
  {
    id: "10000000-0000-4000-8000-000000000001",
    client_id: CLIENT_ID,
    account_id: ACCOUNT_ID,
    action_type: "submit_instagram_credentials",
    status: "pending",
    severity: "warning",
    audience: "client",
    requires_client_action: true,
    blocking_campaign: true,
    title: "Connect Instagram",
    safe_client_message: "Instagram credentials are required.",
    admin_message: "admin-only details",
    assistant_message: "assistant-only details",
    action_label: "Connect Instagram",
    action_deep_link: "/accounts/42/connect-instagram",
    metadata: { password: FAKE_SECRET, safe: "ignored" },
    secret_ref: "supabase_vault://11111111-1111-4111-8111-111111111111",
    webhook_url: "https://hooks.example.invalid/secret",
    created_at: "2026-05-25T20:00:00Z",
    updated_at: "2026-05-25T20:01:00Z",
  },
  {
    id: "10000000-0000-4000-8000-000000000002",
    client_id: CLIENT_ID,
    account_id: ACCOUNT_ID,
    action_type: "review_login_failure",
    status: "pending_verification",
    severity: "error",
    audience: "client",
    requires_client_action: false,
    blocking_campaign: false,
    title: "Login verification pending",
    safe_client_message: "Credentials saved. Login verification is pending.",
    action_label: "View status",
    action_deep_link: "/accounts/42/status",
    created_at: "2026-05-25T19:00:00Z",
    updated_at: "2026-05-25T19:01:00Z",
  },
  {
    id: "10000000-0000-4000-8000-000000000003",
    client_id: CLIENT_ID,
    account_id: ACCOUNT_ID,
    action_type: "review_account_mismatch",
    status: "acknowledged",
    severity: "critical",
    audience: "admin",
    requires_client_action: false,
    blocking_campaign: true,
    title: "Review mismatch",
    safe_client_message: "Your account requires review by support.",
    admin_message: "Admin-only mismatch details",
    action_label: "Review",
    action_deep_link: "/admin/accounts/42/incidents",
    created_at: "2026-05-25T18:00:00Z",
    updated_at: "2026-05-25T18:01:00Z",
  },
  {
    id: "10000000-0000-4000-8000-000000000004",
    client_id: null,
    account_id: ACCOUNT_ID,
    action_type: "add_targets",
    status: "pending",
    severity: "info",
    audience: "client",
    requires_client_action: true,
    blocking_campaign: false,
    title: "Add targets",
    safe_client_message: "Target accounts are required.",
    action_label: "Add targets",
    action_deep_link: "/accounts/42/targets",
    created_at: "2026-05-25T17:00:00Z",
    updated_at: "2026-05-25T17:01:00Z",
  },
  {
    id: "10000000-0000-4000-8000-000000000005",
    client_id: "00000000-0000-4000-8000-000000000999",
    account_id: OTHER_ACCOUNT_ID,
    action_type: "contact_support",
    status: "pending",
    severity: "warning",
    audience: "client",
    requires_client_action: true,
    blocking_campaign: false,
    title: "Other client action",
    safe_client_message: "Support is required.",
    action_label: "Contact support",
    action_deep_link: "/support",
    created_at: "2026-05-25T16:00:00Z",
    updated_at: "2026-05-25T16:01:00Z",
  },
];

function withEnv(fn: () => Promise<void> | void) {
  return async () => {
    const previous = {
      SUPABASE_URL: Deno.env.get("SUPABASE_URL"),
      SUPABASE_SERVICE_ROLE_KEY: Deno.env.get("SUPABASE_SERVICE_ROLE_KEY"),
      DASHBOARD_ACTIONS_INTERNAL_API_TOKEN: Deno.env.get("DASHBOARD_ACTIONS_INTERNAL_API_TOKEN"),
    };
    Deno.env.set("SUPABASE_URL", "https://example.supabase.co");
    Deno.env.set("SUPABASE_SERVICE_ROLE_KEY", "service-role-not-real");
    Deno.env.set("DASHBOARD_ACTIONS_INTERNAL_API_TOKEN", "internal-token-not-real");
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
  return new Request("https://example.supabase.co/functions/v1/dashboard-actions", {
    method: "POST",
    headers: {
      "authorization": `Bearer ${token}`,
      "content-type": "application/json",
      "x-request-id": "req-dashboard-actions-test",
    },
    body: JSON.stringify(body),
  });
}

function json(data: unknown, status = 200, headers: HeadersInit = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

function bodyOf(init?: RequestInit): Record<string, unknown> | null {
  return init?.body ? JSON.parse(String(init.body)) : null;
}

function applyActionFilters(url: URL, rows: Row[]): Row[] {
  let result = [...rows];
  const params = url.searchParams;
  for (const statusFilter of params.getAll("status")) {
    if (statusFilter.startsWith("eq.")) {
      const status = statusFilter.slice(3);
      result = result.filter((row) => row.status === status);
    } else if (statusFilter.startsWith("in.(")) {
      const statuses = statusFilter.slice(4, -1).split(",");
      result = result.filter((row) => statuses.includes(String(row.status)));
    }
  }
  for (const audienceFilter of params.getAll("audience")) {
    if (audienceFilter.startsWith("eq.")) {
      const audience = audienceFilter.slice(3);
      result = result.filter((row) => row.audience === audience);
    }
  }
  for (const accountFilter of params.getAll("account_id")) {
    if (accountFilter.startsWith("eq.")) {
      const accountId = accountFilter.slice(3);
      result = result.filter((row) => row.account_id === accountId);
    }
  }
  for (const severityFilter of params.getAll("severity")) {
    if (severityFilter.startsWith("eq.")) {
      const severity = severityFilter.slice(3);
      result = result.filter((row) => row.severity === severity);
    }
  }
  for (const blockingFilter of params.getAll("blocking_campaign")) {
    if (blockingFilter === "eq.true") result = result.filter((row) => row.blocking_campaign === true);
  }
  for (const requiredFilter of params.getAll("requires_client_action")) {
    if (requiredFilter === "eq.true") result = result.filter((row) => row.requires_client_action === true);
  }
  for (const clientFilter of params.getAll("client_id")) {
    if (clientFilter.startsWith("eq.")) {
      const clientId = clientFilter.slice(3);
      result = result.filter((row) => row.client_id === clientId);
    }
  }
  for (const orFilter of params.getAll("or")) {
    if (orFilter.includes(`client_id.eq.${CLIENT_ID}`) && orFilter.includes(`account_id.eq.${ACCOUNT_ID}`)) {
      result = result.filter((row) => row.client_id === CLIENT_ID || row.account_id === ACCOUNT_ID);
    } else if (orFilter.includes(`client_id.eq.${CLIENT_ID}`) && orFilter.includes(`account_id.in.(${ACCOUNT_ID})`)) {
      result = result.filter((row) => row.client_id === CLIENT_ID || row.account_id === ACCOUNT_ID);
    }
  }
  return result;
}

function makeFetch(options: {
  clientAccess?: boolean;
  ownedAccountIds?: string[];
  calls?: FetchCall[];
} = {}) {
  const calls = options.calls ?? [];
  return async (input: string | URL | Request, init?: RequestInit): Promise<Response> => {
    const href = String(input);
    const url = new URL(href);
    calls.push({ url: href, body: bodyOf(init) });

    if (href.endsWith("/auth/v1/user")) {
      const auth = new Headers(init?.headers).get("authorization") || "";
      if (auth === "Bearer client-jwt-not-real") return json({ id: AUTH_USER_ID });
      return json({ error: "unauthorized" }, 401);
    }
    if (href.includes("/rest/v1/client_users?")) {
      return json([{ client_id: CLIENT_ID }]);
    }
    if (href.includes("/rest/v1/client_instagram_accounts?")) {
      const ids = options.ownedAccountIds ?? [ACCOUNT_ID];
      return json(ids.map((account_id) => ({ account_id })));
    }
    if (href.includes("/rest/v1/rpc/client_can_manage_instagram_account")) {
      return json(options.clientAccess ?? true);
    }
    if (href.includes("/rest/v1/account_dashboard_actions?")) {
      const filtered = applyActionFilters(url, FIXTURE_ROWS);
      const range = new Headers(init?.headers).get("range");
      if (range === "0-0") {
        return json(filtered.slice(0, 1), 200, { "content-range": `0-0/${filtered.length}` });
      }
      const offset = Number(url.searchParams.get("offset") || "0");
      const limit = Number(url.searchParams.get("limit") || "20");
      return json(filtered.slice(offset, offset + limit));
    }
    return json({ error: `unexpected ${href}` }, 500);
  };
}

Deno.test("rejette auth manquante", withEnv(async () => {
  const res = await handleRequest(new Request("https://example.test", {
    method: "POST",
    body: JSON.stringify({ action: "count" }),
  }));
  const body = await res.json();
  if (res.status !== 401 || body.error !== "unauthorized") throw new Error("auth manquante acceptee");
}));

Deno.test("rejette action inconnue", () => {
  const result = validatePayload({ action: "resolve" });
  if (result.ok || result.error !== "invalid_action") throw new Error("action inconnue acceptee");
});

Deno.test("rejette champs interdits sensibles", () => {
  if (validateForbiddenFields({ password: FAKE_SECRET }) !== "password") throw new Error("password accepte");
  if (validateForbiddenFields({ metadata: { password: FAKE_SECRET } }) !== "metadata.password") {
    throw new Error("metadata.password accepte");
  }
  if (validateForbiddenFields({ webhook_url: "https://hooks.example.invalid/x" }) !== "webhook_url") {
    throw new Error("webhook_url accepte");
  }
});

Deno.test("client ne peut pas demander audience admin", withEnv(async () => {
  const res = await handleRequest(request({ action: "count", audience: "admin" }), {
    fetch: makeFetch(),
    log: () => {},
  });
  const body = await res.json();
  if (res.status !== 403 || body.error !== "audience_not_allowed") {
    throw new Error("audience admin autorisee au client");
  }
}));

Deno.test("ownership false retourne 403", withEnv(async () => {
  const res = await handleRequest(request({ action: "list", account_id: ACCOUNT_ID }), {
    fetch: makeFetch({ clientAccess: false }),
    log: () => {},
  });
  const body = await res.json();
  if (res.status !== 403 || body.error !== "account_not_allowed") {
    throw new Error("ownership false pas rejetee");
  }
}));

Deno.test("client count filtre correctement ses actions", withEnv(async () => {
  const res = await handleRequest(request({ action: "count" }), {
    fetch: makeFetch(),
    log: () => {},
  });
  const body = await res.json();
  if (res.status !== 200 || body.pending_count !== 3 || body.blocking_count !== 1 || body.client_required_count !== 2) {
    throw new Error(`count client inattendu: ${JSON.stringify(body)}`);
  }
  if (body.counts_by_severity.info !== 1 || body.counts_by_severity.warning !== 1 || body.counts_by_severity.error !== 1) {
    throw new Error("counts_by_severity client incorrect");
  }
}));

Deno.test("internal count peut filtrer par audience admin", withEnv(async () => {
  const res = await handleRequest(request({ action: "count", audience: "admin" }, "internal-token-not-real"), {
    fetch: makeFetch(),
    log: () => {},
  });
  const body = await res.json();
  if (res.status !== 200 || body.pending_count !== 1 || body.blocking_count !== 1) {
    throw new Error("count internal admin incorrect");
  }
}));

Deno.test("list retourne champs safe et exclut messages internes", withEnv(async () => {
  const res = await handleRequest(request({ action: "list", limit: 5 }), {
    fetch: makeFetch(),
    log: () => {},
  });
  const body = await res.json();
  const text = JSON.stringify(body);
  if (res.status !== 200 || body.actions.length !== 3) throw new Error("list client incorrecte");
  if (text.includes("admin_message") || text.includes("assistant_message") || text.includes("metadata")) {
    throw new Error("list expose des champs internes");
  }
  if (text.includes(FAKE_SECRET) || text.includes("supabase_vault://") || text.includes("hooks.example")) {
    throw new Error("list expose un secret");
  }
}));

Deno.test("list filtre account_id status audience", withEnv(async () => {
  const res = await handleRequest(request({ action: "list", account_id: ACCOUNT_ID, status: "pending", audience: "client" }), {
    fetch: makeFetch(),
    log: () => {},
  });
  const body = await res.json();
  if (res.status !== 200 || body.actions.length !== 2) throw new Error("filtres list incorrects");
  if (!body.actions.every((row: Record<string, unknown>) => row.status === "pending" && row.audience === "client")) {
    throw new Error("rows non filtrees");
  }
}));

Deno.test("limit est clampe et offset fonctionne", withEnv(async () => {
  if (clampLimit(500) !== 100 || clampLimit(0) !== 1 || normalizeOffset(-1) !== 0) {
    throw new Error("clamp helpers incorrects");
  }
  const calls: FetchCall[] = [];
  const res = await handleRequest(request({ action: "list", limit: 1, offset: 1 }), {
    fetch: makeFetch({ calls }),
    log: () => {},
  });
  const body = await res.json();
  if (res.status !== 200 || body.actions.length !== 1 || body.next_offset !== 2) {
    throw new Error("pagination offset incorrecte");
  }
  if (!calls.some((c) => c.url.includes("limit=2") && c.url.includes("offset=1"))) {
    throw new Error("limit+1/offset non appliques");
  }
}));

Deno.test("internal token path fonctionne", withEnv(async () => {
  const calls: FetchCall[] = [];
  const res = await handleRequest(request({ action: "list", audience: "client" }, "internal-token-not-real"), {
    fetch: makeFetch({ calls }),
    log: () => {},
  });
  const body = await res.json();
  if (res.status !== 200 || body.ok !== true) throw new Error("internal token path echoue");
  if (calls.some((c) => c.url.endsWith("/auth/v1/user"))) {
    throw new Error("internal token ne doit pas verifier JWT");
  }
}));

Deno.test("client_id null visible via ownership account_id", withEnv(async () => {
  const res = await handleRequest(request({ action: "list", account_id: ACCOUNT_ID, status: "pending" }), {
    fetch: makeFetch(),
    log: () => {},
  });
  const body = await res.json();
  if (!body.actions.some((row: Record<string, unknown>) => row.action_type === "add_targets")) {
    throw new Error("action client_id null liee au compte non visible");
  }
}));
