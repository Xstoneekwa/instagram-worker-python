from pathlib import Path


MIGRATION = Path(
    "supabase/migrations/20260714173000_reactivate_safe_unverified_welcome_jobs.sql"
)


def test_reactivation_is_limited_to_unattempted_scan_overflow_cancellations() -> None:
    sql = MIGRATION.read_text()

    assert "v_job.status = 'cancelled'" in sql
    assert "v_job.skip_reason = 'cleanup_full_cycle_minimum_extra_welcome_jobs'" in sql
    assert "v_job.sent_at is null" in sql
    assert "coalesce(v_job.attempts, 0) = 0" in sql
    assert "v_follower.welcome_dm_status = 'pending'" in sql
    assert "for update" in sql.lower()


def test_sent_and_other_terminal_jobs_remain_terminal() -> None:
    sql = MIGRATION.read_text()

    assert "v_job.status in ('sent', 'skipped', 'failed', 'cancelled')" in sql
    assert "reactivation_reason', 'safe_unverified_scan_overflow_job'" in sql
