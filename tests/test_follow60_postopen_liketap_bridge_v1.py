from __future__ import annotations

import hashlib
import time
import unittest
from unittest import mock

import follow_60s_canary as canary
import instagram_navigation as nav
from tests.test_certification_admission import restore_exact_like_budget_delta
from tests.follow60_generic_fixtures import configure_canary


PKG = "com.instagram.android"
ACT = "com.instagram.mainactivity.InstagramMainActivity"
ACCOUNT_ID = "11111111-1111-4111-8111-111111111111"


class PostOpenLikeTapBridgeV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        configure_canary(
            canary,
            account_id=ACCOUNT_ID,
            account_username="bridge_account",
            run_id="run-bridge",
            package=PKG,
            resume_policy=None,
        )
        self.device = mock.MagicMock()
        self.device.window_size.return_value = (1080, 2340)

    def tearDown(self) -> None:
        configure_canary(
            canary,
            account_id="other",
            account_username="other",
            run_id="reset",
            package=PKG,
            resume_policy=None,
        )

    def _binding(self) -> dict[str, object]:
        return {
            "account_id": ACCOUNT_ID,
            "run_id": "run-bridge",
            "request_id": "request-bridge",
            "action_id": "action-bridge",
            "attempt_id": 1,
            "business_session_id": "business-bridge",
            "control_id": "control-bridge",
            "worker_sha": "worker-bridge",
        }

    def _xml(self, *, candidate: str = "candidate", state: str = "Like", story: bool = False) -> str:
        story_node = '<node resource-id="story_viewer_root"/>' if story else ""
        return (
            f'<hierarchy>{story_node}<node text="Posts"/><node text="{candidate}"/>'
            f'<node resource-id="com.instagram.android:id/row_feed_button_like" '
            f'content-desc="{state}" clickable="true" bounds="[40,1700][120,1800]"/>'
            '</hierarchy>'
        )

    def _stage(self, **overrides: object) -> dict[str, object]:
        stage = {
            "version": "PostOpenContextV1",
            **self._binding(),
            "candidate_username": "candidate",
            "candidate_profile_id": "profile-bridge",
            "source_target_id": "target-bridge",
            "package": PKG,
            "activity": ACT,
            "viewer_type": "Posts",
            "open_method": "direct_cell_under_suggested",
            "source_cell_fingerprint": "cell-fingerprint",
            "v5_positive": True,
            "post_identity_confirmed": True,
            "story_or_highlight_detected": False,
            "exact_like_proof": None,
            "exact_like_source": "",
            "exact_like_bounds": None,
            "exact_like_bounds_hash": "",
            "xml_hash": "",
            "exact_like_xml_fingerprint": "",
            "exact_like_ui_generation": 0,
            "post_open_ui_generation": canary.runtime_context()["ui_generation"],
            "created_at_monotonic": time.monotonic(),
            "navigation_generation": "nav-bridge",
            "stage_nonce": "nonce-bridge",
        }
        stage.update(overrides)
        stage["proof_hash"] = nav._post_open_context_v1_proof_hash(stage)
        return stage

    def _create(self, *, stage: dict[str, object] | None = None, xml: str | None = None, allow_dump: bool = False):
        post_open: dict[str, object] = {"post_open_context_v1": stage or self._stage()}
        if xml is not None:
            post_open["post_open_surface_audit"] = {
                "snapshot_xml": xml,
                "snapshot_captured_at_monotonic": time.perf_counter(),
            }
        with mock.patch.object(
            nav,
            "_followers_current_pkg_activity",
            return_value={"current_package": PKG, "current_activity": ACT},
        ):
            return nav._create_like_tap_context_v2(
                self.device,
                expected_package=PKG,
                expected_follower_username="candidate",
                expected_stage_binding=self._binding(),
                post_open_context=post_open,
                allow_fresh_dump=allow_dump,
                force_fresh_dump=allow_dump and xml is None,
            )

    def test_v5_bridge_uses_existing_single_xml_without_dump(self) -> None:
        ctx, reason = self._create(xml=self._xml())
        self.assertEqual(reason, "")
        self.assertEqual(ctx["like_context_transport"], "PostOpenContextV1_single_xml_bridge")
        self.device.dump_hierarchy.assert_not_called()

    def test_v5_bridge_reacquires_exactly_one_xml_when_snapshot_missing(self) -> None:
        self.device.dump_hierarchy.return_value = self._xml()
        ctx, reason = self._create(allow_dump=True)
        self.assertEqual(reason, "")
        self.assertEqual(ctx["snapshot_source"], "single_reacquisition")
        self.device.dump_hierarchy.assert_called_once()

    def test_bridge_keeps_exact_candidate_binding(self) -> None:
        ctx, reason = self._create(xml=self._xml())
        self.assertEqual(reason, "")
        self.assertEqual(ctx["candidate_username"], "candidate")
        self.assertEqual(ctx["request_id"], "request-bridge")

    def test_story_wins_over_like_node(self) -> None:
        ctx, reason = self._create(xml=self._xml(story=True))
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_story_or_highlight_detected")

    def test_already_liked_fails_closed(self) -> None:
        ctx, reason = self._create(xml=self._xml(state="Unlike"))
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_already_liked")

    def test_candidate_mismatch_fails_closed(self) -> None:
        ctx, reason = self._create(stage=self._stage(candidate_username="other"), xml=self._xml())
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_post_open_candidate_mismatch")

    def test_binding_mismatch_fails_closed(self) -> None:
        ctx, reason = self._create(stage=self._stage(request_id="wrong"), xml=self._xml())
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_post_open_binding_mismatch")

    def test_v5_false_fails_closed(self) -> None:
        ctx, reason = self._create(stage=self._stage(v5_positive=False), xml=self._xml())
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_post_open_v5_rejected")

    def test_identity_false_fails_closed(self) -> None:
        ctx, reason = self._create(stage=self._stage(post_identity_confirmed=False), xml=self._xml())
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_post_open_v5_rejected")

    def test_stage_nonce_missing_fails_closed(self) -> None:
        ctx, reason = self._create(stage=self._stage(stage_nonce=""), xml=self._xml())
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_post_open_stage_nonce_missing")

    def test_source_fingerprint_missing_fails_closed(self) -> None:
        ctx, reason = self._create(stage=self._stage(source_cell_fingerprint=""), xml=self._xml())
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_post_open_source_fingerprint_missing")

    def test_navigation_generation_missing_fails_closed(self) -> None:
        ctx, reason = self._create(stage=self._stage(navigation_generation=""), xml=self._xml())
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_post_open_navigation_generation_missing")

    def test_generation_change_fails_closed(self) -> None:
        stage = self._stage(
            post_open_ui_generation=int(canary.runtime_context()["ui_generation"]) + 1
        )
        ctx, reason = self._create(stage=stage, xml=self._xml())
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_generation_changed")

    def test_tampered_hash_fails_closed(self) -> None:
        stage = self._stage()
        stage["source_cell_fingerprint"] = "tampered"
        ctx, reason = self._create(stage=stage, xml=self._xml())
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_post_open_hash_mismatch")

    def test_exact_like_missing_preserves_create_reason(self) -> None:
        xml = '<hierarchy><node text="Posts"/><node text="candidate"/></hierarchy>'
        ctx, reason = self._create(xml=xml)
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_exact_like_missing")
        self.assertEqual(
            nav._liketapcontext_rejection_reason(
                ctx,
                create_reason=reason,
                validation_reason="liketapcontext_version_missing",
            ),
            "liketapcontext_exact_like_missing",
        )


if __name__ == "__main__":
    unittest.main()


class LikeContextActivityAuthority446Tests(unittest.TestCase):
    """Execute the actual consumer predicate; never execute any UI transport."""
    @staticmethod
    def device(version=('446.0.0.49.77', 385211304), package=PKG):
        from types import SimpleNamespace
        from instagram_ui_446_compat import retain_context
        d = SimpleNamespace(serial='certification-device')
        d._instagram_search_observed_version = (id(d), d.serial, package, *version)
        retain_context(d, serial=d.serial, package=package,
                       version_name=version[0], version_code=version[1], unsafe=False)
        return d

    def evaluate(self, activity='.activity.MainTabActivity', age=4456.9,
                 v2=True, device=None, stage=None, **changes):
        import ast, inspect, textwrap
        src = textwrap.dedent(inspect.getsource(nav.visual_like_open_post))
        tree = ast.parse(src)
        nodes = list(ast.walk(tree))
        assignment = lambda key: next(n for n in nodes if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == key for t in n.targets))
        initial = assignment('stage_activity_allowed')
        combined = assignment('stage_scoped_open_context_ok')
        extension = next(n for n in nodes if isinstance(n, ast.If)
                         and initial.lineno < n.lineno < combined.lineno)
        reason = next(n for n in nodes if isinstance(n, ast.If)
                      and ast.unparse(n.test) == 'stage_ctx and (not stage_scoped_open_context_ok)')
        ctx = dict(candidate_username='candidate', package=PKG, activity=activity,
                   viewer_type='Posts', v5_positive=True,
                   story_or_highlight_detected=False, post_identity_confirmed=True)
        ctx.update(stage or {})
        ns = dict(d=device if device is not None else self.device(),
                  _followers_row_activity_allowed=nav._followers_row_activity_allowed,
                  _normalize_handle=nav._normalize_handle, stage_ctx=ctx,
                  meta0={'current_package':PKG, 'current_activity':activity},
                  expected_follower_username='candidate', post_opened_via_profile_grid=True,
                  stage_ctx_hash_ok=True, stage_binding_ok=True, stage_ctx_age_ms=age,
                  like_tap_context_v2_required=v2, stage_context_rejection_reason='')
        ns.update(changes)
        code = compile(ast.fix_missing_locations(ast.Module(
            body=[initial,extension,combined,reason],type_ignores=[])), '<real-consumer>', 'exec')
        exec(code,ns)
        return ns['stage_scoped_open_context_ok'], ns['stage_context_rejection_reason']

    def test_qualified_446_full_relative_and_372_unchanged(self):
        for activity in ('.activity.MainTabActivity', 'com.instagram.android.activity.MainTabActivity', ACT):
            with self.subTest(activity=activity):
                self.assertEqual(self.evaluate(activity=activity), (True,''))
        old=self.device(('372.0.0.48.60', 1), 'com.instagram.androif')
        self.assertEqual(self.evaluate(activity=ACT,device=old,
            stage={'package':'com.instagram.androif'},
            meta0={'current_package':'com.instagram.androif','current_activity':ACT}), (True,''))

    def test_wrong_version_clone_owner_serial_and_context_rejected(self):
        devices=[self.device(('372.0.0.48.60',1)), self.device(('446.0.0.49.77',1)),
                 self.device(package='com.instagram.androif')]
        d=self.device(); d._instagram_search_observed_version=(0,*d._instagram_search_observed_version[1:]);devices.append(d)
        d=self.device();d.serial='other';devices.append(d)
        d=self.device();d._instagram_446_observed_context=None;devices.append(d)
        for d in devices:
            with self.subTest(device=vars(d)):
                self.assertEqual(self.evaluate(device=d),(False,'positive_viewer_contract_missing'))

    def test_existing_foreground_and_stage_contradictions_rejected(self):
        for changes in (
            {'stage':{'package':'foreign'}},
            {'stage':{'activity':'foreign.OverlayActivity'}},
            {'meta0':{'current_package':PKG,'current_activity':'foreign.OverlayActivity'}},
            {'meta0':{'current_package':'foreign','current_activity':'.activity.MainTabActivity'}},
            {'stage':{'viewer_type':'Story'}}, {'stage':{'post_identity_confirmed':False}},
            {'stage':{'v5_positive':False}}, {'post_opened_via_profile_grid':False},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(self.evaluate(**changes)[0])

    def test_reason_precedence_and_age_unchanged(self):
        cases=[({'stage':{'candidate_username':'other'},'stage_ctx_hash_ok':False},'candidate_mismatch'),
               ({'stage':{'story_or_highlight_detected':True},'v2':False},'story_or_highlight_detected'),
               ({'v2':False,'stage_ctx_hash_ok':False},'stage_context_stale'),
               ({'stage_ctx_hash_ok':False,'stage_binding_ok':False},'proof_hash_mismatch'),
               ({'stage_binding_ok':False},'run_action_binding_mismatch')]
        for kwargs,expected in cases:
            self.assertEqual(self.evaluate(**kwargs),(False,expected))
        self.assertEqual(self.evaluate(age=100),(True,''))
        self.assertEqual(self.evaluate(age=4456.9),(True,''))
        self.assertEqual(self.evaluate(age=3000,v2=False),(True,''))
        self.assertEqual(self.evaluate(age=3000.01,v2=False),(False,'stage_context_stale'))

    def test_no_new_io_and_real_authority_used(self):
        import socket
        from run_latency_observability_v1 import _TimedProxy
        d=self.device()
        with mock.patch.object(time,'sleep',side_effect=AssertionError('sleep')), \
             mock.patch.object(socket,'create_connection',side_effect=AssertionError('network')):
            self.assertEqual(self.evaluate(device=d),(True,''))
        # SimpleNamespace has no UI, XML, screenshot, exists, or app_current API.
        # TimedProxy consumes the same retained raw owner; no timed operation needed.
        proxy=object.__new__(_TimedProxy)
        vars(proxy).update(_kind='device',_raw=d)
        self.assertEqual(self.evaluate(device=proxy),(True,''))

    def test_all_other_navigation_bytes_unchanged(self):
        import subprocess
        from pathlib import Path
        root=Path(nav.__file__).resolve().parent
        original=subprocess.check_output(['git','show',
            'fcd58cb1b7ad9b5d4d5a88503bdfbc88b71259ab:instagram_navigation.py'],cwd=root,text=True)
        current=restore_exact_like_budget_delta(Path(nav.__file__).read_text())
        current = restore_liketap_validator_activity(current)
        import ast
        a=next(n for n in ast.parse(original).body if isinstance(n,ast.FunctionDef) and n.name=='visual_like_open_post')
        b=next(n for n in ast.parse(current).body if isinstance(n,ast.FunctionDef) and n.name=='visual_like_open_post')
        old_lines=original.splitlines(True);new_lines=current.splitlines(True)
        self.assertEqual(old_lines[:a.lineno-1],new_lines[:b.lineno-1])
        self.assertEqual(old_lines[a.end_lineno:],new_lines[b.end_lineno:])


VALIDATOR_ACTIVITY_HUNK = ('        activity_allowed = bool(\n            "instagram" in live_activity.lower() and "mainactivity" in live_activity.lower()\n        )\n        if (not activity_allowed and live_package == "com.instagram.android"\n                and live_activity in (\n                    "com.instagram.android.activity.MainTabActivity", ".activity.MainTabActivity")):\n            try:\n                activity_allowed = _followers_row_activity_allowed(d, live_package, live_activity)\n            except (AttributeError, TypeError):\n                activity_allowed = False\n        if not activity_allowed:\n            return False, "liketapcontext_viewer_mismatch", age_ms\n', '        if "instagram" not in live_activity.lower() or "mainactivity" not in live_activity.lower():\n            return False, "liketapcontext_viewer_mismatch", age_ms\n')

def restore_liketap_validator_activity(source):
    after, before = VALIDATOR_ACTIVITY_HUNK
    if source.count(after) != 1:
        raise AssertionError('validator_activity_hunk_not_unique')
    return source.replace(after, before, 1)


class LikeTapValidatorActivity446Tests(unittest.TestCase):
    def setUp(self):
        configure_canary(canary, account_id=ACCOUNT_ID, account_username='bridge_account',
                         run_id='run-validator', package=PKG, resume_policy=None)
        self.binding = dict(account_id=ACCOUNT_ID, run_id='run-validator',
                            request_id='request-validator', action_id='action-validator')

    def evaluate(self, activity='.activity.MainTabActivity', package=PKG,
                 version=('446.0.0.49.77',385211304), changes=None,
                 live_activity=None, device_mutation=None, bad_hash=False):
        d=LikeContextActivityAuthority446Tests.device(version,package)
        calls=[]
        d.app_current=lambda: (calls.append('app_current') or
            dict(package=package,activity=activity if live_activity is None else live_activity))
        d.window_size=lambda: (calls.append('window_size') or (1080,2340))
        if device_mutation: device_mutation(d)
        ctx=dict(version='LikeTapContextV2',**self.binding,
            candidate_username='azafranytaconesespanol',package=package,activity=activity,
            viewer_type='Posts',v5_positive=True,story_or_highlight_detected=False,
            exact_like_source=package+':id/row_feed_button_like',
            like_bounds=dict(left=34,top=1440,right=102,bottom=1569),
            canonical_generation=canary.runtime_context()['ui_generation'],
            created_at_monotonic=100.,ttl_ms=600.,one_shot_nonce='offline-only',
            consumed=False,already_liked=False)
        ctx.update(changes or {})
        ctx['proof_hash']=nav._like_tap_context_v2_proof_hash(ctx)
        if bad_hash:ctx['proof_hash']='invalid'
        # Freeze clock only; use the actual validator and actual retained-version authority.
        with mock.patch.object(nav.time,'monotonic',return_value=100.1):
            result=nav._validate_like_tap_context_v2(ctx,expected_stage_binding=self.binding,
                expected_follower_username='azafranytaconesespanol',d=d,expected_package=package)
        return result,calls

    def test_positive_exact_activity_matrix_and_no_added_io(self):
        for activity,package,version in [
            (ACT,'com.instagram.androif',('372.0.0.48.60',377611059)),
            (ACT,PKG,('446.0.0.49.77',385211304)),
            ('com.instagram.android.activity.MainTabActivity',PKG,('446.0.0.49.77',385211304)),
            ('.activity.MainTabActivity',PKG,('446.0.0.49.77',385211304))]:
            with self.subTest(activity=activity,package=package):
                result,calls=self.evaluate(activity,package,version)
                self.assertEqual(result[:2],(True,''))
                self.assertEqual(calls,['app_current','window_size'])

    def test_unqualified_activity_version_package_rejected(self):
        for kwargs in [dict(version=('372.0.0.48.60',377611059)),
            dict(version=('446.0.0.49.77',385211305)),dict(version=('447.0.0',385211304)),
            dict(package='com.instagram.androif'),dict(package='foreign.package'),
            dict(activity='foreign.MainTabActivity'),dict(activity='overlay.MainTabActivity'),
            dict(activity='com.instagram.android.activity.MainTabActivityOverlay')]:
            with self.subTest(kwargs=kwargs):
                result,calls=self.evaluate(**kwargs)
                self.assertEqual(result[:2],(False,'liketapcontext_viewer_mismatch'))
                self.assertEqual(calls,['app_current'])

    def test_retained_owner_serial_context_required(self):
        for mutation in [lambda d:vars(d).pop('_instagram_446_observed_context'),
            lambda d:setattr(d,'serial','wrong-serial'),
            lambda d:setattr(d,'_instagram_446_observed_context',None),
            lambda d:setattr(d,'_instagram_search_observed_version',(0,d.serial,PKG,'446.0.0.49.77',385211304)),
            lambda d:setattr(d,'_instagram_search_observed_version',None)]:
            result,calls=self.evaluate(device_mutation=mutation)
            self.assertEqual(result[:2],(False,'liketapcontext_viewer_mismatch'))
            self.assertEqual(calls,['app_current'])

    def test_activity_equality_precedes_membership(self):
        result,calls=self.evaluate(live_activity='com.instagram.android.activity.MainTabActivity')
        self.assertEqual(result[:2],(False,'liketapcontext_activity_mismatch'))
        self.assertEqual(calls,['app_current'])

    def test_original_reasons_and_ttl_preserved(self):
        for changes,reason in [({'viewer_type':'Reels'},'viewer_mismatch'),
            ({'candidate_username':'wrong'},'candidate_mismatch'),
            ({'v5_positive':False},'v5_rejected'),
            ({'story_or_highlight_detected':True},'v5_rejected'),
            ({'account_id':'wrong'},'account_id_mismatch'),
            ({'run_id':'wrong'},'run_id_mismatch'),
            ({'request_id':'wrong'},'request_id_mismatch'),
            ({'action_id':'wrong'},'action_id_mismatch'),
            ({'created_at_monotonic':99.},'ttl_expired'),
            ({'exact_like_source':''},'exact_like_source_missing'),
            ({'consumed':True},'consumed'),({'already_liked':True},'already_liked'),
            ({'canonical_generation':-1},'generation_changed')]:
            with self.subTest(reason=reason):
                self.assertEqual(self.evaluate(changes=changes)[0][:2],
                                 (False,'liketapcontext_'+reason))
        self.assertEqual(self.evaluate(bad_hash=True)[0][:2],(False,'liketapcontext_hash_mismatch'))

    def test_exact_inverse_hunk_rejects_changed_missing_duplicate(self):
        from pathlib import Path
        source=Path(nav.__file__).read_text();after,before=VALIDATOR_ACTIVITY_HUNK
        for mutation in [source.replace(after,''),source+after,
                         source.replace(after,after.replace('activity_allowed = False','activity_allowed = True'))]:
            with self.assertRaises(AssertionError):restore_liketap_validator_activity(mutation)

    def test_only_validator_hunk_changed_against_signed_baseline(self):
        import subprocess
        from pathlib import Path
        root=Path(nav.__file__).resolve().parent
        baseline=subprocess.check_output(['git','show',
            'c2677f10507b873b71c3a550ee497322a62e128c:instagram_navigation.py'],cwd=root,text=True)
        current=restore_exact_like_budget_delta(Path(nav.__file__).read_text())
        self.assertEqual(restore_liketap_validator_activity(current),baseline)
        self.assertNotEqual(restore_liketap_validator_activity(current+'\n'),baseline)
