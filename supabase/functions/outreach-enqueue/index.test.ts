import {
  accessDecision,
  normalizeUsername,
  parseAllowedAccountIds,
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
