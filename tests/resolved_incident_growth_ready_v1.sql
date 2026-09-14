-- This schema exists only in the disposable test cluster.
alter function restriction_test.reset() rename to reset_legacy;
create function restriction_test.reset() returns void language plpgsql as $$
begin
  perform restriction_test.reset_legacy();
  delete from ig_action_logs where action_type in ('account_admin_status_changed','restriction_resolution_lifecycle_restored');
  delete from commercial_account_lifecycle_states;
  update ig_accounts a set updated_at=h.detected_at from instagram_account_restriction_holds h where h.account_id=a.id;
  update instagram_account_restriction_holds set metadata_safe=metadata_safe-'independent_lifecycle_write_at'-'resolved_growth_ready_v1'-'restriction_pause_updated_at';
  perform restore_resolved_restriction_lifecycle_v1(incident_id) from instagram_account_restriction_holds;
  update worker_heartbeats set git_sha=repeat('a',40),status='idle',last_seen_at=now() where worker_id='run-dispatcher:local-cert';
end $$;
create function restriction_test.reset_ready() returns void language sql as $$
  select restriction_test.reset();
$$;
