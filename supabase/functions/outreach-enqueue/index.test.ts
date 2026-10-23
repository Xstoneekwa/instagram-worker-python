import {
  accessDecision,
  findTemplateTokens,
  handleRequest,
  normalizeUsername,
  parseAllowedAccountIds,
  renderDmTemplate,
  sanitizeMetadata,
  validateForbiddenTopLevelFields,
  validateSource,
  validateUsername,
} from "./index.ts";

Deno.test("normalizes and validates Instagram usernames", () => {
  if (normalizeUsername(" @Muse_Europe ") !== "muse_europe") {
    throw new Error("username was not normalized");
  }
  if (!validateUsername("zisty.home").ok) {
    throw new Error("valid dotted username rejected");
  }
  if (validateUsername("bad@@").ok) {
    throw new Error("invalid username accepted");
  }
});

// Same scoped environment + fetch substitution used by dashboard-actions tests.
// Deno runs without --allow-net: an unmocked request cannot reach any provider.
const ACCOUNT = "10000000-0000-4000-8000-000000000001";
const CLIENT = "20000000-0000-4000-8000-000000000001";
const ACTOR = "30000000-0000-4000-8000-000000000001";
const CAMPAIGN = "40000000-0000-4000-8000-000000000001";
const TEMPLATE = "50000000-0000-4000-8000-000000000001";
type Call = { path: string; method: string; body: Record<string, unknown> };
type EdgeFixture = {
  internal?: boolean; legacy?: boolean; ownership?: boolean; fallback?: boolean;
  mode?: Record<string, unknown>; membership?: Record<string, unknown>;
  modeFailure?: boolean; membershipFailure?: boolean; existing?: boolean;
  templateAccount?: string; templateBody?: string; disabled?: boolean; pending?: number;
};
function expectFixture(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}
async function withEdgeFixture(options: EdgeFixture, test: (run: (
  payload?: Record<string, unknown>, bulk?: boolean, authorized?: boolean,
) => Promise<{ response: Response; body: Record<string, any> }>, calls: Call[]) => Promise<void>) {
  const environment: Record<string, string> = {
    SUPABASE_URL: "https://edge-fixture.invalid", SUPABASE_SERVICE_ROLE_KEY: "service-role-not-real",
    OUTREACH_ENQUEUE_INTERNAL_API_TOKEN: "internal-token-not-real", OUTREACH_ENQUEUE_ALLOWED_ORIGINS: "",
    OUTREACH_ENQUEUE_ALLOWLIST_FALLBACK_ENABLED: options.fallback ? "true" : "false",
    OUTREACH_ENQUEUE_ALLOWED_ACCOUNT_IDS: ACCOUNT, OUTREACH_ENQUEUE_MAX_PENDING_PER_ACCOUNT: "500",
    OUTREACH_ENQUEUE_MAX_BATCH_SIZE: "100",
  };
  const previous = Object.fromEntries(Object.keys(environment).map(key => [key, Deno.env.get(key)]));
  const previousFetch = globalThis.fetch;
  const calls: Call[] = [];
  const reply = (value: unknown, status = 200, headers?: HeadersInit) => new Response(JSON.stringify(value), { status, headers });
  const job = { id: "60000000-0000-4000-8000-000000000001", status: "pending", account_id: ACCOUNT,
    campaign_id: CAMPAIGN, source: "campaign", recipient_username: "offline_alice", recipient_username_normalized: "offline_alice", message_body: "frozen" };
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : String(input));
    expectFixture(url.origin === environment.SUPABASE_URL, "unexpected host in offline fixture");
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    const method = init?.method || "GET";
    calls.push({ path: url.pathname, method, body });
    if (url.pathname === "/auth/v1/user") return reply({ id: ACTOR });
    if (url.pathname.endsWith("/client_can_enqueue_outreach") || url.pathname.endsWith("/client_account_has_outreach_entitlement")) return reply(options.ownership !== false);
    if (url.pathname.endsWith("/outreach_campaign_mode_v1")) return options.modeFailure ? reply({ error: "offline_mode_failure" }, 503) : reply(options.mode || { managed_campaign: !options.legacy, campaign_id: CAMPAIGN });
    if (url.pathname === "/rest/v1/ig_account_dm_settings") return reply([{ outreach_enabled: !options.disabled }]);
    if (url.pathname === "/rest/v1/ig_dm_templates") return reply([{ id: TEMPLATE, account_id: options.templateAccount || ACCOUNT, template_type: "outreach", active: true, body: options.templateBody || "salut {name} / {username} / {account_username}" }]);
    if (url.pathname === "/rest/v1/ig_accounts") return reply([{ username: "offline_owner" }]);
    if (url.pathname === "/rest/v1/ig_dm_jobs") {
      expectFixture(method === "GET", "direct job write forbidden");
      if (new Headers(init?.headers).get("prefer")?.includes("count=exact")) return reply([], 200, { "content-range": `0-0/${options.pending || 0}` });
      return reply(options.existing ? [job] : []);
    }
    if (url.pathname.endsWith("/resolve_outreach_campaign_admission_v1")) return options.membershipFailure ? reply({ error: "offline_foreign_campaign" }, 403) : reply({ ok: true, managed_campaign: true, campaign_id: CAMPAIGN, client_id: CLIENT, campaign_revision: 1, origin: "CLIENT_LIST", recipient_name: "Server Alice", ...options.membership });
    if (url.pathname.endsWith("/admit_outreach_campaign_job_v1") || url.pathname.endsWith("/enqueue_outreach_dm_job")) return reply({ ...job, recipient_username: body.p_recipient_username, message_body: body.p_message_body });
    throw new Error("unmocked offline route: " + url.pathname);
  }) as typeof fetch;
  for (const [key, value] of Object.entries(environment)) Deno.env.set(key, value);
  try {
    await test(async (payload = {}, bulk = false, authorized = true) => {
      const response = await handleRequest(new Request("https://edge-fixture.invalid/" + (bulk ? "outreach/bulk-enqueue" : "outreach/enqueue"), {
        method: "POST", headers: { "content-type": "application/json", ...(authorized ? { authorization: "Bearer " + (options.internal ? "internal-token-not-real" : "client-token-not-real") } : {}) },
        body: JSON.stringify({ account_id: ACCOUNT, campaign_id: CAMPAIGN, source: "campaign", template_id: TEMPLATE,
          recipient_username: "offline_alice", recipient_name: "FORGED OPERATOR NAME", ...payload }),
      }));
      return { response, body: await response.json() };
    }, calls);
  } finally {
    globalThis.fetch = previousFetch;
    for (const [key, value] of Object.entries(previous)) {
      if (value == null) Deno.env.delete(key); else Deno.env.set(key, value);
    }
  }
}
const admissions = (calls: Call[]) => calls.filter(call => /\/(?:admit_outreach_campaign_job_v1|enqueue_outreach_dm_job)$/.test(call.path));

Deno.test("managed CLIENT_LIST renders only persisted server names and uses canonical admission", async () => {
  await withEdgeFixture({}, async (run, calls) => {
    const { response, body } = await run({ metadata: { client_id: "forged", origin: "AI_AGENT", campaign_revision: "99" } });
    expectFixture(response.status === 200 && body.ok, "managed enqueue failed");
    const jobs = admissions(calls); expectFixture(jobs.length === 1 && jobs[0].path.endsWith("/admit_outreach_campaign_job_v1"), "managed admission bypassed");
    expectFixture(jobs[0].body.p_message_body === "salut Server Alice / offline_alice / offline_owner", "untrusted recipient name rendered");
    expectFixture(jobs[0].body.p_account_id === ACCOUNT && jobs[0].body.p_campaign_id === CAMPAIGN && jobs[0].body.p_auth_user_id === ACTOR, "owner/account/campaign binding lost");
    expectFixture(!("p_metadata" in jobs[0].body) && !("p_source" in jobs[0].body), "operator provenance propagated");
  });
});
Deno.test("internal managed producer still requires database entitlement and canonical campaign admission", async () => {
  await withEdgeFixture({ internal: true }, async (run, calls) => {
    expectFixture((await run()).body.ok, "internal managed enqueue failed");
    expectFixture(calls.some(c => c.path.endsWith("/client_account_has_outreach_entitlement")), "database entitlement bypassed");
    expectFixture(admissions(calls)[0].body.p_auth_user_id === null, "internal actor fabricated");
  });
});
Deno.test("managed bulk admits each recipient through the same canonical path", async () => {
  await withEdgeFixture({}, async (run, calls) => {
    const { body } = await run({ recipients: ["offline_alice", "offline_bob"] }, true);
    expectFixture(body.ok && body.summary.accepted === 2, "bulk result mismatch");
    expectFixture(admissions(calls).length === 2 && admissions(calls).every(c => c.path.endsWith("/admit_outreach_campaign_job_v1")), "bulk bypassed canonical admission");
    expectFixture(calls.filter(c => c.path.endsWith("/resolve_outreach_campaign_admission_v1")).length === 2, "bulk recipient membership missing");
  });
});
Deno.test("managed duplicate retains native idempotent job identity", async () => {
  await withEdgeFixture({ existing: true }, async (run, calls) => {
    const { body } = await run();
    expectFixture(body.ok && body.result === "duplicate_existing" && body.job_id === "60000000-0000-4000-8000-000000000001", "duplicate identity changed");
    expectFixture(admissions(calls).length === 1, "duplicate did not use native admission");
  });
});
Deno.test("unmanaged legacy producer preserves its original enqueue and template contract", async () => {
  await withEdgeFixture({ internal: true, legacy: true }, async (run, calls) => {
    const { body } = await run({ recipient_name: "Legacy Name", metadata: { note: "legacy" } });
    const jobs = admissions(calls);expectFixture(body.ok && jobs.length === 1 && jobs[0].path.endsWith("/enqueue_outreach_dm_job"), "legacy enqueue changed");
    expectFixture(jobs[0].body.p_message_body === "salut Legacy Name / offline_alice / offline_owner", "legacy rendering changed");
    expectFixture(JSON.stringify(jobs[0].body.p_metadata) === JSON.stringify({ note: "legacy" }), "legacy metadata changed");
    expectFixture(!calls.some(c => c.path.endsWith("/resolve_outreach_campaign_admission_v1")), "legacy path acquired managed membership");
  });
});
for (const [name, options] of [
  ["AI producer remains reserved", { membership: { origin: "AI_AGENT" } }],
  ["foreign campaign response rejected", { membership: { campaign_id: "foreign" } }],
  ["malformed membership rejected", { membership: { ok: false } }],
  ["missing managed proof rejected", { membership: { managed_campaign: false } }],
  ["nonselected or foreign recipient RPC failure stops", { membershipFailure: true }],
  ["malformed campaign mode rejected", { mode: {} }],
  ["campaign authority RPC failure stops", { modeFailure: true }],
  ["foreign template rejected", { templateAccount: ACTOR }],
  ["unsupported template token rejected", { templateBody: "{password}" }],
  ["disabled Outreach refuses admission", { disabled: true }],
  ["pending queue cap preserved", { pending: 500 }],
  ["foreign tenant cannot enqueue", { ownership: false }],
  ["operational allowlist cannot establish managed authority", { ownership: false, fallback: true, internal: true }],
] as [string, EdgeFixture][]) Deno.test(name, async () => {
  await withEdgeFixture(options, async (run, calls) => {
    const { response, body } = await run();
    expectFixture(response.status >= 400 && !body.ok, "unsafe request accepted: " + name);
    expectFixture(admissions(calls).length === 0, "unsafe request fell back or admitted: " + name);
  });
});
Deno.test("missing authentication creates no job and reads no backend authority", async () => {
  await withEdgeFixture({}, async (run, calls) => {
    const { response } = await run({}, false, false);
    expectFixture(response.status === 401 && calls.length === 0, "unauthenticated request reached backend");
  });
});

Deno.test("allows only Outreach producer sources", () => {
  if (!validateSource("dashboard").ok) {
    throw new Error("dashboard source rejected");
  }
  if (validateSource("welcome_scan").ok) {
    throw new Error("welcome_scan accepted for outreach");
  }
  if (validateSource("import_csv").ok) {
    throw new Error("import_csv should require enum migration first");
  }
});

Deno.test("rejects forbidden top-level fields and dangerous metadata", () => {
  const forbidden = validateForbiddenTopLevelFields({ account_id: "x", status: "pending" });
  if (forbidden !== "status") {
    throw new Error("status field was not rejected");
  }
  const badMetadata = sanitizeMetadata({ handoff: "unfollow" });
  if (badMetadata.ok) {
    throw new Error("unfollow handoff metadata accepted");
  }
  const metadata = sanitizeMetadata({
    external_request_id: "req-1",
    reserved_by: "client",
    ignored: "drop-me",
  });
  if (metadata.ok) {
    throw new Error("reserved_by metadata accepted");
  }
});

// This test documents the whitelist behavior separately from dangerous-key rejection.
Deno.test("sanitizes safe metadata and drops unknown safe-by-omission fields", () => {
  const metadata = sanitizeMetadata({
    external_request_id: "req-1",
    note: "hello",
    arbitrary: "ignored",
  });
  if (!metadata.ok) throw new Error("safe metadata rejected");
  if (metadata.metadata.external_request_id !== "req-1") {
    throw new Error("external_request_id missing");
  }
  if ("arbitrary" in metadata.metadata) {
    throw new Error("unknown metadata key was not dropped");
  }
});

Deno.test("entry2a access decision requires database entitlement by default", () => {
  const accountId = "42c625c2-e761-4100-8a9d-7ae1373de97d";
  const missing = accessDecision(false, accountId);
  if (missing.ok || missing.error !== "account_outreach_entitlement_required") {
    throw new Error("missing ownership/entitlement was not rejected");
  }

  const active = accessDecision(true, accountId);
  if (!active.ok || active.mode !== "db") {
    throw new Error("active database entitlement was not accepted");
  }
});

Deno.test("entry2a access decision rejects failed ownership checks unless explicit fallback is enabled", () => {
  const accountId = "42c625c2-e761-4100-8a9d-7ae1373de97d";
  const failed = accessDecision(false, accountId, {
    dbCheckFailed: true,
    allowlistFallback: false,
    allowlistAllowed: true,
  });
  if (failed.ok || failed.error !== "account_ownership_check_failed") {
    throw new Error("failed DB ownership check was not rejected");
  }

  const fallback = accessDecision(false, accountId, {
    dbCheckFailed: true,
    allowlistFallback: true,
    allowlistAllowed: true,
  });
  if (!fallback.ok || fallback.mode !== "allowlist_fallback") {
    throw new Error("explicit ops allowlist fallback did not allow account");
  }
});

Deno.test("entry2a allowlist parsing trims empty values", () => {
  const parsed = parseAllowedAccountIds(" a, ,b ,, c ");
  if (parsed.join("|") !== "a|b|c") {
    throw new Error("allowlist parser did not trim empty values");
  }
});

Deno.test("renders supported Outreach template variables", () => {
  const rendered = renderDmTemplate("Salut {name}, c'est {{account_username}}.", {
    recipientUsername: "justperfect.eu",
    recipientName: "",
    accountUsername: "j_automatise_pour_toi",
  });
  if (!rendered.ok) throw new Error("supported template was rejected");
  if (rendered.renderedBody !== "Salut justperfect.eu, c'est j_automatise_pour_toi.") {
    throw new Error("template variables were not rendered");
  }
  if (rendered.fallbacksUsed.join("|") !== "name:recipient_username") {
    throw new Error("name fallback was not reported");
  }
});

Deno.test("rejects unsupported Outreach template variables", () => {
  const rendered = renderDmTemplate("Salut {company}.", {
    recipientUsername: "justperfect.eu",
    accountUsername: "j_automatise_pour_toi",
  });
  if (rendered.ok || rendered.error !== "unsupported_template_variable") {
    throw new Error("unsupported variable was not rejected");
  }
  if (findTemplateTokens("Salut {{ username }} et {name}").join("|") !== "username|name") {
    throw new Error("template token parser failed");
  }
});
