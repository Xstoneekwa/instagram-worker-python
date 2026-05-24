import {
  normalizeUsername,
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
