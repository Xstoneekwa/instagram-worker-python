from __future__ import annotations

import unittest
from unittest.mock import patch

import dm_sender_engine
import supabase_client
from dm_template_renderer import (
    find_template_tokens,
    has_unresolved_template_tokens,
    render_dm_template,
)


class DmTemplateRendererTest(unittest.TestCase):
    def test_single_brace_username_is_replaced(self) -> None:
        result = render_dm_template(
            "Salut {username}, je voulais te présenter notre page.",
            {
                "recipient_username": "justperfect.eu",
                "account_username": "j_automatise_pour_toi",
            },
        )

        self.assertTrue(result.ok)
        self.assertEqual(
            result.rendered_body,
            "Salut justperfect.eu, je voulais te présenter notre page.",
        )
        self.assertEqual(result.used_variables, ["username"])

    def test_double_brace_username_is_replaced(self) -> None:
        result = render_dm_template(
            "Salut {{username}}.",
            {
                "recipient_username": "justperfect.eu",
                "account_username": "j_automatise_pour_toi",
            },
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.rendered_body, "Salut justperfect.eu.")

    def test_name_is_replaced_when_available(self) -> None:
        result = render_dm_template(
            "Salut {name}, ravi de te connecter ici.",
            {
                "recipient_username": "justperfect.eu",
                "recipient_name": "Marie",
                "account_username": "j_automatise_pour_toi",
            },
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.rendered_body, "Salut Marie, ravi de te connecter ici.")
        self.assertEqual(result.fallbacks_used, [])

    def test_name_falls_back_to_username(self) -> None:
        result = render_dm_template(
            "Salut {name}, ravi de te connecter ici.",
            {
                "recipient_username": "justperfect.eu",
                "recipient_name": None,
                "account_username": "j_automatise_pour_toi",
            },
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.rendered_body, "Salut justperfect.eu, ravi de te connecter ici.")
        self.assertEqual(result.fallbacks_used, ["name:recipient_username"])

    def test_account_username_is_replaced(self) -> None:
        result = render_dm_template(
            "C'est {account_username}.",
            {
                "recipient_username": "justperfect.eu",
                "account_username": "j_automatise_pour_toi",
            },
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.rendered_body, "C'est j_automatise_pour_toi.")

    def test_unknown_variable_is_rejected(self) -> None:
        result = render_dm_template(
            "Salut {company}.",
            {
                "recipient_username": "justperfect.eu",
                "account_username": "j_automatise_pour_toi",
            },
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "unsupported_template_variable")
        self.assertEqual(result.unresolved_tokens, ["company"])

    def test_missing_account_username_is_rejected(self) -> None:
        result = render_dm_template(
            "Salut {username}, c'est {account_username}.",
            {"recipient_username": "justperfect.eu"},
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "missing_template_variable")
        self.assertEqual(result.unresolved_tokens, ["account_username"])

    def test_plain_text_is_unchanged(self) -> None:
        result = render_dm_template(
            "Salut, je voulais te présenter notre page.",
            {},
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.rendered_body, "Salut, je voulais te présenter notre page.")
        self.assertEqual(result.used_variables, [])

    def test_accents_and_emojis_are_preserved(self) -> None:
        result = render_dm_template(
            "Salut {name}, ravi d'échanger ici 😊",
            {
                "recipient_username": "marie.eu",
                "recipient_name": "Élodie",
                "account_username": "j_automatise_pour_toi",
            },
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.rendered_body, "Salut Élodie, ravi d'échanger ici 😊")

    def test_variation_selector_hearts_are_preserved(self) -> None:
        template = "Salut {{username}} et merci du follow ❤️❤️!"
        result = render_dm_template(
            template,
            {
                "recipient_username": "justperfect.eu",
                "account_username": "j_automatise_pour_toi",
            },
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.rendered_body, "Salut justperfect.eu et merci du follow ❤️❤️!")
        self.assertEqual([hex(ord(char)) for char in result.rendered_body[-3:]], ["0x2764", "0xfe0f", "0x21"])

    def test_full_emoji_matrix_is_preserved(self) -> None:
        matrix = "Emoji matrix: ✅ ❤️ 🔄 🔥 🚀 🙏 😄 ✨ 👍 🥰 👨‍💻 👩‍💻 ❤️‍🔥 👍🏽 🙏🏾 🇫🇷 🇺🇸 👋 🎀"
        result = render_dm_template(
            f"Salut {{username}}. {matrix}",
            {
                "recipient_username": "justperfect.eu",
                "account_username": "j_automatise_pour_toi",
            },
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.rendered_body, f"Salut justperfect.eu. {matrix}")

    def test_unresolved_token_detector(self) -> None:
        self.assertTrue(has_unresolved_template_tokens("Salut {username}"))
        self.assertEqual(find_template_tokens("Salut {{ username }} et {name}"), ["username", "name"])
        self.assertFalse(has_unresolved_template_tokens("Salut justperfect.eu"))


class DmTemplateEnqueueIntegrationTest(unittest.TestCase):
    def test_welcome_enqueue_freezes_rendered_message_body(self) -> None:
        with (
            patch.object(
                supabase_client,
                "_resolve_dm_template_for_enqueue",
                return_value={
                    "body": "Salut {username}, c'est {account_username}.",
                    "active": True,
                    "template_type": "welcome",
                },
            ),
            patch.object(supabase_client, "get_account_username", return_value="j_automatise_pour_toi"),
            patch.object(supabase_client, "call_rpc", return_value={"id": "job-1", "status": "pending"}) as rpc,
        ):
            row = supabase_client.enqueue_welcome_dm_job_if_eligible(
                "acct-1",
                "justperfect.eu",
                template_id="tpl-1",
                account_username="j_automatise_pour_toi",
            )

        self.assertEqual(row["id"], "job-1")
        payload = rpc.call_args.args[1]
        self.assertEqual(payload["p_message_body"], "Salut justperfect.eu, c'est j_automatise_pour_toi.")
        self.assertEqual(payload["p_template_id"], "tpl-1")

    def test_welcome_enqueue_preserves_emoji_template_body(self) -> None:
        with (
            patch.object(
                supabase_client,
                "_resolve_dm_template_for_enqueue",
                return_value={
                    "body": "Salut {{username}} et merci du follow ❤️❤️!",
                    "active": True,
                    "template_type": "welcome",
                },
            ),
            patch.object(supabase_client, "get_account_username", return_value="j_automatise_pour_toi"),
            patch.object(supabase_client, "call_rpc", return_value={"id": "job-emoji", "status": "pending"}) as rpc,
        ):
            row = supabase_client.enqueue_welcome_dm_job_if_eligible(
                "acct-1",
                "justperfect.eu",
                template_id="tpl-emoji",
                account_username="j_automatise_pour_toi",
            )

        self.assertEqual(row["id"], "job-emoji")
        payload = rpc.call_args.args[1]
        self.assertEqual(payload["p_message_body"], "Salut justperfect.eu et merci du follow ❤️❤️!")
        self.assertEqual(payload["p_template_id"], "tpl-emoji")

    def test_outreach_enqueue_freezes_rendered_message_body_with_name_fallback(self) -> None:
        with (
            patch.object(
                supabase_client,
                "_resolve_dm_template_for_enqueue",
                return_value={
                    "body": "Salut {name}, je voulais te présenter notre page.",
                    "active": True,
                    "template_type": "outreach",
                },
            ),
            patch.object(supabase_client, "get_account_username", return_value="j_automatise_pour_toi"),
            patch.object(supabase_client, "call_rpc", return_value={"id": "job-1", "status": "pending"}) as rpc,
        ):
            row = supabase_client.enqueue_outreach_dm_job(
                "acct-1",
                "justperfect.eu",
                template_id="tpl-1",
                account_username="j_automatise_pour_toi",
            )

        self.assertEqual(row["id"], "job-1")
        payload = rpc.call_args.args[1]
        self.assertEqual(payload["p_message_body"], "Salut justperfect.eu, je voulais te présenter notre page.")

    def test_enqueue_rejects_unknown_template_variable(self) -> None:
        with (
            patch.object(
                supabase_client,
                "_resolve_dm_template_for_enqueue",
                return_value={"body": "Salut {company}.", "active": True, "template_type": "outreach"},
            ),
            patch.object(supabase_client, "get_account_username", return_value="j_automatise_pour_toi"),
            patch.object(supabase_client, "call_rpc") as rpc,
        ):
            with self.assertRaisesRegex(RuntimeError, "dm_template_render_failed"):
                supabase_client.enqueue_outreach_dm_job(
                    "acct-1",
                    "justperfect.eu",
                    template_id="tpl-1",
                    account_username="j_automatise_pour_toi",
                )

        rpc.assert_not_called()

    def test_enqueue_rejects_inactive_template_before_rpc(self) -> None:
        with (
            patch.object(
                supabase_client,
                "_resolve_dm_template_for_enqueue",
                return_value={"body": "Salut {username}.", "active": False, "template_type": "outreach"},
            ),
            patch.object(supabase_client, "call_rpc") as rpc,
        ):
            with self.assertRaisesRegex(RuntimeError, "template_inactive"):
                supabase_client.enqueue_outreach_dm_job(
                    "acct-1",
                    "justperfect.eu",
                    template_id="tpl-1",
                    account_username="j_automatise_pour_toi",
                )

        rpc.assert_not_called()


class DmTemplateSenderGuardTest(unittest.TestCase):
    def test_sender_selects_set_text_for_emoji_message(self) -> None:
        flags = dm_sender_engine._dm_message_typing_flags(
            "Emoji matrix: ✅ ❤️ 🔄 🔥 🚀 🙏 😄 ✨ 👍 🥰 👨‍💻 👩‍💻 ❤️‍🔥 👍🏽 🙏🏾 🇫🇷 🇺🇸 👋 🎀"
        )

        self.assertTrue(flags["contains_non_ascii"])
        self.assertEqual(dm_sender_engine._select_dm_typing_strategy(flags), "set_text")

    def test_sender_refuses_unresolved_template_token_before_ui_probe(self) -> None:
        with patch.object(dm_sender_engine, "dm_thread_shows_outgoing_message") as probe:
            sent_ok, send_out, failure_reason = dm_sender_engine._perform_real_welcome_dm_send(
                object(),
                username="justperfect.eu",
                message_body="Salut {username}.",
                thread_state="empty_new_thread",
                pkg="com.instagram.androif",
            )

        self.assertFalse(sent_ok)
        self.assertEqual(send_out, {})
        self.assertEqual(failure_reason, "unresolved_template_token")
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
