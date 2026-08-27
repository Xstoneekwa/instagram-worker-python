"""Offline equivalence/security fixtures and read-only verifier benchmark.

No service launch, environment-file read, database, tick or device action.
Synthetic signing keys are created only by the existing test fixture helper.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from time import perf_counter
import types
import unittest
from unittest.mock import patch

import follow60_lock_v3 as candidate
from follow60_deployment_gate_v1 import verify_deployment_candidate
from tests import test_follow60_deployment_gate_v1 as fixtures


class VerificationPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.git("config", "user.name", "Synthetic")
        self.git("config", "user.email", "synthetic@example.invalid")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args])

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "synthetic")
        return self.git("rev-parse", "HEAD").decode().strip()

    def test_batch_exact_bytes_modes_symlink_empty_binary_and_duplicate_blob(self):
        for name, value in {"empty": b"", "binary": b"\x00\xff\nblob\x00", "a": b"shared", "b": b"shared", "space name": b"z"}.items():
            (self.root / name).write_bytes(value)
        (self.root / "a").chmod(0o755)
        (self.root / "link").symlink_to("a")
        sha = self.commit()
        names = ["empty", "binary", "a", "b", "space name", "link"]
        with patch.object(candidate, "git", wraps=candidate.git) as calls:
            result = candidate.git_file_entries(self.root, names, revision=sha)
        self.assertEqual(calls.call_count, 2)
        for name in names:
            payload = self.git("show", f"{sha}:{name}")
            mode, kind, oid = self.git("ls-tree", sha, "--", name).split(b"\t")[0].decode().split()
            self.assertEqual(kind, "blob")
            self.assertEqual(result[name], {"path": name, "mode": mode, "git_blob_oid": oid,
                "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})

    def test_no_cached_blob_after_release_change(self):
        target = self.root / "source.py"
        target.write_text("VALUE=1\n")
        first = self.commit()
        before = candidate.git_file_entry(self.root, "source.py")
        target.write_text("VALUE=2\n")
        self.commit()
        self.assertNotEqual(before, candidate.git_file_entry(self.root, "source.py"))
        self.assertEqual(before, candidate.git_file_entry(self.root, "source.py", revision=first))

    def test_missing_and_non_blob_paths_are_not_admitted(self):
        (self.root / "nested").mkdir()
        (self.root / "nested" / "source.py").write_text("pass\n")
        self.commit()
        self.assertEqual(candidate.git_file_entries(self.root, ["missing", "nested"]), {})
        with self.assertRaises(ValueError):
            candidate.git_file_entry(self.root, "missing")

    def test_malformed_batch_response_fails_closed(self):
        (self.root / "source.py").write_text("pass\n")
        self.commit()
        real_git = candidate.git
        for bad in [b"", b"wrong blob 1\nx\n", b"missing\n"]:
            def fake_git(root, *args, **kwargs):
                return bad if args[0] == "cat-file" else real_git(root, *args, **kwargs)
            with self.subTest(bad=bad), patch.object(candidate, "git", side_effect=fake_git), self.assertRaises(ValueError):
                candidate.git_file_entry(self.root, "source.py")

    def test_import_traversal_all_statement_containers(self):
        names = [f"synthetic_{i}" for i in range(12)]
        for name in names:
            (self.root / f"{name}.py").write_text("pass\n")
        source = '''import synthetic_0
if True:
    import synthetic_1
else:
    from synthetic_2 import value
def outer():
    import synthetic_3
    def inner():
        import synthetic_4
class Klass:
    import synthetic_5
for item in []:
    import synthetic_6
else:
    import synthetic_7
while False:
    import synthetic_8
try:
    import synthetic_9
except Exception:
    import synthetic_10
finally:
    with object():
        import synthetic_11
'''
        expected = {f"{name}.py" for name in names}
        self.assertEqual(candidate.direct_local_imports(self.root, "main.py", source.encode()), expected)
        if hasattr(ast, "match_case"):
            self.assertEqual(candidate.direct_local_imports(self.root, "main.py", b"match x:\n case 1:\n  import synthetic_0\n"), {"synthetic_0.py"})
        self.assertEqual(candidate.direct_local_imports(self.root, "main.py", b"async def f():\n async with ctx:\n  async for x in y:\n   import synthetic_1\n"), {"synthetic_1.py"})

    def test_import_traversal_equivalent_to_frozen_graph(self):
        root = Path(__file__).resolve().parents[1]
        baseline = types.ModuleType("frozen_follow60_verifier")
        source = subprocess.check_output(["git", "-C", str(root), "show", "HEAD:follow60_lock_v3.py"])
        exec(compile(source, "frozen_follow60_verifier.py", "exec"), baseline.__dict__)
        # Read frozen source bytes, not this candidate's changed import graph.
        manifest = json.loads((root / "docs/governance/FOLLOW60_MAINLINE_LOCK_V3.json").read_text())
        for name in manifest["protected_entries"]:
            if name.endswith(".py"):
                payload = subprocess.check_output(["git", "-C", str(root), "show", f"HEAD:{name}"])
                with self.subTest(path=name):
                    self.assertEqual(candidate.direct_local_imports(root, name, payload), baseline.direct_local_imports(root, name, payload))

    def test_actual_signature_gate_rechecks_warm_trust_sha_and_tamper(self):
        helper = fixtures.Follow60DeploymentGateV1Tests()
        temp, root, sha, private, public, manifest, signature = helper._repo()
        self.addCleanup(temp.cleanup)
        payload = json.dumps(helper._manifest(root, sha), sort_keys=True).encode()
        manifest.write_bytes(payload)
        signature.write_bytes(private.sign(payload))
        def verify():
            return verify_deployment_candidate(root, manifest_path=manifest, signature_path=signature, public_key_path=public)
        for _ in range(2):
            self.assertTrue(verify()["ok"])
        valid_signature = signature.read_bytes()
        signature.write_bytes(b"\0" * 64)
        self.assertFalse(verify()["ok"])
        signature.write_bytes(valid_signature)
        manifest.write_bytes(payload + b" ")
        self.assertFalse(verify()["ok"])
        manifest.write_bytes(payload)
        self.assertTrue(verify()["ok"])
        (root / "protected.py").write_text("VALUE=99\n")
        self.assertFalse(verify()["ok"], "Dirty protected bytes cannot reuse a signed HEAD")
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "next synthetic release"], check=True)
        self.assertFalse(verify()["ok"], "A different release cannot reuse a warm trust result")


def benchmark(root: Path, samples: int) -> dict:
    durations = []
    for _ in range(samples):
        start = perf_counter()
        result = verify_deployment_candidate(root)
        durations.append((perf_counter() - start) * 1000)
        if not result.get("ok"):
            raise RuntimeError(json.dumps(result))
    ordered = sorted(durations)
    import math
    return {"scope": "full_verifier_only_not_status_query", "samples": samples,
        "p50_ms": ordered[math.ceil(samples * .5) - 1], "p95_ms": ordered[math.ceil(samples * .95) - 1],
        "durations_ms": durations, "verified_artifact_sha": result["candidate_sha"],
        "verifier_source_sha256": hashlib.sha256(Path(candidate.__file__).read_bytes()).hexdigest(),
        "trust_cache": "NONE", "activation": False}


if __name__ == "__main__":
    import sys
    if "--benchmark-root" in sys.argv:
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--benchmark-root", type=Path, required=True)
        parser.add_argument("--samples", type=int, default=30)
        parser.add_argument("--output", type=Path, required=True)
        args = parser.parse_args()
        if args.samples < 1:
            parser.error("samples must be positive")
        result = benchmark(args.benchmark_root, args.samples)
        with args.output.open("x") as stream:
            json.dump(result, stream, indent=2)
        print(json.dumps({k: v for k, v in result.items() if k != "durations_ms"}))
    else:
        unittest.main()
