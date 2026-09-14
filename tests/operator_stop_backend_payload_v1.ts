// Actual unchanged Backend policy and payload serializer; no fake database.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const backend = resolve(process.argv[2]);
const { canonicalOperatorStopContinuationAuthorized } = await import(pathToFileURL(
  resolve(backend, 'lib/instagram-dashboard/auto-restart-candidate-policy.ts')).href);
const { buildAutoRestartResumePlanMetadata } = await import(pathToFileURL(
  resolve(backend, 'lib/instagram-dashboard/auto-restart-resume-metadata.ts')).href);
const input = JSON.parse(readFileSync(0, 'utf8'));
if (!canonicalOperatorStopContinuationAuthorized(input.authority)) {
  throw new Error('actual_backend_operator_stop_policy_rejected');
}
const metadata = buildAutoRestartResumePlanMetadata(input.candidate, new Date(input.now));
process.stdout.write(JSON.stringify({ auto_restart: true, source: 'auto_restart_tick',
  worker_id: 'run-dispatcher:local-cert', execution_worker_id: 'run-dispatcher:local-cert',
  ...metadata }));
