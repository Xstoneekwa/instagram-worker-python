// Offline execution of the actual compatible backend's pure contract functions.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { stripTypeScriptTypes } from 'node:module';
import path from 'node:path';

const [backendRoot, serializedStates] = process.argv.slice(2);
assert.ok(backendRoot && serializedStates, 'backend root and Worker states required');
const states = JSON.parse(serializedStates);
const sources = {};
function read(relative) {
  const bytes = readFileSync(path.join(backendRoot, relative));
  sources[relative] = createHash('sha256').update(bytes).digest('hex');
  return bytes.toString('utf8');
}
const source = read('lib/instagram-dashboard/resume-state-contract.ts');
const code = stripTypeScriptTypes(source);
const backend = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
async function pureModule(relative) {
  return import(`data:text/javascript;base64,${Buffer.from(stripTypeScriptTypes(read(relative))).toString('base64')}`);
}
const authorization = await pureModule('lib/instagram-dashboard/incident-resume-authorization.ts');
const policy = await pureModule('lib/instagram-dashboard/auto-restart-candidate-policy.ts');
assert.deepEqual([...backend.DB_ALLOWED_RESUME_STATES].sort(), [...states].sort());
assert.equal(backend.backendUnderstandsEveryDbResumeState(states), true);
assert.equal(backend.backendUnderstandsEveryDbResumeState([...states, 'partial_resumable']), false);
assert.equal(backend.backendUnderstandsEveryDbResumeState(['unknown_state']), false);
const matrix = [];
for (const state of states) {
  const input = {resumeState: state, sourceRunStatus: 'completed', sourceRequestStatus: 'completed',
    activeRunExists: false, activeRequestExists: false, liveDeviceLockExists: false};
  const verdict = backend.resolveRunActiveProof(input);
  assert.equal(verdict.proven, state !== 'run_active');
  if (state === 'run_active') {
    assert.equal(verdict.reason, 'STALE_RESUME_PLAN_STATE');
    assert.equal(backend.resolveRunActiveProof({...input, sourceRunStatus: 'running', activeRunExists: true}).proven, true);
    assert.equal(backend.resolveRunActiveProof({...input, liveDeviceLockExists: true}).proven, true);
    assert.equal(backend.resolveRunActiveProof({...input, sourceRunStatus: 'running'}).proven, false);
  }
  const eligibility = policy.resolveAccountRestartEligibility(verdict.proven ? [] : [verdict.reason]);
  assert.equal(eligibility.eligible, state !== 'run_active');
  // Query-only fake: any attempt to insert/update/RPC has no implementation and fails.
  const queried = [];
  const fake = {from(table) {
    queried.push(table);
    const query = {select() {return this;}, eq() {return this;}, order() {return this;},
      limit() {return this;}, maybeSingle: async () => ({data: table === 'account_session_resume_plans'
        ? {id: 'synthetic-plan', resume_state: state, scheduled_window_start: '2026-01-01T00:00:00Z',
          scheduled_window_end: '2026-01-01T06:00:00Z'} : null})};
    return query;
  }};
  const human = await authorization.evaluateReadyToResume(fake, {
    id: 'synthetic-incident', account_id: 'synthetic-account', run_id: 'synthetic-run',
    status: 'open', incident_type: 'run_worker_failure', metadata: {},
  }, new Date('2026-01-01T01:00:00Z'));
  assert.equal(human.eligible, state === 'awaiting_human_resume_authorization');
  assert.deepEqual(queried, ['account_session_resume_plans', 'incident_resume_authorizations']);
  matrix.push({state, understood: true, noLiveProofVerdict: verdict,
    autoRestartSafetyEligible: eligibility.eligible, humanAuthorizationEligible: human.eligible});
}
// Integration assertions supplement (not replace) the executable pure-function fixtures.
const projection = read('app/instagram-dashboard/auto-restart-data.ts');
assert.match(projection, /resumeState: readString\(canonicalPlanRow\?\.resume_state/);
assert.match(projection, /resumeState: reliability\.resumeState/);
assert.match(projection, /if \(!runActiveProof\.proven\) blockingReasons\.push\(runActiveProof\.reason\)/);
const tick = read('lib/instagram-dashboard/auto-restart-tick.ts');
assert.match(tick, /readString\(storedPlan\.resume_state\) !== "awaiting_human_resume_authorization"/);
assert.match(tick, /consume_resume_authorization_and_create_request_v3/);
assert.match(tick, /if \(!forceDryRun && !options\.manual\) \{[\s\S]*reconcile_stale_account_session_resume_plans_v1/);
console.log(JSON.stringify({status: 'PASS', matrix, source_files: sources}));
