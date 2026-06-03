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
const BLOCKING_ACTION_ID = "10000000-0000-4000-8000-000000000001";
const PENDING_VERIFICATION_ACTION_ID = "10000000-0000-4000-8000-000000000002";
const ADMIN_ACTION_ID = "10000000-0000-4000-8000-000000000003";
const NON_BLOCKING_REQUIRED_ACTION_ID = "10000000-0000-4000-8000-000000000004";
const OTHER_CLIENT_ACTION_ID = "10000000-0000-4000-8000-000000000005";
const CLIENT_RESOLVABLE_ACTION_ID = "10000000-0000-4000-8000-000000000006";
const TERMINAL_ACTION_ID = "10000000-0000-4000-8000-000000000007";
const EMAIL_VERIFICATION_ACTION_ID = "10000000-0000-4000-8000-000000000008";

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
  {
    id: CLIENT_RESOLVABLE_ACTION_ID,
    client_id: CLIENT_ID,
    account_id: ACCOUNT_ID,
    action_type: "review_targets",
    status: "pending",
    severity: "info",
    audience: "client",
    requires_client_action: false,
    blocking_campaign: false,
    title: "Review targets",
    safe_client_message: "Targets are being reviewed.",
    action_label: "View targets",
    action_deep_link: "/accounts/42/targets",
    metadata: { safe: "ignored" },
    admin_message: "admin-only review note",
    assistant_message: "assistant-only review note",
    incident_id: "22222222-2222-4222-8222-222222222222",
    secret_ref: "supabase_vault://22222222-2222-4222-8222-222222222222",
    webhook_url: "https://hooks.example.invalid/another-secret",
    created_at: "2026-05-25T15:00:00Z",
    updated_at: "2026-05-25T15:01:00Z",
  },
  {
    id: TERMINAL_ACTION_ID,
    client_id: CLIENT_ID,
    account_id: ACCOUNT_ID,
    action_type: "resolved_smoke",
    status: "resolved",
    severity: "info",
    audience: "client",
    requires_client_action: false,
    blocking_campaign: false,
    title: "Resolved action",
    safe_client_message: "Already resolved.",
    action_label: "View",
    action_deep_link: "/accounts/42",
    resolved_at: "2026-05-25T14:05:00Z",
    created_at: "2026-05-25T14:00:00Z",
    updated_at: "2026-05-25T14:05:00Z",
  },
  {
    id: EMAIL_VERIFICATION_ACTION_ID,
    client_id: CLIENT_ID,
    account_id: ACCOUNT_ID,
    action_type: "enter_email_verification_code",
    status: "pending",
    severity: "warning",
    audience: "client",
    requires_client_action: true,
    blocking_campaign: true,
    title: "Email verification code required",
    safe_client_message: "Instagram is waiting for the email verification code.",
    action_label: "Enter code",
    action_deep_link: "/instagram-dashboard/credentials-actions",
    metadata: { safe: "ignored" },
    created_at: "2026-05-25T13:00:00Z",
    updated_at: "2026-05-25T13:01:00Z",
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
  for (const idFilter of params.getAll("id")) {
    if (idFilter.startsWith("eq.")) {
      const id = idFilter.slice(3);
      result = result.filter((row) => row.id === id);
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
  transitionStatus?: number;
  transitionError?: string;
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
    if (href.includes("/rest/v1/rpc/submit_account_verification_code")) {
      const body = bodyOf(init) ?? {};
      if (body.p_verification_code === FAKE_SECRET) {
        return json({ message: "verification_code_invalid" }, 400);
      }
      return json({
        ok: true,
        action_id: body.p_action_id,
        account_id: body.p_account_id,
        status: "code_submitted",
      });
    }
    if (href.includes("/rest/v1/rpc/transition_account_dashboard_action")) {
      if (options.transitionStatus && options.transitionStatus >= 400) {
        return json({ message: options.transitionError ?? "invalid_dashboard_action_transition" }, options.transitionStatus);
      }
      const body = bodyOf(init) ?? {};
      const row = FIXTURE_ROWS.find((item) => item.id === body.p_action_id);
      if (!row) return json({ message: "dashboard_action_not_found" }, 404);
      const status = String(body.p_new_status);
      const timestamp = "2026-05-25T21:30:00Z";
      return json({
        ...row,
        status,
        acknowledged_at: status === "acknowledged" ? (row.acknowledged_at ?? timestamp) : row.acknowledged_at ?? null,
        dismissed_at: status === "dismissed" ? timestamp : row.dismissed_at ?? null,
        resolved_at: status === "resolved" ? timestamp : row.resolved_at ?? null,
        updated_at: timestamp,
        metadata: { password: FAKE_SECRET, safe: "must-not-leak" },
        admin_message: "admin-only transition details",
        assistant_message: "assistant-only transition details",
        incident_id: "33333333-3333-4333-8333-333333333333",
        secret_ref: "supabase_vault://33333333-3333-4333-8333-333333333333",
        webhook_url: "https://hooks.example.invalid/rpc-secret",
      });
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
  const result = validatePayload({ action: "unknown" });
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
  if (res.status !== 200 || body.pending_count !== 5 || body.blocking_count !== 2 || body.client_required_count !== 3) {
    throw new Error(`count client inattendu: ${JSON.stringify(body)}`);
  }
  if (body.counts_by_severity.info !== 2 || body.counts_by_severity.warning !== 2 || body.counts_by_severity.error !== 1) {
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
  if (res.status !== 200 || body.actions.length !== 5) throw new Error("list client incorrecte");
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
  if (res.status !== 200 || body.actions.length !== 4) throw new Error("filtres list incorrects");
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

Deno.test("mutation rejette auth manquante", withEnv(async () => {
  const res = await handleRequest(new Request("https://example.test", {
    method: "POST",
    body: JSON.stringify({ action: "acknowledge", action_id: BLOCKING_ACTION_ID }),
  }));
  const body = await res.json();
  if (res.status !== 401 || body.error !== "unauthorized") throw new Error("mutation sans auth acceptee");
}));

Deno.test("mutation rejette action_id invalide ou manquant et reason trop longue", () => {
  const missing = validatePayload({ action: "acknowledge" });
  if (missing.ok || missing.error !== "action_id_invalid") throw new Error("action_id manquant accepte");
  const invalid = validatePayload({ action: "dismiss", action_id: "not-a-uuid" });
  if (invalid.ok || invalid.error !== "action_id_invalid") throw new Error("action_id invalide accepte");
  const longReason = validatePayload({ action: "resolve", action_id: CLIENT_RESOLVABLE_ACTION_ID, reason: "x".repeat(501) });
  if (longReason.ok || longReason.error !== "reason_too_long") throw new Error("reason trop longue acceptee");
});

Deno.test("client acknowledge sa propre action client", withEnv(async () => {
  const calls: FetchCall[] = [];
  const res = await handleRequest(request({
    action: "acknowledge",
    action_id: BLOCKING_ACTION_ID,
    reason: "vu",
  }), {
    fetch: makeFetch({ calls }),
    log: () => {},
  });
  const body = await res.json();
  const text = JSON.stringify(body);
  if (res.status !== 200 || body.dashboard_action?.status !== "acknowledged" || !body.dashboard_action?.acknowledged_at) {
    throw new Error(`acknowledge client incorrect: ${text}`);
  }
  const rpc = calls.find((c) => c.url.includes("transition_account_dashboard_action"));
  if (!rpc) throw new Error("RPC transition non appelee");
  if (rpc.body?.p_actor_type !== "client" || rpc.body?.p_actor_id !== AUTH_USER_ID || rpc.body?.p_reason !== "vu") {
    throw new Error("actor client/reason incorrects");
  }
  if (JSON.stringify(rpc.body?.p_metadata).includes(FAKE_SECRET)) throw new Error("metadata RPC contient un secret");
}));

Deno.test("client ne peut pas acknowledge audience admin", withEnv(async () => {
  const res = await handleRequest(request({ action: "acknowledge", action_id: ADMIN_ACTION_ID }), {
    fetch: makeFetch(),
    log: () => {},
  });
  const body = await res.json();
  if (res.status !== 403 || body.error !== "audience_not_allowed") {
    throw new Error("client a mute audience admin");
  }
}));

Deno.test("client ne peut pas muter action hors ownership", withEnv(async () => {
  const res = await handleRequest(request({ action: "acknowledge", action_id: OTHER_CLIENT_ACTION_ID }), {
    fetch: makeFetch({ clientAccess: false }),
    log: () => {},
  });
  const body = await res.json();
  if (res.status !== 403 || body.error !== "account_not_allowed") {
    throw new Error("client a mute hors ownership");
  }
}));

Deno.test("client ne peut pas dismiss action bloquante requise", withEnv(async () => {
  const res = await handleRequest(request({ action: "dismiss", action_id: BLOCKING_ACTION_ID }), {
    fetch: makeFetch(),
    log: () => {},
  });
  const body = await res.json();
  if (res.status !== 403 || body.error !== "transition_not_allowed") {
    throw new Error("dismiss bloquant requis accepte");
  }
}));

Deno.test("client ne peut pas dismiss ou resolve pending_verification", withEnv(async () => {
  for (const action of ["dismiss", "resolve"]) {
    const res = await handleRequest(request({ action, action_id: PENDING_VERIFICATION_ACTION_ID }), {
      fetch: makeFetch(),
      log: () => {},
    });
    const body = await res.json();
    if (res.status !== 403 || body.error !== "transition_not_allowed") {
      throw new Error(`${action} pending_verification accepte`);
    }
  }
}));

Deno.test("client ne peut pas resolve action bloquante ou requise", withEnv(async () => {
  const blocking = await handleRequest(request({ action: "resolve", action_id: BLOCKING_ACTION_ID }), {
    fetch: makeFetch(),
    log: () => {},
  });
  const required = await handleRequest(request({ action: "resolve", action_id: NON_BLOCKING_REQUIRED_ACTION_ID }), {
    fetch: makeFetch(),
    log: () => {},
  });
  const blockingBody = await blocking.json();
  const requiredBody = await required.json();
  if (blocking.status !== 403 || blockingBody.error !== "transition_not_allowed") {
    throw new Error("resolve bloquant accepte");
  }
  if (required.status !== 403 || requiredBody.error !== "transition_not_allowed") {
    throw new Error("resolve action requise accepte");
  }
}));

Deno.test("client resolve action non bloquante non requise", withEnv(async () => {
  const res = await handleRequest(request({ action: "resolve", action_id: CLIENT_RESOLVABLE_ACTION_ID }), {
    fetch: makeFetch(),
    log: () => {},
  });
  const body = await res.json();
  if (res.status !== 200 || body.dashboard_action?.status !== "resolved" || !body.dashboard_action?.resolved_at) {
    throw new Error("resolve client safe echoue");
  }
}));

Deno.test("internal resolve pending_verification et dismiss bloquante", withEnv(async () => {
  const resolve = await handleRequest(request({
    action: "resolve",
    action_id: PENDING_VERIFICATION_ACTION_ID,
  }, "internal-token-not-real"), {
    fetch: makeFetch(),
    log: () => {},
  });
  const dismiss = await handleRequest(request({
    action: "dismiss",
    action_id: BLOCKING_ACTION_ID,
  }, "internal-token-not-real"), {
    fetch: makeFetch(),
    log: () => {},
  });
  const resolveBody = await resolve.json();
  const dismissBody = await dismiss.json();
  if (resolve.status !== 200 || resolveBody.dashboard_action?.status !== "resolved") {
    throw new Error("internal resolve pending_verification echoue");
  }
  if (dismiss.status !== 200 || dismissBody.dashboard_action?.status !== "dismissed") {
    throw new Error("internal dismiss bloquante echoue");
  }
}));

Deno.test("mutation response exclut metadata messages internes et secrets", withEnv(async () => {
  const res = await handleRequest(request({ action: "resolve", action_id: CLIENT_RESOLVABLE_ACTION_ID }), {
    fetch: makeFetch(),
    log: () => {},
  });
  const body = await res.json();
  const text = JSON.stringify(body);
  if (res.status !== 200) throw new Error("mutation safe response echoue");
  for (const forbidden of ["metadata", "admin_message", "assistant_message", "incident_id", FAKE_SECRET, "secret_ref", "supabase_vault://", "hooks.example"]) {
    if (text.includes(forbidden)) throw new Error(`mutation response expose ${forbidden}`);
  }
}));

Deno.test("mutation terminale et erreur RPC retournent transition_not_allowed safe", withEnv(async () => {
  const terminal = await handleRequest(request({ action: "acknowledge", action_id: TERMINAL_ACTION_ID }), {
    fetch: makeFetch(),
    log: () => {},
  });
  const rpcError = await handleRequest(request({ action: "acknowledge", action_id: BLOCKING_ACTION_ID }, "internal-token-not-real"), {
    fetch: makeFetch({ transitionStatus: 400, transitionError: "invalid_dashboard_action_transition" }),
    log: () => {},
  });
  const terminalBody = await terminal.json();
  const rpcErrorBody = await rpcError.json();
  if (terminal.status !== 409 || terminalBody.error !== "transition_not_allowed") {
    throw new Error("transition terminale pas rejetee");
  }
  if (rpcError.status !== 409 || rpcErrorBody.error !== "transition_not_allowed") {
    throw new Error("erreur RPC transition pas mappee");
  }
}));

Deno.test("submit_verification_code happy path sans fuite", withEnv(async () => {
  const res = await handleRequest(request({
    action: "submit_verification_code",
    action_id: EMAIL_VERIFICATION_ACTION_ID,
    account_id: ACCOUNT_ID,
    verification_code: "123456",
  }), {
    fetch: makeFetch(),
    log: () => {},
  });
  const body = await res.json();
  const text = JSON.stringify(body);
  if (res.status !== 200 || body.ok !== true || body.status !== "code_submitted") {
    throw new Error("submit_verification_code happy path echoue");
  }
  for (const forbidden of ["123456", "verification_code", "secret_ref", "supabase_vault://"]) {
    if (text.includes(forbidden)) throw new Error(`submit response expose ${forbidden}`);
  }
}));

Deno.test("submit_verification_code rejette ownership false", withEnv(async () => {
  const res = await handleRequest(request({
    action: "submit_verification_code",
    action_id: EMAIL_VERIFICATION_ACTION_ID,
    account_id: ACCOUNT_ID,
    verification_code: "123456",
  }), {
    fetch: makeFetch({ clientAccess: false }),
    log: () => {},
  });
  const body = await res.json();
  if (res.status !== 403 || body.error !== "account_not_allowed") {
    throw new Error("submit_verification_code ownership false pas rejete");
  }
}));
