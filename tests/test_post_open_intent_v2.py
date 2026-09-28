from __future__ import annotations

from dataclasses import replace
import time
import unittest
from unittest import mock

import instagram_navigation as nav
from tests.test_certification_admission import restore_exact_like_budget_delta, restore_exact_self_xml_delta
import post_open_intent_v2 as intent_v2
from tests.test_follow60_ordering_v2_behavioral_canary_v1 import _binding, _stable


class PostOpenIntentV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        intent_v2._reset_consumed_nonces_for_tests()
        self.binding = {
            "account_id": "account", "run_id": "run", "request_id": "request",
            "action_id": "action", "attempt_id": 1,
            "business_session_id": "session", "control_id": "control",
            "worker_sha": "a" * 40,
        }

    def _intent(self, *, branch: str = "SAFE", row: int = 0, column: int = 0):
        return intent_v2.create_post_open_intent_v2(
            binding=self.binding, candidate_username="candidate", target_id="target",
            target_username="ct", package="com.instagram.android",
            activity="com.instagram.mainactivity.InstagramMainActivity",
            source_branch=branch, absolute_row=row, absolute_column=column,
            bounds={"left": 0, "top": 900, "right": 360, "bottom": 1260},
            coordinate_frame={"transform_version": "coordinate_frame_v1"},
            viewport={"width": 1080, "height": 2340}, insets={"bottom": 126},
            navigation_generation=4, scroll_generation=2, ui_generation=6,
            xml_hash="xml-hash", fingerprint="viewport-fingerprint",
            post_grid_classification="POST_ROW_POSITIVE_SAFE",
            candidate_bound_provenance="final_mute_close_xml", ttl_ms=1250,
            created_at_monotonic=100.0,
        )

    def _consume(self, intent, **overrides):
        values = {
            "binding": self.binding, "candidate_username": "candidate",
            "package": "com.instagram.android",
            "activity": "com.instagram.mainactivity.InstagramMainActivity",
            "viewport": {"width": 1080, "height": 2340},
            "navigation_generation": 4, "scroll_generation": 2,
            "ui_generation": 6, "now_monotonic": 100.4,
        }
        values.update(overrides)
        return intent_v2.consume_post_open_intent_v2(intent, **values)

    def test_safe_and_golden_share_the_same_contract(self):
        for branch in ("SAFE", "GOLDEN"):
            with self.subTest(branch=branch):
                item = self._intent(branch=branch)
                self.assertIsNotNone(item)
                self.assertEqual(item.version, "PostOpenIntentV2")

    def test_intent_is_consumed_once(self):
        item = self._intent()
        self.assertIsNotNone(self._consume(item)[0])
        self.assertEqual(self._consume(item)[2], "post_open_intent_already_consumed")

    def test_generation_candidate_and_binding_mismatch_fail_closed(self):
        cases = (
            ({"navigation_generation": 5}, "post_open_intent_navigation_generation_mismatch"),
            ({"candidate_username": "other"}, "post_open_intent_candidate_mismatch"),
            ({"binding": {**self.binding, "run_id": "other"}}, "post_open_intent_run_mismatch"),
        )
        for values, reason in cases:
            with self.subTest(reason=reason):
                item = self._intent()
                self.assertEqual(self._consume(item, **values)[2], reason)

    def test_stale_bounds_and_ttl_fail_closed(self):
        item = self._intent()
        self.assertEqual(
            self._consume(item, viewport={"width": 720, "height": 1600})[2],
            "post_open_intent_viewport_or_bounds_stale",
        )
        item = self._intent()
        self.assertEqual(
            self._consume(item, now_monotonic=101.3)[2],
            "post_open_intent_expired",
        )

    def test_row_two_never_becomes_row_one(self):
        item = self._intent(row=1, column=0)
        accepted, _, reason = self._consume(item)
        self.assertEqual(reason, "")
        self.assertEqual(accepted.absolute_row, 1)
        self.assertEqual(accepted.absolute_column, 0)

    def test_invalidated_intent_is_rejected(self):
        item = replace(
            self._intent(), invalidated=True,
            invalidation_reason="planned_scroll_after_intent",
        )
        self.assertEqual(self._consume(item)[2], "planned_scroll_after_intent")

    def test_safe_intent_reuses_terminal_surface_without_device_reacquisition(self):
        package, activity = nav._post_open_intent_surface_from_final_proof(
            {
                "final_proof_package": "com.instagram.android",
                "final_proof_activity": (
                    "com.instagram.mainactivity.InstagramMainActivity"
                ),
            },
            expected_package="com.instagram.android",
        )
        self.assertEqual(package, "com.instagram.android")
        self.assertEqual(
            activity,
            "com.instagram.mainactivity.InstagramMainActivity",
        )

    def test_post_reveal_surface_supersedes_mute_close_surface(self):
        package, activity = nav._post_open_intent_surface_from_final_proof(
            {
                "final_proof_package": "com.instagram.android",
                "final_proof_activity": "stale.ProfileActivity",
                "post_reveal_package": "com.instagram.android",
                "post_reveal_activity": (
                    "com.instagram.mainactivity.InstagramMainActivity"
                ),
            },
            expected_package="com.instagram.android",
        )
        self.assertEqual(package, "com.instagram.android")
        self.assertIn("InstagramMainActivity", activity)

    def test_terminal_surface_mismatch_fails_closed(self):
        self.assertEqual(
            nav._post_open_intent_surface_from_final_proof(
                {
                    "final_proof_package": "other.package",
                    "final_proof_activity": "other.MainActivity",
                },
                expected_package="com.instagram.android",
            ),
            ("", ""),
        )

    def _ordering_v2_stage_binding(self, binding, stable):
        return {
            "account_id": binding.account_id,
            "run_id": binding.run_id,
            "request_id": binding.request_id,
            "action_id": stable.action_id,
            "attempt_id": binding.attempt_id,
            "business_session_id": binding.business_session_id,
            "control_id": binding.control_id,
            "worker_sha": binding.actual_worker_sha,
            "source_target_id": stable.target_id,
        }

    def _ordering_v2_intent(self, runtime):
        binding, _ = _binding()
        stable = _stable(binding)
        evidence = dict(stable.post_grid_evidence)
        package, activity = nav._post_open_intent_surface_from_final_proof(
            evidence, expected_package="com.instagram.android"
        )
        with mock.patch(
            "follow_60s_canary.runtime_context", return_value=runtime
        ):
            intent = nav._create_post_open_intent_from_final_proof(
                binding=self._ordering_v2_stage_binding(binding, stable),
                candidate_username=stable.candidate_username,
                target_username="ct",
                source_branch="SAFE",
                bounds=dict(evidence["post_bounds"]),
                package=package,
                activity=activity,
                evidence=evidence,
                viewport_width=int(evidence["screen_width"]),
                viewport_height=int(evidence["screen_height"]),
                xml_hash=str(evidence["source_xml_fingerprint"]),
                fingerprint=str(evidence["source_xml_fingerprint"]),
                absolute_row=1,
                absolute_column=1,
                candidate_bound_provenance="ordering_v2_initial_mono_xml",
            )
        return intent

    def test_ordering_v2_initial_proof_creates_intent_without_reacquisition(self):
        intent = self._ordering_v2_intent({
            "navigation_counter": 4,
            "scroll_counter": 2,
            "ui_generation": 6,
        })
        self.assertIsNotNone(intent)
        self.assertEqual(intent.source_branch, "SAFE")

    def test_ordering_v2_initial_proof_generation_mismatch_fails_closed(self):
        intent = self._ordering_v2_intent({
            "navigation_counter": 5,
            "scroll_counter": 2,
            "ui_generation": 6,
        })
        self.assertIsNone(intent)

    def test_dispatch_uses_prevalidated_surface_without_slow_device_rpc(self):
        device = mock.Mock()
        now = time.monotonic()
        item = replace(
            self._intent(),
            created_at_monotonic=now,
            expires_at_monotonic=now + 1.25,
        )
        surface = {
            "current_package": "com.instagram.android",
            "current_activity": (
                "com.instagram.mainactivity.InstagramMainActivity"
            ),
            "viewport_width": 1080,
            "viewport_height": 2340,
        }
        with mock.patch(
            "follow_60s_canary.runtime_context",
            return_value={
                "navigation_counter": 4,
                "scroll_counter": 2,
                "ui_generation": 6,
            },
        ), mock.patch.object(
            nav, "_followers_current_pkg_activity"
        ) as live_surface:
            result = nav._dispatch_post_open_intent_v2_tap(
                device,
                intent=item,
                binding=self.binding,
                candidate_username="candidate",
                prevalidated_surface=surface,
            )

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["prevalidated_surface_used"])
        live_surface.assert_not_called()
        device.window_size.assert_not_called()
        device.click.assert_called_once_with(180, 1080)

    def test_lightweight_reissue_is_one_xml_dump_and_same_bounds_only(self):
        device = mock.Mock()
        device.dump_hierarchy.return_value = (
            '<hierarchy rotation="0"><node text="candidate" /></hierarchy>'
        )
        bounds = {"left": 0, "top": 900, "right": 360, "bottom": 1260}
        classified = {
            "outcome": "POST_ROW_POSITIVE_SAFE",
            "identity_exact": True,
            "grid_selected": True,
            "loading_visible": False,
            "private_profile_visible": False,
            "reels_or_tagged_selected": False,
            "suggested_overlay_visible": False,
            "post_bounds": dict(bounds),
        }
        with mock.patch.object(
            nav,
            "_post_follow_post_grid_evidence_from_xml",
            return_value=classified,
        ), mock.patch.object(
            nav,
            "_post_follow_screen_dimensions_from_hierarchy",
            return_value={
                "coordinate_frame": {"transform_version": "coordinate_frame_v1"}
            },
        ):
            result = nav._post_open_intent_lightweight_reissue_evidence(
                device,
                original_evidence={},
                original_bounds=bounds,
                candidate_username="candidate",
                viewport_width=1080,
                viewport_height=2340,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["dump_count"], 1)
        self.assertEqual(result["bounds"], bounds)
        device.dump_hierarchy.assert_called_once_with(compressed=False)
        device.click.assert_not_called()
        device.screenshot.assert_not_called()

    def test_lightweight_reissue_rejects_changed_bounds(self):
        device = mock.Mock()
        device.dump_hierarchy.return_value = (
            '<hierarchy rotation="0"><node text="candidate" /></hierarchy>'
        )
        original = {"left": 0, "top": 900, "right": 360, "bottom": 1260}
        changed = {"left": 360, "top": 900, "right": 720, "bottom": 1260}
        with mock.patch.object(
            nav,
            "_post_follow_post_grid_evidence_from_xml",
            return_value={
                "outcome": "POST_ROW_POSITIVE_SAFE",
                "identity_exact": True,
                "grid_selected": True,
                "loading_visible": False,
                "private_profile_visible": False,
                "reels_or_tagged_selected": False,
                "suggested_overlay_visible": False,
                "post_bounds": changed,
            },
        ):
            result = nav._post_open_intent_lightweight_reissue_evidence(
                device,
                original_evidence={},
                original_bounds=original,
                candidate_username="candidate",
                viewport_width=1080,
                viewport_height=2340,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["dump_count"], 1)
        device.click.assert_not_called()



class Exact446PostOpenAuthorityTests(unittest.TestCase):
    BASE = "3bbd471bf7919926817ccfe300cb2ae4295fcd1e"
    MAIN = "com.instagram.mainactivity.InstagramMainActivity"
    FULL = "com.instagram.android.activity.MainTabActivity"
    REL = ".activity.MainTabActivity"

    @classmethod
    def setUpClass(cls):
        import ast, subprocess
        from pathlib import Path
        cls.root = Path(nav.__file__).parent
        cls.old = subprocess.check_output(["git", "show", cls.BASE + ":instagram_navigation.py"], cwd=cls.root, text=True)
        node = next(n for n in ast.parse(cls.old).body if isinstance(n, ast.FunctionDef)
                    and n.name == "_post_open_intent_surface_from_final_proof")
        ns = dict(vars(nav))
        exec(compile(ast.Module(body=[node], type_ignores=[]), "signed-base", "exec"), ns)
        cls.original = staticmethod(ns[node.name])

    def device(self, version=("446.0.0.49.77", 385211304), package="com.instagram.android"):
        from instagram_ui_446_compat import retain_context
        class NoIO:
            serial = "RFGL62N646V"
            def __getattr__(self, name):
                raise AssertionError("extra device I/O: " + name)
        d = NoIO()
        d._instagram_search_observed_version = (id(d), d.serial, package, *version)
        retain_context(d, serial=d.serial, package=package, version_name=version[0],
                       version_code=version[1], unsafe=False)
        return d

    def convert(self, d, activity=None, package="com.instagram.android", expected=None):
        return nav._post_open_intent_surface_from_final_proof(
            {"final_proof_package": package, "final_proof_activity": activity},
            expected_package=package if expected is None else expected, device_context=d)

    def test_required_positive_and_negative_matrix(self):
        for version, package, activity, accepted in (
            (("372.0.0.48.60",377611059),"com.instagram.androif",self.MAIN,True),
            (("446.0.0.49.77",385211304),"com.instagram.android",self.MAIN,True),
            (("446.0.0.49.77",385211304),"com.instagram.android",self.FULL,True),
            (("446.0.0.49.77",385211304),"com.instagram.android",self.REL,True),
            (("372.0.0.48.60",377611059),"com.instagram.android",self.REL,False),
            (("446.0.0.49.77",385211304),"com.instagram.androif",self.FULL,False),
            (("446.0.0.49.77",385211304),"foreign",self.REL,False),
            (("445",385211304),"com.instagram.android",self.REL,False),
            (("446.0.0.49.77",385211305),"com.instagram.android",self.REL,False),
        ):
            with self.subTest(version=version, package=package, activity=activity):
                self.assertEqual(self.convert(self.device(version,package),activity,package),
                                 (package,activity) if accepted else ("",""))
        for activity in ["MainActivity", "foreign.Activity",self.FULL+"Overlay", "",None]:
            self.assertEqual(self.convert(self.device(),activity),("",""))
        self.assertEqual(self.convert(self.device(),self.REL,expected="foreign"),("",""))

    def test_absent_forged_stale_context_and_proxy_rejected(self):
        from run_latency_observability_v1 import _TimedProxy
        mutations=[lambda d:None,lambda d:object(),
            lambda d:(vars(d).pop("_instagram_search_observed_version"),d)[1],
            lambda d:(vars(d).__setitem__("_instagram_446_observed_context",None),d)[1],
            lambda d:(vars(d).__setitem__("serial","wrong"),d)[1],
            lambda d:(vars(d).__setitem__("_instagram_search_observed_version",(id(d),d.serial,"com.instagram.android","446.0.0.49.77",True)),d)[1]]
        for change in mutations:self.assertEqual(self.convert(change(self.device()),self.REL),("",""))
        d=self.device();other=type(d)();vars(other).update(vars(d))
        self.assertEqual(self.convert(other,self.REL),("",""))
        proxy=_TimedProxy(d,kind="device",emit=None,methods=frozenset())
        self.assertEqual(self.convert(proxy,self.REL),("com.instagram.android",self.REL))
        for bad in [_TimedProxy(d,kind="uiautomator",emit=None,methods=frozenset()),
                    _TimedProxy(proxy,kind="device",emit=None,methods=frozenset())]:
            self.assertEqual(self.convert(bad,self.REL),("",""))

    def test_372_differential_and_no_context_compatibility(self):
        for activity in [self.MAIN,"instagram.MainActivity",self.FULL,self.REL,"",None,"foreign.Activity"]:
            for package in ["com.instagram.android","com.instagram.androif",""]:
                proof={"final_proof_package":package,"final_proof_activity":activity}
                before=self.original(proof,expected_package=package)
                self.assertEqual(nav._post_open_intent_surface_from_final_proof(proof,expected_package=package),before)
                self.assertEqual(self.convert(self.device(("372.0.0.48.60",377611059),package),activity,package),before)

    def test_precedence_and_exact_activity_spelling_preserved(self):
        d=self.device()
        for activity in [self.FULL,self.REL]:
            proof={"package":"foreign","activity":"foreign","final_proof_package":"com.instagram.android",
                   "final_proof_activity":"stale","post_reveal_package":"com.instagram.android","post_reveal_activity":activity}
            self.assertEqual(nav._post_open_intent_surface_from_final_proof(proof,expected_package="com.instagram.android",device_context=d),
                             ("com.instagram.android",activity))
        proof["post_reveal_activity"]="foreign.overlay"
        self.assertEqual(nav._post_open_intent_surface_from_final_proof(proof,expected_package="com.instagram.android",device_context=d),("",""))

    def test_real_unmodified_jit_rejects_live_mismatches(self):
        class JitDevice:
            def __init__(self,p,a,w,h):self.values=(p,a,w,h);self.calls=[]
            def app_current(self):self.calls.append("app_current");return dict(zip(("package","activity"),self.values[:2]))
            def window_size(self):self.calls.append("window_size");return self.values[2:]
        p,a=self.convert(self.device(),self.REL)
        for lp,la,w,h,ok in [(p,a,1080,2340,True),("foreign",a,1080,2340,False),
                            (p,self.FULL,1080,2340,False),(p,"foreign.overlay",1080,2340,False),
                            (p,a,0,2340,False),(p,a,1080,0,False)]:
            d=JitDevice(lp,la,w,h)
            result=nav._post_open_intent_jit_surface(d,expected_package=p,expected_activity=a)
            self.assertEqual(result["ok"],ok)
            if not ok:self.assertEqual(result["reason"],"post_open_jit_surface_mismatch")
            self.assertEqual(d.calls,["app_current","window_size"])

    def test_conversion_has_zero_extra_io(self):
        import socket, subprocess
        d=self.device()
        with mock.patch.object(time,"sleep",side_effect=AssertionError("sleep")), \
             mock.patch.object(socket,"create_connection",side_effect=AssertionError("network")), \
             mock.patch.object(subprocess,"run",side_effect=AssertionError("subprocess")):
            self.assertEqual(self.convert(d,self.REL),("com.instagram.android",self.REL))

    def test_real_callsite_transports_existing_context(self):
        import ast, inspect
        tree=ast.parse(inspect.getsource(nav.run_post_follow_post_likes_phase))
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)
               and n.func.id=="_post_open_intent_surface_from_final_proof"]
        self.assertEqual(len(calls),1)
        self.assertEqual([(k.arg,ast.unparse(k.value)) for k in calls[0].keywords],
                         [("expected_package","pkg"),("device_context","d")])
        ns={"_post_open_intent_surface_from_final_proof":nav._post_open_intent_surface_from_final_proof,
            "_intent_evidence":{"final_proof_package":"com.instagram.android","final_proof_activity":self.REL},
            "pkg":"com.instagram.android","d":self.device()}
        self.assertEqual(eval(compile(ast.Expression(calls[0]),"real-callsite","eval"),ns),("com.instagram.android",self.REL))

    def test_all_other_navigation_bytes_and_routing_unchanged(self):
        import ast, subprocess
        current=(self.root/"instagram_navigation.py").read_text()
        def omit_helper(src):
            node=next(n for n in ast.parse(src).body if isinstance(n,ast.FunctionDef)
                      and n.name=="_post_open_intent_surface_from_final_proof")
            lines=src.splitlines(True);return "".join(lines[:node.lineno-1]+lines[node.end_lineno:])
        call='''                _post_open_intent_surface_from_final_proof(
                    _intent_evidence,
                    expected_package=pkg,
                    device_context=d,
                )'''
        self.assertEqual(current.count(call),1)
        viewer_activity_hunks = [['    activity_lower = current_activity.lower()\n    instagram_main_activity = bool(\n        "instagram" in activity_lower and "mainactivity" in activity_lower\n    )\n    if (not instagram_main_activity and package_exact\n            and current_package == "com.instagram.android"\n            and current_activity in ("com.instagram.android.activity.MainTabActivity",\n                                     ".activity.MainTabActivity")):\n        try:\n            instagram_main_activity = _followers_row_activity_allowed(\n                d, current_package, current_activity)\n        except (AttributeError, TypeError):\n            instagram_main_activity = False\n', '    activity_lower = current_activity.lower()\n    instagram_main_activity = bool(\n        "instagram" in activity_lower and "mainactivity" in activity_lower\n    )\n'], ['    activity_lower = str(current_activity or "").lower()\n    instagram_main_activity = bool(\n        "instagram" in activity_lower and "mainactivity" in activity_lower\n    )\n    if (not instagram_main_activity and package_exact\n            and current_package == "com.instagram.android"\n            and current_activity in ("com.instagram.android.activity.MainTabActivity",\n                                     ".activity.MainTabActivity")):\n        try:\n            instagram_main_activity = _followers_row_activity_allowed(\n                d, current_package, current_activity)\n        except (AttributeError, TypeError):\n            instagram_main_activity = False\n', '    activity_lower = str(current_activity or "").lower()\n    instagram_main_activity = bool(\n        "instagram" in activity_lower and "mainactivity" in activity_lower\n    )\n']]
        viewer_activity_hunks.extend([('        stage_activity_allowed = bool(\n            "InstagramMainActivity" in str(stage_ctx.get("activity") or "")\n        )\n        if (not stage_activity_allowed\n                and str(stage_ctx.get("package") or "") == "com.instagram.android"\n                and str(stage_ctx.get("activity") or "") in (\n                    "com.instagram.android.activity.MainTabActivity",\n                    ".activity.MainTabActivity")\n                and str(stage_ctx.get("activity") or "")\n                == str(meta0.get("current_activity") or "")):\n            try:\n                stage_activity_allowed = _followers_row_activity_allowed(\n                    d, str(stage_ctx.get("package") or ""),\n                    str(stage_ctx.get("activity") or ""))\n            except (AttributeError, TypeError):\n                stage_activity_allowed = False\n        stage_scoped_open_context_ok = bool(\n', '        stage_scoped_open_context_ok = bool(\n'), ('            and stage_activity_allowed\n', '            and "InstagramMainActivity"\n            in str(stage_ctx.get("activity") or "")\n')])
        viewer_activity_hunks.extend([('        activity_allowed = bool(\n            "instagram" in live_activity.lower() and "mainactivity" in live_activity.lower()\n        )\n        if (not activity_allowed and live_package == "com.instagram.android"\n                and live_activity in (\n                    "com.instagram.android.activity.MainTabActivity", ".activity.MainTabActivity")):\n            try:\n                activity_allowed = _followers_row_activity_allowed(d, live_package, live_activity)\n            except (AttributeError, TypeError):\n                activity_allowed = False\n        if not activity_allowed:\n            return False, "liketapcontext_viewer_mismatch", age_ms\n', '        if "instagram" not in live_activity.lower() or "mainactivity" not in live_activity.lower():\n            return False, "liketapcontext_viewer_mismatch", age_ms\n')])
        def restore_viewer_activity(src):
            for new, old in viewer_activity_hunks:
                self.assertEqual(src.count(new), 1, "viewer hunk absent/modified/duplicated")
                src = src.replace(new, old, 1)
            return src
        def assert_exact_baseline(src):
            restored = restore_viewer_activity(restore_exact_like_budget_delta(src))
            self.assertEqual(omit_helper(restored.replace(call,call.replace("                    device_context=d,\n",""),1)),omit_helper(self.old))
        assert_exact_baseline(current)
        for new, old in viewer_activity_hunks:
            for corrupted in (current.replace(new,old,1), current.replace(new,(new.replace("except (AttributeError, TypeError)","except Exception") if "except (AttributeError, TypeError)" in new else new + "# altered approved hunk\n"),1), current + new):
                with self.assertRaises(AssertionError):
                    assert_exact_baseline(corrupted)
        with self.assertRaises(AssertionError):
            assert_exact_baseline(current + "\n# unrelated navigation delta\n")
        for name in ["runner.py","follow60_mainline_v2.py","follow60_ordering_v2_behavioral_canary_v1.py","post_open_intent_v2.py"]:
            current_bytes = (self.root/name).read_bytes()
            if name == "runner.py":
                current_bytes = restore_exact_self_xml_delta(name, current_bytes.decode()).encode()
            self.assertEqual(current_bytes,subprocess.check_output(["git","show",self.BASE+":"+name],cwd=self.root))

if __name__ == "__main__":
    unittest.main()
