-- CT-4 - Target lifecycle audit operations.
--
-- Extends safe CT audit enums for archive/restore/reset lifecycle events only.
-- This does not add hard delete, activate SearchApi, touch worker Python/follow
-- runtime, or compute FBR/performance metrics.

alter table public.ct_target_audit_events
  drop constraint if exists ct_target_audit_operation_check,
  drop constraint if exists ct_target_audit_result_check;

alter table public.ct_target_audit_events
  add constraint ct_target_audit_operation_check
    check (
      operation in (
        'target_add_single',
        'target_add_bulk',
        'target_verify',
        'target_archive',
        'target_restore',
        'target_reset'
      )
    ),
  add constraint ct_target_audit_result_check
    check (
      result in (
        'accepted',
        'duplicate',
        'rejected',
        'review',
        'failed',
        'archived',
        'restored'
      )
    );

comment on table public.ct_target_audit_events is
  'Safe CT target add/bulk/verify/archive/restore/reset audit events. Stores counts and reasons only, never raw provider responses, secrets, tokens, cookies, sessions, raw HTML, XML, screenshots or logs.';
