#!/usr/bin/env node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

function loadEnvFile(path) {
  try {
    const raw = readFileSync(path, "utf8");
    for (const line of raw.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const idx = trimmed.indexOf("=");
      if (idx <= 0) continue;
      const key = trimmed.slice(0, idx).trim();
      let value = trimmed.slice(idx + 1).trim();
      if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
        value = value.slice(1, -1);
      }
      if (!process.env[key]) process.env[key] = value;
    }
  } catch {
    // optional env file
  }
}

loadEnvFile(resolve("/Users/admin/Projects/boost-ai-frontend/.env.local"));

const accountId = process.argv[2] || "d8e9124d-f563-413d-84de-d39ca1355441";
const username = process.argv[3] || "lorielebras_autom";
const baselineIso = process.argv[4] || new Date().toISOString();
const supabaseUrl = process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL;
const supabaseKey = process.env.SUPABASE_SERVICE_ROLE_KEY;
const relayKey = process.env.BOTAPP_RELAY_API_KEY;
const base = "https://www.boostmybusinesses.com";

if (!supabaseUrl || !supabaseKey || !relayKey) {
  console.error(`${new Date().toISOString()} WATCHER_FATAL missing env (SUPABASE_URL/SERVICE_ROLE/RELAY)`);
  process.exit(1);
}

const headers = {
  apikey: supabaseKey,
  authorization: `Bearer ${supabaseKey}`,
  accept: "application/json",
};

let lastSig = "";
let seenRequest = false;
let seenRun = false;
let consecutiveErrors = 0;

function safe(obj) {
  return JSON.stringify(obj).replace(/secret_ref|password|service_role|token|authorization/ig, "redacted_key");
}

async function rest(table, params) {
  const url = new URL(`${supabaseUrl}/rest/v1/${table}`);
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value);
  const response = await fetch(url, { headers });
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    return [];
  }
}

async function count(table, params) {
  const url = new URL(`${supabaseUrl}/rest/v1/${table}`);
  for (const [key, value] of Object.entries({ ...params, select: "id" })) url.searchParams.set(key, value);
  const response = await fetch(url, { headers: { ...headers, prefer: "count=exact" } });
  await response.text();
  return Number((response.headers.get("content-range") || "*/0").split("/").pop() || 0);
}

async function api(path) {
  const response = await fetch(`${base}${path}`, {
    headers: { "x-botapp-relay-key": relayKey, accept: "application/json" },
  });
  const text = await response.text();
  try {
    return { http: response.status, json: JSON.parse(text) };
  } catch {
    return { http: response.status, json: null, text: text.slice(0, 160) };
  }
}

async function tick() {
  const [reqs, runs, client, creds, actions, socialCount, accountSessions] = await Promise.all([
    rest("account_run_requests", {
      select: "id,status,requested_run_type,run_id,created_at,claimed_at,claimed_by,metadata_safe",
      account_id: `eq.${accountId}`,
      created_at: `gte.${baselineIso}`,
      order: "created_at.desc",
      limit: "3",
    }),
    rest("ig_runs", {
      select: "id,status,worker_type,created_at,started_at,finished_at,completed_at,totals,performance_summary",
      account_id: `eq.${accountId}`,
      created_at: `gte.${baselineIso}`,
      order: "created_at.desc",
      limit: "3",
    }),
    rest("client_instagram_accounts", {
      select: "login_status,provisioning_status,onboarding_status,updated_at",
      account_id: `eq.${accountId}`,
      limit: "1",
    }),
    rest("account_credentials", {
      select: "status,reauth_required,reauth_reason,updated_at",
      account_id: `eq.${accountId}`,
      order: "credentials_version.desc",
      limit: "1",
    }),
    rest("account_dashboard_actions", {
      select: "action_type,status,blocking_campaign,updated_at",
      account_id: `eq.${accountId}`,
      action_type: "eq.submit_instagram_credentials",
      order: "updated_at.desc",
      limit: "3",
    }),
    count("ig_action_logs", {
      account_id: `eq.${accountId}`,
      created_at: `gte.${baselineIso}`,
      action_type: "in.(follow,unfollow,like,comment,dm,send_dm,story_watch,watch_story,target_discovery)",
    }),
    count("account_run_requests", {
      account_id: `eq.${accountId}`,
      requested_run_type: "eq.account_session",
      created_at: `gte.${baselineIso}`,
    }),
  ]);

  const req = Array.isArray(reqs) ? reqs[0] || null : null;
  const run = Array.isArray(runs) ? runs[0] || null : null;
  const progress = req?.id
    ? await api(`/api/instagram-dashboard/runs/progress?account_id=${accountId}&request_id=${req.id}&audience=admin`)
    : null;
  const progressData = progress?.json?.data || progress?.json || null;
  const progressBlob = JSON.stringify(progressData || {});
  const reqBlob = JSON.stringify(req?.metadata_safe || {});
  const runBlob = JSON.stringify(run?.metadata || {});

  const summary = {
    request_id: req?.id || null,
    request_status: req?.status || null,
    requested_run_type: req?.requested_run_type || null,
    dispatcher_id: req?.claimed_by || null,
    run_id: req?.run_id || run?.id || null,
    run_status: run?.status || null,
    run_type: run?.worker_type || null,
    progress_http: progress?.http || null,
    progress_status: progressData?.status || progressData?.global_status || progressData?.request_status || null,
    app_start_ok: /app_start_ok[^a-zA-Z0-9_]*true/i.test(`${progressBlob}${reqBlob}${runBlob}`),
    action_required: /action_required|challenge|checkpoint|2fa|code_required/i.test(progressBlob),
    login_status: Array.isArray(client) ? client[0]?.login_status : null,
    provisioning_status: Array.isArray(client) ? client[0]?.provisioning_status : null,
    credential_status: Array.isArray(creds) ? creds[0]?.status : null,
    reauth_required: Array.isArray(creds) ? creds[0]?.reauth_required : null,
    dashboard_action: Array.isArray(actions) ? actions[0] || null : null,
    social_logs_since_baseline: socialCount,
    account_session_runs_since_baseline: accountSessions,
  };

  const sig = safe(summary);
  if (sig !== lastSig) {
    const events = [];
    if (summary.request_id && !seenRequest) {
      events.push("REQUEST_CREATED");
      seenRequest = true;
    }
    if (summary.run_id && !seenRun) {
      events.push("RUN_LINKED");
      seenRun = true;
    }
    if (summary.action_required) events.push("ACTION_REQUIRED");
    if (summary.login_status === "connected") events.push("CONNECTED");
    if (summary.social_logs_since_baseline > 0 || summary.account_session_runs_since_baseline > 0) {
      events.push("SOCIAL_ACTIVITY_ALERT");
    }
    if (!events.length) events.push("STATE_CHANGE");
    console.log(`${new Date().toISOString()} ${events.join(",")} ${sig}`);
    lastSig = sig;
  }
}

console.log(`${new Date().toISOString()} WATCHER_ARMED ${safe({ account_id: accountId, username, baseline_iso: baselineIso, mode: "read_only" })}`);

async function loop() {
  try {
    await tick();
    consecutiveErrors = 0;
  } catch (error) {
    consecutiveErrors += 1;
    const message = error instanceof Error ? error.message : String(error);
    console.log(`${new Date().toISOString()} WATCHER_ERROR ${message} consecutive=${consecutiveErrors}`);
  }
}

await loop();
setInterval(loop, 5000);
