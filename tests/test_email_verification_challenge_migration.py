from pathlib import Path
import unittest


class EmailVerificationChallengeMigrationTest(unittest.TestCase):
    def test_submit_verification_code_allows_new_submission_after_consumed_history(self) -> None:
        migration = Path("supabase/migrations/20260603143000_email_verification_challenge_flow.sql").read_text()

        self.assertNotIn("verification_code_already_consumed", migration)
        self.assertIn("v_submission.status in ('consumed', 'expired', 'failed')", migration)
        self.assertIn("insert into public.account_verification_code_submissions", migration)


if __name__ == "__main__":
    unittest.main()

