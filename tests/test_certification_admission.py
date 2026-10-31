"""Offline admission contract, actual runner hook AST and subprocess durability."""
import ast
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import certification_admission as ca

# Exact inverse of the owner-scoped ordinary refusal evidence delta only.
_ORDINARY_RECORDER_EXACT_INVERSE = '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'


def restore_ordinary_recorder_delta(name, source):
    import base64, zlib
    record = json.loads(zlib.decompress(base64.b64decode(_ORDINARY_RECORDER_EXACT_INVERSE))).get(name)
    if record is None:
        return source
    if hashlib.sha256(source.encode()).hexdigest() != record['candidate_sha256']:
        raise AssertionError('ordinary_recorder_source_changed:' + name)
    for hunk in reversed(record['hunks']):
        offset, new = hunk['offset'], hunk['new']
        if source[offset:offset+len(new)] != new:
            raise AssertionError('ordinary_recorder_hunk_changed:' + name)
        source = source[:offset] + hunk['old'] + source[offset+len(new):]
    if hashlib.sha256(source.encode()).hexdigest() != record['base_sha256']:
        raise AssertionError('ordinary_recorder_inverse_mismatch:' + name)
    return source


ROOT = Path(__file__).resolve().parents[1]
BASE = 'a0d995be8df3ea1a773c572bc900aa1d3a360df6'

# Exact inverse of the signed XML-self delta. Full restored historical hashes
# enforce position and all other bytes; no whole-file exclusion or hash replacement.
SELF_XML_BASE_HASHES = {'runner.py': '290ee55729856b9d3f4f821bce43b4e0a10a4903755c95fcfd4b7dbdb90e6829', 'account_session_orchestrator.py': 'ff22ffb59d7b1dd73e502fe32d374084c76b9a63b2a75ca692c7b94081c6839c'}
SELF_XML_EXACT_HUNKS = {'runner.py': [('    auto_restart_resume_policy: dict[str, Any] | None = None,\n    worker_runtime_identity: WorkerRuntimeIdentity | None = None,\n    account_session_snapshot: Any | None = None,\n    verified_current_account_username: str | None = None,\n) -> int:\n    """\n    Source profile → source followers list → follower profile → FOLLOW SAFE V1 → return to list.\n', '    auto_restart_resume_policy: dict[str, Any] | None = None,\n    worker_runtime_identity: WorkerRuntimeIdentity | None = None,\n    account_session_snapshot: Any | None = None,\n) -> int:\n    """\n    Source profile → source followers list → follower profile → FOLLOW SAFE V1 → return to list.\n'), ('    global _RUNTIME_SKIPPED_USERNAMES\n    global _VISUAL_FOLLOWERS_OPEN_COUNT_THIS_SESSION\n    _VISUAL_FOLLOWERS_OPEN_COUNT_THIS_SESSION = 0\n    _verified_current_account_key = _norm_ig_handle(verified_current_account_username or "")\n    _snapshot_payload = (\n        account_session_snapshot.to_payload()\n        if hasattr(account_session_snapshot, "to_payload")\n', '    global _RUNTIME_SKIPPED_USERNAMES\n    global _VISUAL_FOLLOWERS_OPEN_COUNT_THIS_SESSION\n    _VISUAL_FOLLOWERS_OPEN_COUNT_THIS_SESSION = 0\n    _snapshot_payload = (\n        account_session_snapshot.to_payload()\n        if hasattr(account_session_snapshot, "to_payload")\n'), ('                    return None\n                for c in cand_list:\n                    ckey = _norm_ig_handle(str(c.get("username") or ""))\n                    if _verified_current_account_key and ckey == _verified_current_account_key:\n                        log(\n                            "info",\n                            "followers_candidate_skipped_source_account_self",\n                            username=c.get("username"),\n                            source_profile_username=source_profile_username,\n                            skip_reason="source_account_self",\n                        )\n                        _expl_v1.note_visible_skip("source_account_self")\n                        continue\n                    if ckey == src_key:\n                        log(\n                            "info",\n', '                    return None\n                for c in cand_list:\n                    ckey = _norm_ig_handle(str(c.get("username") or ""))\n                    if ckey == src_key:\n                        log(\n                            "info",\n'), ('        ),\n    )\n\n    verified_current_account_username = None\n    if (\n        supabase_mode\n        and not unfollow_session_run\n', '        ),\n    )\n\n    if (\n        supabase_mode\n        and not unfollow_session_run\n'), ('                target_username=account_username or None,\n            )\n            return _return_with_cleanup(d, 75)\n        if isinstance(identity.actual_logged_in_username, str):\n            verified_current_account_username = identity.actual_logged_in_username or None\n\n    if account_session_run and supabase_mode and account_id:\n        from auto_restart_runtime import load_resume_policy_from_env\n', '                target_username=account_username or None,\n            )\n            return _return_with_cleanup(d, 75)\n\n    if account_session_run and supabase_mode and account_id:\n        from auto_restart_runtime import load_resume_policy_from_env\n'), ('        )\n        asess_code = dispatch_account_session(\n            d,\n            verified_current_account_username=verified_current_account_username,\n            account_id=account_id,\n            account_username=account_username,\n            run_id=run_id or None,\n', '        )\n        asess_code = dispatch_account_session(\n            d,\n            account_id=account_id,\n            account_username=account_username,\n            run_id=run_id or None,\n'), ('        )\n        eng_code = _run_followers_list_engine_session(\n            d,\n            verified_current_account_username=verified_current_account_username,\n            source_profile_username=source_profile_username,\n            account_id=account_id,\n            run_id=run_id,\n', '        )\n        eng_code = _run_followers_list_engine_session(\n            d,\n            source_profile_username=source_profile_username,\n            account_id=account_id,\n            run_id=run_id,\n')], 'account_session_orchestrator.py': [('    auto_restart_resume_policy: dict[str, Any] | None = None,\n    worker_runtime_identity: WorkerRuntimeIdentity | None = None,\n    account_session_snapshot: AccountSessionCPlusSnapshot | None = None,\n    verified_current_account_username: str | None = None,\n) -> dict[str, Any]:\n    total_targets = len(follow_targets)\n    max_targets = _resolve_max_follow_targets_per_run(total_targets, max_targets_per_run)\n', '    auto_restart_resume_policy: dict[str, Any] | None = None,\n    worker_runtime_identity: WorkerRuntimeIdentity | None = None,\n    account_session_snapshot: AccountSessionCPlusSnapshot | None = None,\n) -> dict[str, Any]:\n    total_targets = len(follow_targets)\n    max_targets = _resolve_max_follow_targets_per_run(total_targets, max_targets_per_run)\n'), ('            "worker_runtime_identity": worker_runtime_identity,\n            "account_session_snapshot": account_session_snapshot,\n        }\n        if verified_current_account_username is not None:\n            call_kwargs["verified_current_account_username"] = verified_current_account_username\n        if follow60_canary_active:\n            call_kwargs.update(\n                {\n', '            "worker_runtime_identity": worker_runtime_identity,\n            "account_session_snapshot": account_session_snapshot,\n        }\n        if follow60_canary_active:\n            call_kwargs.update(\n                {\n'), ('    follow60_mainline_active: bool = False,\n    follow60_business_session_binding: dict[str, Any] | None = None,\n    worker_runtime_identity: WorkerRuntimeIdentity | None = None,\n    verified_current_account_username: str | None = None,\n) -> int:\n    global _LAST_ACCOUNT_SESSION_SUMMARY\n    _LAST_ACCOUNT_SESSION_SUMMARY = {}\n', '    follow60_mainline_active: bool = False,\n    follow60_business_session_binding: dict[str, Any] | None = None,\n    worker_runtime_identity: WorkerRuntimeIdentity | None = None,\n) -> int:\n    global _LAST_ACCOUNT_SESSION_SUMMARY\n    _LAST_ACCOUNT_SESSION_SUMMARY = {}\n'), ('                )\n                rotation_result = _run_follow_target_rotation(\n                    d,\n                    verified_current_account_username=verified_current_account_username,\n                    account_id=aid,\n                    account_username=uname,\n                    run_id=run_id,\n', '                )\n                rotation_result = _run_follow_target_rotation(\n                    d,\n                    account_id=aid,\n                    account_username=uname,\n                    run_id=run_id,\n'), ('    follow60_mainline_active: bool = False,\n    follow60_business_session_binding: dict[str, Any] | None = None,\n    worker_runtime_identity: WorkerRuntimeIdentity | None = None,\n    verified_current_account_username: str | None = None,\n) -> int:\n    return run_account_session(\n        d,\n        verified_current_account_username=verified_current_account_username,\n        account_id=account_id,\n        account_username=account_username,\n        run_id=run_id,\n', '    follow60_mainline_active: bool = False,\n    follow60_business_session_binding: dict[str, Any] | None = None,\n    worker_runtime_identity: WorkerRuntimeIdentity | None = None,\n) -> int:\n    return run_account_session(\n        d,\n        account_id=account_id,\n        account_username=account_username,\n        run_id=run_id,\n')]}


_DM_CERTIFICATION_RUNNER_DELTA = 'eJzNVm1vIjcQ/isW/QCcaMS+sCyRqEQJvUbXo2lIPl1OlteeJVYW79b2kkSn/Pd6lwX2BXI06UnlQwL2zDMzzzwe+1srIAoiLgCre2IPvNY5avVd5vmsP+wHFgl9Eg6YHYQULHC9Pg0Zgz5zLGY5fbA8OnSH9iAg4dBzAxqOHIeyVg+1KBGMM6LLuIHjhgbBG1jAXPBc8MH13SAY2rYfwhAsxwEYDVzb8XzihaOhRWw6cqnTH9iWR4IM9z4VD8qAffnWiiOWoSLzSUUYR1H8iONUSyD0Hic82ZQlU4HGCHOFXzfqELlU3TuRBRHw+AOQ70QGyFdJLDViK0xBah5ySjSPBSZsxZUy3xBRjd2Na8OHxkLDkzZZ1LfO1iTa0G8SECDL1cVhqECbAi1rNHRc/6WHdmTyEGWJaA0s88ScIdNIRCiNU6GxgjzFbKvC1BG3zgE/FMvjhXCFRKzRPBbQSNbuW647eluy+XpmIuHvFJTemm68fngpp4SvFOrbbrnQXDchCuI46ixBE61lx8QJ+bKH7lqT6fTP2/kNniwWlx/nn2fm68Xl4mpyM/0dz+aTX/+YXdyZCL+RSEG3e96QuEE+pYrzjQhzIXKVEG1kTvXTIfFJcw64WO4446y360qqQAqygl5Rfa9GTLcUBtacmgECkpMoi1MK+6VNWFBstb/ufTa0nF3OFzeTj9eTz/hqMv00+ThruCeEPpAl4CyXMsDOKoqXOOQQMZX7Ut0pA5TSNIadNjcjoN1D7QaTBRdm78OHA9gFjhkW/21/S3KyvOFgVJdT9jGZmUYzKHOzm2CFzjv7OnN2etXf+w6PS80+bLPt/Lghhar9Rhfj4vCZM5apr2TTren3bRquE1DX8JaRGgEbEo7U/a9LbZTbQ2sjaSMNtsfYrdBUSjBg3wEtSROMJF6p+cSmH2r8qc1/iwAarBwQQV5oVeiO7bp+5TZjEKIV4aLTRT//grjQBRdaPlfH4DHDd1zW+xilDAcjx7LqRxGeKCQaHYlwNi2vL3ScZDGNT3OQF0ANKb+CsKUyn2EgZSwPDjFlnBJgZs/oRMVirMyEMgjd4+MAT2+vr7NZtR1flxfbkb/fu76dZ+u187+7C6qG17O/bmeLDZAiIWAd52ecixRwysebAbjHkqBTKZBVTNh3sVzp4cBpjNMCfZb/K3RRZdhMKK64UJoIChl3PXQl4SK/4qamChlHVxERcCvImvCIBBEcuKtPitMQgHmiJhKUmR2dbm0c/IRu7sFA6OwkRkilCcg1V+bAkcicOBQ/ClUlDWkgkpn1syrSdySUCnhKgGbPqkxN7QNnPnfG+jmBcfYnF9gZzm9ojE/rebPv7+S+1HjP8i2/3viQG9qi52oDjonM9AFEhs4afTjmsiLyAZvmrLI4nXbtbZG/7TE8cd2u34rNxJrPNFjHD2AeI9RAd/8fFZT5HvkD9+Xryz8Js8hV'

# Exact inverse of the approved standalone Outreach activation split. The
# historical DM inverse below remains unchanged and receives its exact bytes.
_OUTREACH_ROUTING_LINE = '        if supabase_mode and run_request_id and not _follow60_canary_active and not outreach_session_run:\n'
_OUTREACH_ROUTING_OFFSET = 1246624
_OUTREACH_ROUTING_CURRENT_SHA256 = '1b0f13229f975e8dbcf1d872d9193418ab0ecda5d6065d01783ccb9369240b27'
_OUTREACH_ROUTING_BASE_SHA256 = 'b34f60c651ed4e64e8e484bb7228fe7e133ee9542368a6f971a2c94c305216ab'


# BEGIN SIGNED PROFILE DELTA
_PROFILE_EXACT_INVERSE = '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'


# Exact inverse of the signed managed-Outreach/slot delta against certified 9165651.
# Full hashes plus positional hunks reject moved, duplicated, weakened or unrelated
# edits; historical inverse layers and their expected bytes remain unchanged.
_PRODUCT_EXACT_INVERSE = 'eNrdfQ1320iO4F9hu99bSRlFISWKotSj3pdJe2ez253kEve823PyOPwo2mxLpIaU7Kgz/u8H1AdZVSxKsuP03u3M7sRifaMAFIACUF/Owjgudvk2KHd5UJJ/7Ei1DeIir3ZrUo42+7PFl7MorAh8W6+z7dnibO54U2/qkNl0PJ66U8/z0hlJwiiaESd03Pk0scMw9u2zIWtYXYfjqQcNnSS0x+7cnnrjcOpH88gf2/HMmblxktph7NnTcZi6s9AhXpzacxfLYj/0x04cJ/Y4hg7jME+yJNxKvXokGpNZMvO8ydhOJtGUENvxIjcJ3eksiUPHh/n5XuLNEuJPncQjqROO/dSd+G7k2QR6vd7lN9XZ4vLLWU7uoMuEpBbAAqAQZysSrMhVGO8FcKrgLtteB1tSrrM8XCHcqv7HHLop0rQiAKGx5409+LlKeF9NV0f6uB+KKVjafz6eVWRF4u3HswX8nSVDsW3wZ7UNt7tqiBsIv/gQJKE7ut1vyHBNtiEALQyqMCUfz4Yf83b3Sp3nPxa7bUnC+DqoVsU2uHX4uNUo361WtAt5wTNnPhMLfsqZs4FUqChAs5bWl2Y11bbsl8Xd6Ips+zjUx7OBVZQwKvwxgsJs0x8srCSLt1ht0DRMoRZ8sbLcwl6bgiy1sirLYZ55TLDRkDaXmgJCauOyRZnGHq2KO1L2pdYw4JePZ0BamxWBhcN6oUUaAqrwv6ttsdmIHzHOoi6KVkV8I36EUVHSDu5Z5/wfAazsd5IArGz2lS6XAZsumSPlQll3XmyVtbNKfP0LFYUAvbdZviOdgGONOYA0dBzwPikoP561MU+a42Wr9aejUxFMLUMI5EW5puCoed1ulyXaBCnmSLvE8BNa041WqrIi02YrzaGtgre0NWs7aEFdmjB0Sr/s8uPLhN4Y6omJ7vJHYKQYvO7qizqwgq0L9edQr1pj70LF3laXddGRijXOLxQCaKrd15DlvEWiJ2mV23KvAbQk1W61hfVSvArEqgLD8dhv889mywzMNU6vRndFeUNKc7kKckMFUpZFCVNKyNIwNv7nTZETc0mW6ltq5DkNzO/N/ZBVRaz049kqy284h/7SgPkesKrVbNC5lDWpqvCKUFD/Ny/p49nPdEUW2yGrgqllBTIc4EHwOawJ10I82pXEwkHJ6NQVR4A+CUAMKbYsVsFmFeZkeVHuiFZZQk/yOSabrXVO/8HZhBV+01B2VVz1TUf5XVjmWX7VcdDLfKhmgkJCyUIcLrgrM0D9+hxqd1Oj8/JU5IaqB4mEMcMlFwXM5Wy3lxJ1dyDYEvkfAGxwuRjb9qduQHceXowPI0v4w4AOQwYbUlZZtf3/DuoqPS4PcrSj4FeElj8tLacpakMfTus8LQxHxUGQGwB8GnBPAewhoJ4C0IcAs0YYnaNwKJdkuyspdyxu8NSktVDKiipS3rKTdEVyIdVUg6EEOtwArCD/Bl6K/z1drQFoLqyfsmoTboGhlq+KPM2uhtazobXKQKFcAPPGY3dsD6znP1JJ8BKod2i9zPdCtmMKGJ7ND1LKYGQ+yJL+LweJevZvyiIiKDHtNiHTc1cZybejGmV+q4ocZKi/nl9wSbstEFRYAn+V+2VLWtKUH10VO1XzAuYwkrUvXc5iAt4CT+gsH/W/9Ia90W9Flvcrqhf0X766eP238+D9+f/69fzDRfDh4uXFrx/OPwwG9wM2CyHItgez8NAH8LEyRxW3hjW2brM1gbkvpyN7aK3DzwAckDFJtbSp2Hn5qcVfw3zfl5UEWQ+Qvx9WG4xn+0FVwqBEMJUISyk+DFrCIaUihnNN0TrMQYpJVMTkHx+DmSaCZWNyEOCnAcCGkjFdJB/NVG48iFS6l7uuC4YW7Nif9J6V4oNnnOAaSvdqoXGIdhUutx2XhgChdPrNqmBbhnmFP1TZKyCiIyoi6FsdZhVpb8ezZ2w5lHfSjfm3EMRH/EklDkYcYu+b3SZhUstREut8OL58LQ9Fte/j2ducWMmuDKMVTL24y0n5vIoLUNFQuC2tDczkB6BNK9oB/YEkjOsv9xa1F8Qkg11IYEWgA1D590zIwGlZrC1NnKlA/ray9Qb4j/VeKfoAJUPr7bvz9y8v3r4Pzj+8evnzy4vXb98EP53//PK/WJcIBUO7vnI4DxAVcCANF+jQFVUhDOJbDyWG3tDqSdvUmnoAINhDpQ6xYGBkEF96xU1PHLI9QTPwxZZGQ/zGT/eH1VHaoYQn9WRxk1ocZEgXvcT/ke1a2GC1fwwMNmFVBbdjtxsEBiYAmFSGW1DwCExkxTpKyCrcLzt3G7D4Gc56tMbjIq4GRiJRF88GNhMFK3vG/9XIg3/F8eDPY8TyOiGAvltgIKu9Fa8K0BbDeJvdEjzrgf+WxV1l3V3jd6YVU0sP4J2Y7QhXgv29bJTIv/89u6JE/fe/M5MjVL8mQsTBFd+CHAGsEaE8sn4iMEFQNxNLHGasxzBJMgQvbi89gjPQVqxsi5wRJimIXJDtyPolA702v8LzrUDbHJTcwiELPf/AeszyW8DNxMKv2LKueE1WCT0fxe7SpuRuSGkUpqVxi5EMQ5PIJTgdSl248ymBDewfFMCG1jHBKgbgoOk43D6hafz/CeFM769bUlPkMhDB+s4QUb/PZAwQmIDyl/D/LdMmbsWlOo5q0z7FzKkb0oVsnGu2G6NB3WBHP2XMtlDJF2RmpwfVINuk+shcml84HNEUtN1iekNLZ2U8oFViVCKM+M4xGzh8Vl0zvN9tEk4CHVYIhjUtLOQgM2HbfYdqKgv7ag1F8pf1UVkJOC7RmRT9LhPLaeYVRRR7lO5/xLQ0+CPl0ZYUehyTFVlVXQWVTOULPd+b2OJCz3ArKR/Ylz08BwMCp8cO0a/3idtspP4mU382P9Cf4LJAXfL1Sk/hvj0VwH39MqbXZu89inW9Xuu+w1ouQT6rmTmz+fYMAk0psysxHZln9Xv6mdBD7levCMeva8ThehNmV7lea6DvOhWo62ayjEaFcC5W0wMbL8P1CkNx7gcgH+DuVLs4hkUOrdfslL/gDd6zai0eTTcYD/6+ZOXivFWfawdC1LIgl04axNAbU3mUEzeKpMZ1y6Tc7npotvCbrXWagQ5N/cC5lz0uAQkgBrh+mI+Qbihb2y8pxRmsDm07JpsB27Gl4T6jLfWLfshnbCGwg2F3C80G2mGqYye93jDfopDPGzhLqOWC3obzMfUe+Welwy/33d2N2AnU7wB7I5ctFbptvvcGA5NtWYBQ56mgYwBubOKgwNmaUaBH+X4bpW7HsLVfepugmWFv0fwNAhQ/44A7ABySCs+6wUmYL8iyG+ObJXWScL8mZQFdDW8Nc/neekNAJCelFVr1ZRhVSvICvzEB/xm/x3pmSewEAAqaNOMSo3bP7Ss3ZKD1GD3KCtmdiYnviMWYWIfx0rnHDmqNI592LcYg8RLOQ8AKOjF68FKOKa7wSrIOqYwKmmN8QzUlocyEqOrFNyNTp29z0LZQXauuQ2A7YvcAuGhxiFegM2fpHiHNlagawoy1dEGWV6ooCJFkdRsorLKLd3fBlLHwwsjCEaniwYF2j2Xl35adn87STzdZNP0ePASMst+pRwMyzsXJ0E5ICqOdfF4eh++G5Amt+jWH5MMgKqCpc7Vgl2/K4pbkXSA1d/fgI1iWPT1nPn2YLAtTvCphOLMw69pjx+noEKmZy55mEZMStgDzsQNewplvJBC2L11bkkpbSpGqHj6RH3T2Wg88fLm+X0+GMnpoRQc0UNvps2Y6daDtB51zh4zRJUUMWRHSQm/RS2pbZXCH/yZFJ01Cs3hFwny3CdCizLU8+MrUPErXVW/x5f4kUOnino5/l23c+9TRxekiXvPnoBN9uqUf8eEU8ec0saATwWuWShmSkaECQ/gNJoHMdJeHtyBEoMjQG5qhoDM2x7NlcIqNbSS0pRUVxaqvorKwIVPeICHDQFWDyedsS13DkN/YTCVV6EK4EJoFM1GtpYFK/Wr8zxnbsKIOBtiounzGV2UYk2BdMSP8P5l0w7ZAdSJ23Knrzg4war7gzhFa8hN6HwZCnZIKF0YpDEurIkUhtKjpmSRHJNa4YNJLdktow7486FAcg9e7dZjjTd8OkAlr9LqEr/YEAFhorTTX513CAY+0DZSEzGCUF3d9/ON3WOxot43xrpWyRrLahv11tlplgll0QrNjgiD3JoG4A0QdNcv7yrehNCeqT6otqA7Z1NBwziwnmV2uDjj/SOZ7NhJ1tdlFQMaI9AHzDGxA3NmBkZ09SIwy1z1dmBoYcfUBmM3VzaKsb6WZzF1mV3gBIWOrkNo+nrXwVXGebfShC1SF2P3xvxXIWCzPrlAXCukV1t7KCRRtC+h5s4KfSJm3pMzSrGkBbAHOYCRY0wjpalddCx9KS4jGVsOQq5H1n4RsLEKvzQqm/bK9guFMXaL+xi7J/gq8huTWxH7OyMGSIESFSaCFrVlnC1I6e8+ubbnhhirR/SfAJHbO4QGyApI+4Gv+lajYrY6zy2cBDiIRT79bhcIaQ4sykHS3CnS5ZG6PbHPjwZDZsU2GkgMzwX+bpspRMp80Ivop3pudbOSrWMipm37qfp3GNtpy8gPYxTdgFd+WTTwxi/ha9vBA1nAyhnwNSzgFvQaLBxKfmZIfxQLM5P8Q0leFReFRJWQbEFMeFYuE/ok3ZH84/IheefNafcnlULrqYT6UbdcBIcmjUHsToO3wKl9zJG8tYnFII78C8By3j5/V6lY9F6GS8lkaL543QYOaeHUoISotbYL1FrXxg5fU2EbLunDvIeq+tJG4aHM4Wn1vav0tXO3IOepj8t7Uqw5322tgb9t9sGbuMS3mBcPiOBxbOCFzJ0ucD3XE5CqWNjfeRiBCg1wPnK3AhHrWGCjAZ/67FteE/6nrxdvPl10Y+QnQBSd5KU/w06G1r0EXxIWDmvnxDHH44xkzZkn2LhnLmcELNt5wZwKtm/FZX5/a1VTKkpGQ+mHX9FJ/SshtBspLXWOzCcSONB+TCOZWZiF3bd6E8Q2GIuXhGpe36Aw5wmlSUMCUBtZ3SwXOdcHigIX5FIyMMmqrRXxc44cFQPlPCAWD/N210TWDwU2m0NW0LPPGfof7t4EjGK82YFh05RF7S8vEDhza2gchMo5cr1TB49NMOgrXHFoSRjDxBJbf+Ee0Qc3CBxmEVWuNKk16U8f1uw2+LTww8npp6pIYSfJbDLF89kz9hhN+++vF+/OXr/49+PDz24vg1ds3F+f/+yL4G3U5Q0+jUbJbb6r+cSQQbtRHpqqwh6+Y7quXv7x7+fqvbx4+ZZVB4bTlXfB9p9OH5FG7wJjQcfh1GeHXgGO7kiC+VcIAL5l4hclDsrGLTxyOpsp9HHeguePA2qfT6WlXDgYyRUp+MHiOd6sRo7a4QKsfyOUtC/RQmeBAX70/G8va3IPm9sTzMtslO81oy34L8iZPDtihL/cDVkw+k3jHLu6KVRbve8O6SO+9N9BsqPPpxBkft6GeMifTFYrRYYUJ6zCzCgPBeBS1+MCPZGS1BywH9cG9rP86cFXbqMHYbUDDGILT7nhPN9HV9w4BW0gzNzooP6eBY4hi4BSn3X80tgjoh8OpvgPoa5CUEAO0PV5ZzU7wcO+I7hsPsUQ+BfYv98V43G2HJr8ri+6Ymnb1cIKF+CvQ73TUexTanYxy3xTdvhbVVBYzt32Tbe2Rm3DaBjwY+CcB/psB/asBfv/pfiirODXHhrbF6vapUzOF00k6jpx56My9ydyNbW9mRxGZ+SSyx+EcOiVp6M8nJPXCJPZdz02TseeQxPfTCD6bUzM50ymJXdeexfMosZOJP4lmswnx0snMddN55E7i1A1n9mwSecQjZJK6Thr5s8iHz3M/NaRm4qxEgoxB9yzy1Z5rp/2mpmbx4UazZgfUI/CqzBJhFamrkBzvfRMpyFJiRtwIU3tCBxUmkNitSIAl/ZA61eBs2xlfaFOYjKSV1aGcwsrQESBG1pvtXthl+zJuCiceHHJJBzaSCbXjIvvui1nISImltXlRhZDJeDIUsUiSYsYi0600XK0iULaDHWB5y4VHsn5RBUPW62gITbmtaGDDQgaS9J0GrpM8MdSqvw50P3dv7kpiNZJcsgbBME+Aq5D8CsMcn5TMpuP5NIzGfujNEn8eOZHnxtHMcd2Zn8YkSaPxZOqGSTKO/Yk9TkPXS7yYhM6MxNAoTM1klvoEdmVO7CiZ2bHtuuPZPEkTx/bG/sx3YmcaOs7Mnc6i8Wxsz70kndrudOrHyXQezmheNROZtcwxivZDUUAYwYX6w8PjQPDQUIV+H1of4MdfeNaeLjsL7EDLfYq5TZn67HMvfBH78lsRKcI08yjtUetGD3Z22ztqoZEmKcvFgBXCZYP5gcQYcFiuQTIa6I4MwOkOCOFfDci2LwlXZjsg1AlVg4s41FU6pdCVv4yY7yw1UlXQ+Z/R7QQ9YOU63IUBuYeuSOThbXbFbpZE8JKw02hg9B3X6wDjt8DFU2EojuBEPXXgwNHASTuQIKF4HnGLomaQGHFGftlc7aARj/+dN7dU78oi2cFigQECnluU61dWjZIWomv1g7Ul8XWexeGKRcJWVrXNVit2WSZ3F263eJBUGC8Yr3Yo9ljVTbapXnCP6YoiQrwKs3XFoL8pM3Qq5w1H2jpKkkKjazlWj86RlKYQOlhCrZcDlHnNYFsk4R4PzkGXDUYo8UUJZy0cYtQXmyNAkHFX/Lq/srhrHb3GWn0xV2M0VgeHEG0UF7GBnC3iM7KnihtlJTqiYA2iXYIsC5B/Cx9hK+pJKJNGV9irQNhpaY9HJykd3UrDIC3KKEsSklPrq2r0tO1DbrNt/O22ECBT5oumS+0PjH790oy7dVcDUQnd05B7C+rcaOmGKKzXuAvKZSS1ipthq4Jl7rc8C7pbfh3cKMQiAnII4FS2/e8CW3tyq5DGV3I9jxmsCFIurBsHWNHj0qTsDY3YYfgqyGBZ08Ph23OjEigqgMhZ4gUPKmzs1yNyYW0w7L3YVU1vHSYFDp0429BgT1H9mMTTea4cyF5nDu8yOwHA7oNcXpGwxJvPFFkdgH6JTLDLNnLswMMzAbccFGXy2fpx2fC456bABdO88JQJNgWGT1PcobNcGr+a9gSojdpy6aLQ3Xy/NHwzYx2b9rL+y1yrgmNoG66WYmlHs/YdpceTadHMK2rOoHWh9ao20kja3DM/lqgHEGU4Ep0PGcQSBJeWJlXhj77rtfjjo9iFToJtNnGMRRxkDyezhkNs4SSWcDI7eBQr6GADg1bg/sPI/ylJX5/L40n+ceR+nNQPk/ng0EHOPM079KLjhM/cH+pLKH2HOdnKFDZz552x+XxO8hi6gnEq06h263VY7i9ZEl9RXLsUmBux7Mq0zFCPO3JoC1Yu57O82qVpFlNKyUERonyIi8aKh4A0P2avEWlWPlGo1sEVAeDHlnp7MIm/mSBP/8rdAFQlUt4HCjZyCzNS5GjWrWQS6BxB1SiltN0SbJY6sIxLpSgK6MW1QtTi+tTTf7QhZSo0kP7g+dYePHNs2x5a43Zuub5tnKgzGIqhNIybTmSMQzMZdau5KsN10KjzT2sq86d24oWO7UQxFKfwf443i8ah7/iJm9pxOJ/705nrj2PXHaf2OCTJZBamY3uc+M6MzM2msnAWTSeuHY3nKbG92dRx7LGdTtOYkNiful44sz1nDF/tlEzi+XQ+9b2ZY7uTcOZOx36kmMrqbBnz2Xw86cp/Xx8N4o+hBVwuoxEjzHgrx3QtYcvy3ToChC5Sys0wgSLdD7Nl7gm6H5pci8KrHHhxFgeu63ELrtRRUPv/Ih7LBXUyk4CGQ9MagoE2APPHbmMh05cmdYfG4HtDa9/o3a02BGwHmkXElL4zS05dpMQMSUepEdxP0b1Ut70sVwpUNU2gysONCMAzbIb0ScnaKFdi/LY7dEBb54nbOZ+NbX1D9Oto/SyU1rK9poGiNK82jAAElhd3dKo1moE8Ik+LhxEnwmyhwuq0m/CnmsGJkPwDNwklN6pOjALq1xgE+pZNJnPX9zqR7dmzfjvtP8tugNno5JkuGBz1+yKURIzf4fjpoUMmindBtStT9GTZ5f/YhSvq748ZA0I7uM6wwj6g6VibUEksxUukK3r2dbWHU/yWlBWbKLV1VgerbMLttV6BmiHXwEqBVTLxA8B8He6qbV1uQgdeJIn7UgFJU0LtyQHND5Gt8PIsy8XqsMo1oCEy5zLYZDFebmM6wFW4qQJ5xrRmRkoUefdqSKrVE8n1VwBC6isMItQ/dgT75Bd1rVqd01kXCcgIsNAYbcABIxMsAHzhv8TdI8tfLoL7e3kRaOcQ6Li7GAQQ2jFbFru4BKkZForNaUUsPtS0LuM4gt8aeNNZ8BqrvTShpgqzmdeOp1iI2TpQcu9AKJCR1qiZdBRzdgGw25YFaCLrELh/XLGiumbdOiFVDDIrVOoNhIy4y0uy4U+4tNKbmTiEQnemCjoNdtRBeqwTlcKEEccoJ+G7yvNhYFEKChknvIjgD7kQM0LiAuQ8a5QYduuN9FPqg+mg8XWYXwma5PRW08zDwaOxwgNwatU8BDBTZYRc4+iMC3gP4jcI4/XvCxbVUP9+VeQ5C0mqP739UP/5soLxlMIL4OP1j/8k+6bqdltm0W7blL4Ly4r/4kCDFbzFUKdzlvLtYYurRaHB0PT8C90lnuNGA3G7uCsh9cez67ASRKz1opR0d6CJstgJBiSj5sOSkPdtlgFT6rrVhjo32INBe6kHBOmThjrY3jTsvbZLqhgu5/7mubgGQ10Wm87n7tQ1nuxPdO988J704OUm5xrU2MesibWr08EbvafVLL0ocefuOPJAbYMeYlAW7dQNx64zicjEcYg7CyeOb8/SJJm6JHXcKfFBwfNiZzodxx3P0JE0Ao0UepokM9+bkNglgBuTcEzGESiU8dyOJ77tpXbiRHaYhD7xoBi2y7OT2cRxDU4YfE9+oyYW/qOo1ESNY1NgAk2eLKItaq/6to8QTQMp8mrQHzxBgyEzMi/hW1qGd4gC1Yjkt1lZi7KHgwAa5KAJKcK7Rcs00RgypZwlNHRgVQCx9qFR00tfC7pqMoWwGD6grzptCY/fkmKIqEtI80F6aku5audZIDEWkIvVUo83g9pFC4HHuxBVLm8+mbPkyvFNHdFN7GK8+Sk2MTM4/0I37alJbVimZ3re19PFZBwSfC6N1T9Zf7Yc+Qqc3SzLx5wpvkcTHeqNBx6xOyH/DqoL1OgctCNDRNetcMXTwhR5IKJsyj8YfNgOOmRrkDCMfRC+hRrNGQIFEZH0m4i6hxuKlBqKPRpPlA8yEjx6R7mwVu8op1q2Apa4XWJO01lXKIL0cEY3m5IyjqpOe553yLsKNkq8SLOjQ1x+wn0UQ9KDU0dAXjtgbRtveHpDpb3u6fluy9bXGvPhA7QM+XzCnTnADpzfyj2fOJmNTiJdG087r70ZmM+E6isiX52cQNKsizZB05sXY/CxgaI7SZlHGzMtJoj2IsMxR5+2YBfUL9qgyPaPXbENMbab3/0EtaKOcWhqWvXDkcpthkHvkizUsgeC/Ns1Op+ygRbNfWv79GLHVlNDPSawCseiS63sk3GoftNTp5e0xHD0AZqSr+jewK861iGdU0cAxy8z6yBX6RozZRWRu+JOPZBKaE4LTh76acfvhzf0Cc0z7JyGQVJ0qONs6V3OAj2RxFs3aoAL70DUbryT23zJd8ZKkNzRpm3WqfLZ+bhxjjZfgz6cjT8xb6OvSMl+wPjhVAe5Y4ce60EYTFhmUmV3Jdw5NJpBUm1I/lRENRuC/0D2wIdrL6RN0Yce5uITupQbfPpK1vLN2Ut3xoYuHGqOTtGdEuauZNOb+vaRiM1vLdI4s8l0brpQ+xZyjbiuZzfNdSCH5GOgRGlzc4DysrG5C9UNgIesdNzuDwS3hQnREx9DA6wfRQJF5htLjZb0I0NaqHWT4XvG+G2gOYbO5yq3/LqOqU0EuGb+1IFePvzjeLbvReP51LUj34+82I3mtuPDh8jxw3jskmgWuWFKktnMT8M4ImQWz9wo9KiZwmD8cGdxktjpLExi4jnjCfQ1Tl3iRuPUcfyJ74fRbBwm8zR2xxOS+t40cSYwEWfuuiRxwo4IlCcyWCklxy1XSvFC9l+nEalBK9Yb4MFDGfrJkFYzZPssbkQYoNL/qI6BaEW4jLpTgAoz4MAoNChD1rMUlBRuMAWDyoTGNiCFgr6n9mH0i+qCoIgvQUB+13aPolILy1XNgsD4D7yfYWE8izox/H2rVzNYTx8CPWjQJQaTxXDOAuNBlT6vw7bBVE0cMl3JfFWEE9sKvZv3+/5e3xzvUPaHbitcK3PI4GESF8AQfZyy30mAtQM+DEblSWJYdyU5HBH5Nkwv3MK/YXlVyU+pCmPFgTxmLGWPCM9lv4YWT95j7FxN7NPqvgmNbEJr+ZdW+o2x47nzrpTf31svpUwez+l1M31zYb2rtha9yKLvskkRjuhMZt1dw/9gsjnMMley1X5vFRv2qptU/bno3SoxsdyOpvCDuY2sVz+/xhTKO1JZOSa3Y/nzYiI6w96BWxcsAkhEDlM1VKSPskIegP1CyHRWQaeVlfg+JA/rkTKVROzuTg7XpFxNdlfjMgKm96lTRDVB2DySKAuUHWoiQaFU38s6TtuY0kkRyk3NFazR2ujSmDPz3a6tpuLCAVjIWUekDBvsfVhm0qxbJ0HjQ9f00JetlibEbPW/Kq7g8CEr+oYbCBv8xqn5rCQgEfFd9BKGVW2F4n7PsiXSZy7om2NwOCGmZQk6NW331i7ndsARzewIKJ4CT7audmGZWBFZyVFW39OHSEL2OCF71HN7Dfj701+sVVHc7DZDOlCYgFxDHythaEK3cYUnO42KH2kPg4l8nRIPORArT9Hk2TMDyKRVB0WZAlDou84o0tcKrGaslbOOLQ1ZdbS0ZDofk7IdtNtK+cuAQcWrIu+qKcrUAVq47Hue88cEoibincpThCzh7K60aaUTkgtHmIuBJ5Lo68kWtKr0SZe+BosJiMkzu/uuLMvZu7vkc1ZhLEEAGJ6sSIme3M9/RCuhJu9PPGfaeFBhL9iJUvubAlyXeIIOO6+50miXY/7KYJcF/NEMJGyRaVxbpz8f/zFI9MetaTyZOv/T1jSR7/3/h6zJdcf+163JmHJAfeaMPq4k1et4glGd2dRzJ13s5DGHfX3GK08Qvydh0hy82+uy2F1d8xTFjFGxkxSdBqk9MsytcEWDYKQzdZfXLxFf4OGbFKQSj0I0b1axHM7NCVzRznbbYh1us9gS2Uwsyl/5ifwyoS6F+EBvncKOPV+BhzoImThz9n4xY97sPBfvb8GQ9Pks05vBdG8PZcCpd/jXd+/evr84/yl4/+ub4OWHD6//+uaX8zcXwcV/vTv/wDrbZZgEuN4DQD0uteDdE7vKLO4M9i2ELL5PlQcGOQPgXc9K2kuuDXLJma0agzXEu7abazy7WUFtoBRvMHMZvF1flnz1VqklPZt7io9DXTlrp49lL/g2CVa4NY05DVjsdp6/NMrkL3ZPdqgXLVuP1N3hvaPNm5TCQHp9g++DuiJZhqKJD+lvebkIE3P9I123lA86gPjSPUS7nXEgZaZymtpBbXhXh9JS17av9XfZ6NfX7xiif9gWaGGksnPNiuSJKS710uW+4mShYD/mkJDeUlYS/DRv0yrND3hoaILzAgF4yeD5adgxhzrtNK3aKvikJQUWFaVPateNWL2Q9k8Wtk1phevx9QJt3tKWLvhuX6qfP7UNF4t61y/1IrV7evrdZLk2eekznX2dlIql/VWqqkUDtXs5BZPCILoyMDWVmgRMapfFrowJe9G6zV4rxl8af2dlg2XVjuVKaaUg5/49LbcUZzLD6IdDmWXw1KMhqLA4OX1dnea1nSWOGb/44Tm0AnpySPX0KXi2oxhcn2TcLoMs12XNfAOZFf9kvCc9xkXgMJU2RnRuTOvcmop6WIkzhj4OJbGHwWPn1RidmATVfQsHG+LbzuF8J+rLAaajkXoCCKSku8jeK2coyh4v/0aLqbPMkNYz2u1XDmTpwey2gi3M6eqfeL7fWy9BwgT5Ml0RsqVCKxUoLRr/XaEgCcPxPvCRWoA0mlPhsNpS25Tc1SbcI90N2cuAxeb5itySVY1Jkh0ScyWtdmt8G+QVM1Cx50GU3lDSybeSCbV5U+QHGCIn9PESZiRDQ2qWpgSD0ZuDWgJxswK8v1VdtPK98iwFzTAvhw6qmfCPpblvveXWjD2Cyuh9zUeT7aZCW2DvTnPEoZsCrBt3KCA5u+zlUNYpu+6h+4UZgyuEaCW7VPJPxwXaTl8ArYv2y/BCSFPIxFit+/L/yWjAvEdiDQb3srruV9OmwJsudo2oKSavvH5A7x0oYtbqKVJmPbMW0DgOD3lLRGUdl+sfIEr0je83iJ+DweDbLp1bMSVSMKRLb+VTOYz6GuZjAxnrAdKtNyYaKGugM2N+y0FYOkOH1gFwd3d3YA/YJunPcGgHvGYOPzCUfmHHf/NhlGc72F8MCdRTfDbzZronyxNjXntjW7xb7JxqBVZnOp+NXX2mDzq8O5yFahHU/LZU13PPh9MwD+RsYPwxdZE2GV8yOCKX9vVtmvuue1jYogEX+lAKhLUvyhlpeMZIPzW1u2f5rSL8XeeWZj+bDNDGR+KuSE6Yq+CBp2dYH9xxWP5EPm8yDK+lKtKjGZuY4qGTZkXvBvmutXSUfkMGBgmx3wDcaAv5blkTidEfF3e0Gf/xW/m02zkwe11rvFqbt3HjMJ9Lae6tfpkViSKrCupBspV6vTT1+GnE7/NhvP/D5v0n217YNg3k+fOhB1+/CRIpJOzbY2feZQA/wUVqaD0DFoUP5QkM5HVpshEx/we6BX/TV9K5SGCMdMSDXzv0ufkM11pXFY4J7OivXbp4ElUaBc1u4xcnvr/e/YC24lE0VJLL1i9pB69+ff8era1ofH3906B+5qp2SuvILiatDdsoD1cfe2iaJxtqpc3rN7HUQ0sNcB6Yc587zb6MWqBUnk3Ge/27MNsGNI1TEHNLJTU08I2k2jptRilqXQAHQK1PbLSUBUrz56qd41gPIuWr4kGXtB6nNqF+jSdN24WskP4kLrprHIdu7sJ9VafhtaJiey2aW/xpavoC5jm/L5L7S3erVRDvobr16ue3H86Dl+/efXjx729/Obcich3eYoJfmDfw1WKHtsHRIayktMevOzihAQqtVoZnxZnpZGgF8H946YLVkC/0wjXy/pg8p1mpevgul5wUuSeLiK0n6sVj6NxVuMPB0OC33WuxKzoJlo0L1PzfSVn0Hvw4mbL/hvctzQ+wGGYgkicceWilvSPJCC0YVb93XazJH70AHPOUqavTllibxIj6zVSWzZ9D83TX1ZJF4/d1Qn7OKZSlRhsMkX0t6VnUwQ81Z0n5rfcIo0zw+TOKqeImEv5+Jb2Qm9BSlMQxPwhA4optqexTzB7lXcptR8KPsm80nXIuy3vv8686j9THFPWGm2s44ZetDesN6aU8dWkLy/3SdDiZsvQyh2D5vWCeg4jv5GWvPppogUK2J51kDzzEjCcw09IecDAdJxEz+tez32AelWpLUD4+kYKbYw3/a3IpYmz7bb7aN05PzVkA0kXFvOvgE6kz8lPHvBCARppTwMLN3u5Fj8LZiaeHL+4AatcAJ5JfkRd4H1Fm1Or5orb11EluRg8U1AyJ99WnnlSGIJzuD3poKVt2MP2uUYY7khD3WKZePkWbvbTNSuk7C0qiy95B9FoYQ3YNUqXkj3IYEXc5g0/SpHcBBPwcB+g6uZSCB9o7kCICr/YPAFsde/cYAffU2Iqa0H86/9vrV+eUfZtyfpt0itbceiYnpN6QTU69AGcSfOsS0XcVfysM2BHeIy/SXc5eHn8hFvScG89f0PSzo231hDE9bpjOJnHkzdP5dJpMAOGmTmpH0SSej6N0ntiuk07C6dgPY9f1J7E9m0f2PArDiTcNCXE6Ynom0ZxM7MS3/TR0o6k7jqfTOPGnM9sOfc8bJ/MEOkznthtiGJHtub7jES8laTwL5+OOmB4etPaKx8UtqPc4bNIPPEfEbnv9a0XK1ywfCbKqf1o5yKk/aBFr/sx8Wwe7WeGVDev+F4ZXIQr/tXPPfwAlg95LGduLW+dFuYlftCPT6eO0SmT6mmyviwQvot+9/XCBBVGR7BfWf3x4+2bEJpul+/4XSw5cr90cXoOAuwmkMMtFPcv3NDX2iJkneRjJ/YBCBO0u3ymr+ec/abaPIlUWOdIDcEH+xQhSDl20z3xR3TosUC7wUSZ8Hpdyj4WcLqJ53EnKLsdy1qJ0vbCm9sS6p1OkVqIXL6y3QttARRCJEIPeRWQC3mlH8OG6jlpkHlYsQCKvL8twvYfX9S//YtHX36rrbDNas1AiXGkSPXiRaFWlpEQPtRXhngUi1LdZrCsvVnmadDbzDue4aBD94LKGbfTHv0fNh6EWFDSdzw4QwJpgliuEEaA/spgiH2kzsv71BLroTsVS39Q+CZHwKR6gFVZBfHvdWKc3FFlpXnWlqwZy1saQf31Rp87l5Cd+NhRoLQTr4YhphiNgZL+B97+OQGFClNxixheg1qZoBBR1lQnSfPXzazzNfn6NYBKLUetLEKCNWkDQ6huZAM5j8EDKUPMpyo/NUxvZ6aA0EM3YmXYa/+u+39BuP1B86Xfir7R0acY4MMAFXUlhB/lttaGCKJEbDEYw5Lqvvas7HgMHkC9rzPN8/FBtAmb+qAYC7QSGRrn0+ticcYVSrLXQW4j7fS2MmxO3DI6J7XkNOI5M+GGjqPhg5B2dEPgisYSv4y0nMJVmrAcQBHYsMq+ytZU0bJ0g0rA//lJIDxhsAhScVyiYSbMQ39hE6UNccFLXxeKD8ICDnQbAv2MoqGP2ZDxRriGNENeaN3tEPjPVrtrnsSWkXq5Ovud3f3CgLiz+Y7DAl8vg4CB/BshsAHHIjzpy+ZMmmfTXd6wI5sKt+4nzCdpTx0kidzaJyDSKw8SNYzuZEuL7bpLYEb5w6hF3PnPTyPHc1JtNPRDOI+LbkTftyFTvTIjv+bOpnU5AxB6PI8+OUy9NJlN/Ok5tD+T2yAnjCOaFAfWRH6c+vv7oJ7YfJvPo24bUnxIB8j2ot9gtSmsJOlM9xxQNFnMfZ2ZpC/2uuJ5qRXsWc4j6JL35Demmj+Qe3/AwV8pCLMwKu6I2kPrAo+momjgJUKBX5CqM99Y1WYF8OmpZW9AVS3l1cOa7c1mtu/+/ylQEhA=='


# Exact inverse of the scoped foreground/native-reason projection hunks.
# Prior A0/product/profile records remain byte-identical and receive their exact bytes.
_CONSOLIDATED_PROJECTION_EXACT_INVERSE = "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"


def restore_consolidated_projection_delta(name, source):
    import base64, zlib
    record = json.loads(zlib.decompress(base64.b64decode(_CONSOLIDATED_PROJECTION_EXACT_INVERSE))).get(name)
    if record is None:
        return source
    if hashlib.sha256(source.encode()).hexdigest() != record["candidate_sha256"]:
        raise AssertionError("consolidated_projection_source_changed:" + name)
    for hunk in reversed(record["hunks"]):
        offset, new = hunk["offset"], hunk["new"]
        if source[offset:offset + len(new)] != new:
            raise AssertionError("consolidated_projection_hunk_changed:" + name)
        source = source[:offset] + hunk["old"] + source[offset + len(new):]
    if hashlib.sha256(source.encode()).hexdigest() != record["base_sha256"]:
        raise AssertionError("consolidated_projection_inverse_mismatch:" + name)
    return source


def restore_exact_product_delta(name, source):
    source = restore_ordinary_recorder_delta(name, source)
    source = restore_consolidated_projection_delta(name, source)
    import base64, zlib
    records = json.loads(zlib.decompress(base64.b64decode(_PRODUCT_EXACT_INVERSE)))
    if name not in records:
        return source
    record = records[name]
    if hashlib.sha256(source.encode()).hexdigest() != record['candidate_sha256']:
        raise AssertionError('product_source_changed:' + name)
    for hunk in reversed(record['hunks']):
        offset, new = hunk['offset'], hunk['new']
        if source[offset:offset + len(new)] != new:
            raise AssertionError('product_hunk_changed:' + name)
        source = source[:offset] + hunk['old'] + source[offset + len(new):]
    if hashlib.sha256(source.encode()).hexdigest() != record['base_sha256']:
        raise AssertionError('product_inverse_mismatch:' + name)
    return source


class ProductExactInverseTests(unittest.TestCase):
    def test_restores_entire_certified_base_and_rejects_mutations(self):
        import base64,zlib
        records=json.loads(zlib.decompress(base64.b64decode(_PRODUCT_EXACT_INVERSE)))
        for name,record in records.items():
            source=(ROOT/name).read_text()
            expected=subprocess.check_output(['git','-C',str(ROOT),'show',record['base_commit']+':'+name],text=True)
            with self.subTest(name=name):
                self.assertEqual(restore_exact_product_delta(name,source),expected)
                self.assertEqual(hashlib.sha256(expected.encode()).hexdigest(),record['base_sha256'])
            hunk=next(q for q in record['hunks'] if q['new'])
            at=hunk['offset'];new=hunk['new']
            variants=[source+'\n',source[:at]+source[at+len(new):],
                      source[:at]+new+new+source[at+len(new):],
                      source[:at]+source[at+len(new):]+new,
                      source[:at]+'# unapproved\n'+source[at:]]
            for bad in variants:
                with self.subTest(name=name,mutation=hashlib.sha256(bad.encode()).hexdigest()), self.assertRaises(AssertionError):
                    restore_exact_product_delta(name,bad)


def restore_exact_ui_profile_delta(name, source):
    import base64, zlib
    record = json.loads(zlib.decompress(base64.b64decode(_PROFILE_EXACT_INVERSE)))[name]
    if hashlib.sha256(source.encode()).hexdigest() != record['candidate_sha256']:
        raise AssertionError('ui_profile_source_changed:' + name)
    for hunk in reversed(record['hunks']):
        offset, new = hunk['offset'], hunk['new']
        if source[offset:offset + len(new)] != new:
            raise AssertionError('ui_profile_hunk_changed:' + name)
        source = source[:offset] + hunk['old'] + source[offset + len(new):]
    if hashlib.sha256(source.encode()).hexdigest() != record['base_sha256']:
        raise AssertionError('ui_profile_inverse_mismatch:' + name)
    return source
# END SIGNED PROFILE DELTA


def restore_exact_outreach_routing_delta(source):
    source = restore_exact_ui_profile_delta("runner.py", source)
    if hashlib.sha256(source.encode()).hexdigest() != _OUTREACH_ROUTING_CURRENT_SHA256:
        raise AssertionError('outreach_runner_bytes_changed')
    line, offset = _OUTREACH_ROUTING_LINE, _OUTREACH_ROUTING_OFFSET
    if source.count(line) != 1 or source[offset:offset + len(line)] != line:
        raise AssertionError('outreach_runner_hunk_changed')
    restored = source[:offset] + source[offset + len(line):]
    if hashlib.sha256(restored.encode()).hexdigest() != _OUTREACH_ROUTING_BASE_SHA256:
        raise AssertionError('outreach_runner_inverse_mismatch')
    return restored


class OutreachRoutingExactInverseTests(unittest.TestCase):
    def test_exact_baseline_and_reject_missing_altered_duplicate_moved_unrelated(self):
        source = restore_exact_product_delta('runner.py', (ROOT/'runner.py').read_text())
        baseline = subprocess.check_output(['git', '-C', str(ROOT), 'show',
            '38a2c01e6b10778fe6099f05736bdc7b062b6e7b:runner.py']).decode()
        self.assertEqual(restore_exact_outreach_routing_delta(source), baseline)
        line = _OUTREACH_ROUTING_LINE
        variants = {
            'missing': source.replace(line, '', 1),
            'altered': source.replace(line, line.replace('not outreach_session_run', 'outreach_session_run'), 1),
            'duplicate': source + line,
            'moved': source.replace(line, '', 1) + line,
            'unrelated': source + '\n# unauthorized\n',
        }
        for name, changed in variants.items():
            with self.subTest(name=name), self.assertRaises(AssertionError):
                restore_exact_dm_certification_delta(changed)


def restore_exact_dm_certification_delta(source):
    source = restore_exact_outreach_routing_delta(source)
    import base64, zlib
    record = json.loads(zlib.decompress(base64.b64decode(_DM_CERTIFICATION_RUNNER_DELTA)))
    if hashlib.sha256(source.encode()).hexdigest() != record['candidate_sha256']:
        raise AssertionError('dm_certification_runner_bytes_changed')
    for hunk in reversed(record['hunks']):
        if source.count(hunk['new']) != 1 or source[hunk['offset']:hunk['offset']+len(hunk['new'])] != hunk['new']:
            raise AssertionError('dm_certification_runner_hunk_changed')
        source=source[:hunk['offset']]+hunk['old']+source[hunk['offset']+len(hunk['new']):]
    if hashlib.sha256(source.encode()).hexdigest() != record['baseline_sha256']:
        raise AssertionError('dm_certification_runner_baseline_mismatch')
    return source


def restore_exact_self_xml_delta(name, source):
    if name == "account_session_orchestrator.py":
        source = restore_exact_ui_profile_delta(name, source)
    if name == 'runner.py':
        source = restore_exact_dm_certification_delta(source)
    for approved, original in SELF_XML_EXACT_HUNKS[name]:
        if source.count(approved) != 1:
            raise AssertionError('self_xml_hunk_missing_altered_or_duplicated:' + name)
        source = source.replace(approved, original, 1)
    if hashlib.sha256(source.encode()).hexdigest() != SELF_XML_BASE_HASHES[name]:
        raise AssertionError('self_xml_unapproved_delta_or_position:' + name)
    return source


# Exact signed Like Verify delta. Whole-source hashes pin hunk locations as well
# as bytes; no function/range exclusion and no generic whitespace normalization.
LIKE_BUDGET_SOURCE_SHA256 = 'fe3fba2c7b5051a4e5f95adfd98a316ac294de3b87d2b26e6eaa84b0617e2d30'
LIKE_BUDGET_BASE_SHA256 = 'db4a882e77feef02ff8db074e869bfbb79a37680e1428c2567704b3c33342c37'
LIKE_BUDGET_EXACT_HUNKS = [('\ndef _ui_post_viewer_action_button_liked_strict(\n    d: u2.Device,\n    *,\n    semantic_deadline: float | None = None,\n) -> tuple[bool, str, float, dict[str, Any]]:\n    """Trusted already-liked: exact Unlike (or localized) on the action heart button."""\n    checks: list[tuple[str, Callable[[], object], float]] = [\n', '\ndef _ui_post_viewer_action_button_liked_strict(\n    d: u2.Device,\n) -> tuple[bool, str, float, dict[str, Any]]:\n    """Trusted already-liked: exact Unlike (or localized) on the action heart button."""\n    checks: list[tuple[str, Callable[[], object], float]] = [\n'), ('        ("ui_text_unlike_exact", lambda: d(textMatches="(?i)^Unlike$"), 0.9),\n    ]\n    for method, pred, conf in checks:\n        if semantic_deadline is not None and time.perf_counter() >= semantic_deadline:\n            return False, "", 0.0, {}\n        try:\n            el = pred()\n            if not el.exists(timeout=0.14):\n', '        ("ui_text_unlike_exact", lambda: d(textMatches="(?i)^Unlike$"), 0.9),\n    ]\n    for method, pred, conf in checks:\n        try:\n            el = pred()\n            if not el.exists(timeout=0.14):\n'), ('\ndef _ui_post_viewer_liked_state_for_verify(\n    d: u2.Device,\n    *,\n    semantic_deadline: float | None = None,\n) -> tuple[bool, str, float, dict[str, Any]]:\n    """\n    Post-tap verification only: accept Unlike / Double tap to unlike on the real heart control.\n    Does not use broad engagement or \'Liked by\' (filtered in _verify_post_tap_text_is_unlike_semantic).\n    """\n    ok, method, conf, proof = _ui_post_viewer_action_button_liked_strict(\n        d, semantic_deadline=semantic_deadline\n    )\n    if ok:\n        return True, method, conf, proof\n\n', '\ndef _ui_post_viewer_liked_state_for_verify(\n    d: u2.Device,\n) -> tuple[bool, str, float, dict[str, Any]]:\n    """\n    Post-tap verification only: accept Unlike / Double tap to unlike on the real heart control.\n    Does not use broad engagement or \'Liked by\' (filtered in _verify_post_tap_text_is_unlike_semantic).\n    """\n    ok, method, conf, proof = _ui_post_viewer_action_button_liked_strict(d)\n    if ok:\n        return True, method, conf, proof\n\n'), ('        ),\n    ]\n    for method_label, pred, conf in probes:\n        if semantic_deadline is not None and time.perf_counter() >= semantic_deadline:\n            return False, "", 0.0, {}\n        try:\n            el = pred()\n            if not el.exists(timeout=0.16):\n', '        ),\n    ]\n    for method_label, pred, conf in probes:\n        try:\n            el = pred()\n            if not el.exists(timeout=0.16):\n'), ('                return True, "ui_action_button_unlike", conf, proof\n        except Exception:\n            continue\n\n    if semantic_deadline is not None and time.perf_counter() >= semantic_deadline:\n        return False, "", 0.0, {}\n\n    try:\n        el = d(resourceId="com.instagram.android:id/row_feed_button_like")\n', '                return True, "ui_action_button_unlike", conf, proof\n        except Exception:\n            continue\n\n    try:\n        el = d(resourceId="com.instagram.android:id/row_feed_button_like")\n'), ('                fail_snap["semantic_result"] = continuity_reason\n                break\n            ok_ui, method_ui, conf_ui, proof_ui = (\n                _ui_post_viewer_liked_state_for_verify(d, semantic_deadline=semantic_deadline)\n            )\n            if proof_ui:\n                fail_snap["last_ui_candidate_method"] = str(\n', '                fail_snap["semantic_result"] = continuity_reason\n                break\n            ok_ui, method_ui, conf_ui, proof_ui = (\n                _ui_post_viewer_liked_state_for_verify(d)\n            )\n            if proof_ui:\n                fail_snap["last_ui_candidate_method"] = str(\n')]


# Exact inverse of the approved DM446 delta; test-only, never a runtime bypass.
DM446_SOURCE_SHA256 = '850d6a101bcc80f80f167b2a818d4f0ca99857482c442f02aed37af202d817e9'
DM446_BASE_SHA256 = 'fe3fba2c7b5051a4e5f95adfd98a316ac294de3b87d2b26e6eaa84b0617e2d30'
DM446_EXACT_HUNKS = [(253360, '    # Instagram 446 invite threads render the newly sent text in an\n    # unclassified TextView and disable the composer after the invite.  Keep\n    # this branch narrowly bound to the complete observed invite surface;\n    # generic TextViews, composer text, or either invite label alone remain\n    # insufficient evidence.\n    package = str(\n        getattr(config, "INSTAGRAM_PACKAGE", "com.instagram.android")\n        or "com.instagram.android"\n    )\n    invite_title = False\n    invite_explanation = False\n    composer_present = False\n    send_enabled = False\n    message_list = None\n    for node in root.iter():\n        rid = str(node.attrib.get("resource-id") or "")\n        if rid == f"{package}:id/thread_disabled_title":\n            invite_title = node.attrib.get("text", "").strip() == "Invite sent"\n        elif rid == f"{package}:id/thread_disabled_bottom_description":\n            invite_explanation = (\n                node.attrib.get("text", "").strip()\n                == "You can send more messages after your invite is accepted."\n            )\n        elif rid == f"{package}:id/row_thread_composer_edittext":\n            composer_present = True\n        elif rid == f"{package}:id/row_thread_composer_send_button_container":\n            send_enabled = node.attrib.get("enabled", "").lower() == "true"\n        elif rid == f"{package}:id/message_list":\n            message_list = node\n    message_nodes = set(message_list.iter()) if message_list is not None else set()\n    if invite_title and invite_explanation and not composer_present and not send_enabled:\n        for node in root.iter():\n            rid = str(node.attrib.get("resource-id") or "")\n            if (\n                node.attrib.get("package") == package\n                and node.attrib.get("class") == "android.widget.TextView"\n                and not rid\n                and expected in _normalize_dm_message_text(node.attrib.get("text", ""))\n            ):\n                if message_list is not None and node not in message_nodes:\n                    continue\n                return "invite_postsend_exact"\n', ''), (290822, 'def _dm_446_context(d):\n    """No I/O. None is historical A0; invalid retained authority never falls back."""\n    from run_latency_observability_v1 import _TimedProxy\n    from instagram_ui_446_compat import context_matches\n    owner = d\n    if isinstance(d, _TimedProxy):\n        if type(d) is not _TimedProxy or vars(d).get("_kind") != "device":\n            raise ValueError("dm_version_context_unproved")\n        owner = vars(d).get("_raw")\n        if owner is None or isinstance(owner, _TimedProxy):\n            raise ValueError("dm_version_context_unproved")\n    values = vars(owner)\n    observed = values.get("_instagram_search_observed_version")\n    key = "_instagram_446_observed_context"\n    if key not in values and observed is None:\n        return None  # Unchanged unversioned A0 call sites; no added device read.\n    serial = get_device_serial(owner)\n    if (type(observed) is not tuple or len(observed) != 5\n            or observed[:2] != (id(owner), serial) or not serial\n            or type(observed[4]) is not int):\n        raise ValueError("dm_version_context_unproved")\n    if observed[3:] == ("372.0.0.48.60", 377611059) and key not in values:\n        return None\n    context = values.get(key)\n    if not context_matches(context, owner, serial, observed[2]):\n        raise ValueError("dm_version_context_unproved")\n    if observed[3:] != ("446.0.0.49.77", 385211304):\n        raise ValueError("dm_version_context_unproved")\n    return context\n\n\ndef _dm_446_observation(d, package, *, _record=None):\n    """One XML read bracketed by exact foreground continuity; no action."""\n    from dm_ui_446_evidence import composer_evidence\n    stage = "version_context_invalid"\n    try:\n        context = _dm_446_context(d)\n        if context is None or context.package != package:\n            raise ValueError("dm_version_context_unproved")\n        stage = "foreground_before_invalid"\n        before = d.app_current() or {}\n        if _record is not None:\n            _record["foreground_before"] = {k: before.get(k) for k in ("package", "activity")}\n        activity = before.get("activity")\n        if before.get("package") != package or activity not in (\n            "com.instagram.modal.ModalActivity",\n            "com.instagram.mainactivity.InstagramMainActivity",\n            "com.instagram.android.activity.MainTabActivity", ".activity.MainTabActivity",\n        ):\n            raise ValueError("dm_foreground_unproved")\n        stage = "viewport_read_failed"\n        display = d.window_size()\n        stage = "dump_failed"\n        xml = d.dump_hierarchy()\n        if _record is not None:\n            _record["xml"] = xml\n            _record["dump_returned"] = True\n        stage = "foreground_after_changed"\n        after = d.app_current() or {}\n        if _record is not None:\n            _record["foreground_after"] = {k: after.get(k) for k in ("package", "activity")}\n        if (after.get("package"), after.get("activity")) != (package, activity):\n            raise ValueError("dm_foreground_changed")\n        stage = "version_context_invalid"\n        if _dm_446_context(d) != context:\n            raise ValueError("dm_version_context_changed")\n        stage = "composer_unproved"\n        composer = composer_evidence(xml, package, display_size=display,\n                                     surface_context=(*context.version, *display))\n        if _record is not None:\n            _record["observation_status"] = "QUALIFIED" if composer["visible"] else "OBSERVATION_REJECTED"\n            _record["observation_reason"] = "observed" if composer["visible"] else stage\n            _record["composer"] = dict(visible=composer["visible"], reason=composer["reason"])\n        return xml, composer\n    except Exception as exc:\n        if _record is not None:\n            _record.update(observation_status="OBSERVATION_REJECTED", observation_reason=stage,\n                           exception_class=type(exc).__name__)\n        raise\n\n\ndef _dm_446_composer(d, package):\n    """Exact current selector, never a generic EditText fallback or a tap."""\n    from dm_446_observation_retention import begin_composer\n    record = begin_composer(package)\n    result = None\n    try:\n        _xml, evidence = _dm_446_observation(d, package, **({"_record": record.observation()} if record is not None else {}))\n        if record is not None: record.first = evidence\n        if not evidence["visible"]:\n            result = False, evidence["reason"], None, None\n            return result\n        rid = package + ":id/row_thread_composer_edittext"\n        selector = d(resourceId=rid)\n        try:\n            selector_count = selector.count\n            if record is not None:\n                record.selector_count = selector_count\n        except Exception as exc:\n            if record is not None:\n                record.record_exception("selector_count", exc)\n            raise\n        if selector_count != 1:\n            result = False, "dm_composer_not_unique", None, None\n            return result\n        try:\n            info = selector.info or {}\n        except Exception as exc:\n            if record is not None:\n                record.record_exception("selector_info", exc)\n            raise\n        if record is not None:\n            record.capture_live(info, rid, evidence["bounds"])\n            record.compare_live(info, rid, evidence["bounds"])\n        if (info.get("resourceName") != rid\n                or info.get("className") != "android.widget.EditText"\n                or info.get("enabled") is not True\n                or info.get("clickable") is not True\n                or info.get("focusable") is not True\n                or info.get("bounds") != evidence["bounds"]):\n            result = False, "dm_composer_live_mismatch", None, None\n            return result\n        # Live resolution is not atomic Android compare-and-act. Re-read the\n        # context, including ancestry/occlusion, before returning any control.\n        _current_xml, current = _dm_446_observation(d, package, **({"_record": record.observation()} if record is not None else {}))\n        if record is not None: record.second = current\n        if current != evidence:\n            result = False, "dm_composer_context_changed", None, None\n            return result\n        result = (\n            True,\n            "resource_id_exact_composer_pkg+contextual_composer:" + evidence["context"],\n            evidence["bounds"],\n            selector,\n        )\n        return result\n    except Exception:\n        result = False, "dm_composer_observation_unproved", None, None\n        return result\n    finally:\n        if record is not None: record.complete(result)\n\n\ndef _dm_446_thread_state(d, username, package, composer_timeout_s):\n    """A0 composer phase then history probe, using the qualified 446 UI reader."""\n    from dm_thread_truth import classify_dm_history\n    snap = dict(thread_state="unknown", composer_visible=False, composer_signal="",\n                has_history=False, has_history_signal=False, history_signal_type="",\n                empty_signal_type="", draft_only_fast=False, package=package,\n                username=username, positive_empty_observations=0, number_of_reads=0)\n    from dm_446_observation_retention import begin\n    retention = begin(package, username)\n    started = time.monotonic()\n\n    def observe():\n        try:\n            observation = retention.observation() if retention is not None else None\n            xml, composer = _dm_446_observation(d, package, _record=observation)\n            snap["number_of_reads"] += 1\n            state, proof = classify_dm_history(xml, package, username,\n                                               surface_context=composer.get("surface_context"))\n            if observation is not None:\n                observation["classifier"] = dict(state=state, reason=proof.get("reason"))\n            snap.update(proof, composer_visible=composer["visible"],\n                        composer_signal=composer["reason"])\n            if state == "existing_thread":\n                snap.update(thread_state=state, has_history=True, has_history_signal=True,\n                            draft_only_fast=False)\n                return "existing"\n            elif state != "empty_new_thread" or not composer["visible"]:\n                snap["thread_state"] = "unknown"\n                return "unknown"\n            elif not username or not str(username).strip():\n                snap.update(thread_state="unknown", reason="dm_recipient_unproved")\n                return "unknown"\n            else:\n                snap["positive_empty_observations"] += 1\n                return "empty"\n        except Exception:\n            snap.update(thread_state="unknown", reason="dm_observation_unproved")\n            return "unknown"\n\n    try:\n        timeout = float(getattr(config, "DM_THREAD_DETECT_MAX_S", 2.5)) if composer_timeout_s is None else float(composer_timeout_s)\n        if timeout <= 0:\n            snap["reason"] = "dm_composer_budget_exhausted"\n            return snap["thread_state"], snap\n        first_state = observe()\n        empty = first_state == "empty"\n        if first_state == "existing":\n            return snap["thread_state"], snap\n        if first_state == "unknown":\n            return snap["thread_state"], snap\n        if time.monotonic() - started > timeout:\n            snap.update(thread_state="unknown", reason="dm_composer_budget_exhausted")\n            return snap["thread_state"], snap\n        if not empty:\n            return snap["thread_state"], snap\n        # A0 probes for history after composer admission. An in-flight history\n        # read is evaluated before the next loop-deadline check, as in A0.\n        snap["draft_only_fast"] = True\n        probe_s = float(getattr(config, "DM_EXISTING_THREAD_PROBE_S", 0.8))\n        poll_s = float(getattr(config, "DM_EXISTING_THREAD_POLL_S", 0.1))\n        probe_deadline = time.monotonic() + probe_s\n        while time.monotonic() < probe_deadline:\n            state = observe()\n            if state == "existing":\n                return snap["thread_state"], snap\n            if state == "unknown":\n                return snap["thread_state"], snap\n            time.sleep(poll_s)\n        snap.update(thread_state="empty_new_thread", reason="a0_history_probe_completed")\n        return snap["thread_state"], snap\n    finally:\n        snap["elapsed_s"] = time.monotonic() - started\n        if retention is not None:\n            retention.finish(snap)\n\n\n', ''), (301425, '    try:\n        context = _dm_446_context(d)\n    except Exception:\n        return None\n    if context is not None:\n        return _dm_446_composer(d, context.package)[3]\n', ''), (304635, '    try:\n        context = _dm_446_context(d)\n    except Exception:\n        return False, "dm_version_context_unproved", None, None\n    if context is not None:\n        started_446 = time.monotonic()\n        result_446 = _dm_446_composer(d, current_pkg)\n        if time.monotonic() - started_446 > max(0.0, float(timeout_s)):\n            return False, "dm_composer_budget_exhausted", None, None\n        return result_446\n', ''), (310238, '                    resource_id = str((el.info or {}).get("resourceName", "") or (el.info or {}).get("resource-id", ""))\n                    if resource_id.endswith(":id/direct_new_friend_bump_tray_horizontal_section_recyclerview"):\n                        continue\n', ''), (311453, '    try:\n        context = _dm_446_context(d)\n    except Exception:\n        return "unknown", {"thread_state": "unknown", "reason": "dm_version_context_unproved"}\n    if context is not None:\n        return _dm_446_thread_state(d, username, pkg or context.package, composer_timeout_s)\n', '')]


def restore_exact_dm446_delta(source):
    if hashlib.sha256(source.encode()).hexdigest() != DM446_SOURCE_SHA256:
        raise AssertionError("dm446_source_changed")
    for offset, approved, original in reversed(DM446_EXACT_HUNKS):
        if source[offset:offset + len(approved)] != approved or source.count(approved) != 1:
            raise AssertionError("dm446_delta_shape_changed")
        source = source[:offset] + original + source[offset + len(approved):]
    if hashlib.sha256(source.encode()).hexdigest() != DM446_BASE_SHA256:
        raise AssertionError("dm446_baseline_not_exact")
    return source


def restore_exact_like_budget_delta(source):
    source = restore_exact_dm446_delta(source)
    if hashlib.sha256(source.encode()).hexdigest() != LIKE_BUDGET_SOURCE_SHA256:
        raise AssertionError("like_budget_source_changed")
    for approved, original in LIKE_BUDGET_EXACT_HUNKS:
        if source.count(approved) != 1:
            raise AssertionError("like_budget_hunk_missing_altered_or_duplicated")
        source = source.replace(approved, original, 1)
    if hashlib.sha256(source.encode()).hexdigest() != LIKE_BUDGET_BASE_SHA256:
        raise AssertionError("like_budget_reverse_not_exact_baseline")
    return source


BINDING = dict(permit_id='test-permit', permit_hash='signed-hash', run_id='run',
               request_id='request', account_id='account', worker_id='worker', lease_id='lease',
               device_id='device', serial='serial', package='package', version_name='372', candidate_sha=BASE)
KEY = ('run', 'account', 'ct', 'alice')


def child_admit(directory, key, queue, hold=None):
    try:
        store = ca._PermitStore(directory, BINDING)
        queue.put(store.admit(key))
        if hold is not None:
            hold.wait(10)
        store.close()
    except ca.CertificationStop as exc:
        queue.put(exc.reason)


def child_crash(directory):
    ca._PermitStore(directory, BINDING).admit(KEY)
    os._exit(19)


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ca._PermitStore(self.tmp.name, BINDING)
        self.addCleanup(self.store.close)
        ca._REQUESTED = True
        now = datetime.now(timezone.utc)
        self.claims = {'certification_not_before': (now-timedelta(minutes=1)).isoformat(),
                       'certification_expires_at': (now+timedelta(minutes=5)).isoformat()}
        ca._SESSION = (self.claims, dict(BINDING), self.store)
        self.addCleanup(self.reset)

    def reset(self):
        ca._REQUESTED = False
        ca._SESSION = None

    def gate(self, candidate=None, **overrides):
        import runner
        args=dict(run_id='run',request_id='request',account_id='account',source_ct='ct',
                  candidate={'username':'alice'} if candidate is None else candidate,normalize_handle=runner._norm_ig_handle,
                  valid_handle=runner._is_plausible_public_ig_username)
        args.update(overrides)
        with patch.object(ca, '_canonical_authority'):
            return ca.before_candidate_transport(**args)

    def test_first_second_and_no_transport(self):
        transports=[]
        self.assertEqual(self.gate(),'FIRST_ADMISSION_GRANTED');transports.append('alice')
        with self.assertRaises(ca.CertificationStop) as error:
            self.gate({'username':'bob'});transports.append('bob')
        self.assertEqual(error.exception.reason,'SECOND_CANDIDATE_BLOCKED')
        self.assertEqual(transports,['alice'])
        self.assertEqual(self.store._read()['state'],'TERMINAL')

    def test_first_failure_before_cycle_complete_does_not_reset(self):
        self.gate()
        # No cycle completion API exists; a failure cannot refund admission.
        with self.assertRaises(ca.CertificationStop):self.gate({'username':'bob'})

    def test_same_candidate_recovery(self):
        self.gate()
        self.assertEqual(self.gate({'username':'@ALICE'}),'SAME_CANDIDATE_REENTRY_GRANTED')

    def test_unknown_identity_terminal_and_no_skip(self):
        calls=[]
        with self.assertRaises(ca.CertificationStop) as error:
            self.gate({'username':'vf_row_1','username_pending_profile_read':True});calls.append('open')
        self.assertEqual(error.exception.reason,ca.IDENTITY_UNPROVED)
        self.assertEqual(calls,[])
        with self.assertRaises(ca.CertificationStop):self.gate()

    def test_identity_matrix(self):
        bad=[{}, {'username':'alice','username_pending_profile_read':True},
             {'username':'alice','visual_candidate_id':'row'}, {'resolved_username_hint':'alice'},
             {'username':'alice','resolved_username_hint':'bob'}, {'username':'vfp_12'},
             {'username':True}, {'username':'__visual1'}, {'username':'alice','resolved_username_hint':123}]
        for n,candidate in enumerate(bad):
            with self.subTest(candidate=candidate), tempfile.TemporaryDirectory() as tmp:
                store=ca._PermitStore(tmp,dict(BINDING,permit_id=str(n)))
                ca._SESSION=(self.claims,dict(BINDING),store)
                with self.assertRaises(ca.CertificationStop) as error:self.gate(candidate)
                self.assertEqual(error.exception.reason,ca.IDENTITY_UNPROVED)
                store.close()
        ca._SESSION=(self.claims,dict(BINDING),self.store)

    def test_wrong_run_request_account(self):
        for field in ('run_id','request_id','account_id'):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                store=ca._PermitStore(tmp,BINDING);ca._SESSION=(self.claims,dict(BINDING),store)
                with self.assertRaises(ca.CertificationStop) as error:self.gate(**{field:'other'})
                self.assertEqual(error.exception.reason,'BINDING_MISMATCH');store.close()
        ca._SESSION=(self.claims,dict(BINDING),self.store)

    def test_disabled_no_identity_disk_backend_or_validation(self):
        ca._REQUESTED=False
        with patch.object(ca,'validate_execution_context',side_effect=AssertionError('read')):
            self.assertEqual(ca.before_candidate_transport(run_id=None,request_id=None,account_id=None,
                source_ct=None,candidate=None,normalize_handle=None,valid_handle=None),'DISABLED')

    def test_terminal_no_reuse(self):
        self.store.terminal('failed');self.store.close()
        again=ca._PermitStore(self.tmp.name,BINDING)
        try:
            with self.assertRaises(ca.CertificationStop) as error:again.admit(KEY)
            self.assertEqual(error.exception.reason,'PERMIT_ALREADY_TERMINAL')
        finally:again.close()

    def test_rebind_every_field_rejected(self):
        self.store.admit(KEY);self.store.close()
        for field in ('run_id','request_id','lease_id','device_id','serial','account_id','package','candidate_sha','permit_hash'):
            with self.subTest(field=field), self.assertRaises(ca.CertificationStop):
                ca._PermitStore(self.tmp.name,dict(BINDING,**{field:'other'}))

    def test_missing_state_tombstone_is_not_reissued(self):
        self.store.close();self.store.path.unlink()
        with self.assertRaises(ca.CertificationStop):ca._PermitStore(self.tmp.name,BINDING)
        self.assertFalse(self.store.path.exists())

    def test_corrupt_state_is_not_reset(self):
        self.store.close();self.store.path.write_text('{')
        with self.assertRaises(ca.CertificationStop):ca._PermitStore(self.tmp.name,BINDING)
        self.assertEqual(self.store.path.read_text(),'{')

    def test_same_process_threads_one_first_grant(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            result=list(pool.map(self.store.admit,[KEY,KEY]))
        self.assertEqual(result.count('FIRST_ADMISSION_GRANTED'),1)
        self.assertEqual(result.count('SAME_CANDIDATE_REENTRY_GRANTED'),1)

    def test_other_executor_same_key_cannot_reenter(self):
        self.store.admit(KEY)
        ctx=multiprocessing.get_context('spawn');q=ctx.Queue();p=ctx.Process(target=child_admit,args=(self.tmp.name,KEY,q))
        p.start();p.join(15);self.assertEqual(p.exitcode,0);self.assertEqual(q.get(timeout=2),'PERMIT_INVALID');q.close()

    def test_crash_then_restart_same_and_different(self):
        self.store.close()
        ctx=multiprocessing.get_context('spawn');p=ctx.Process(target=child_crash,args=(self.tmp.name,))
        p.start();p.join(15);self.assertEqual(p.exitcode,19)
        again=ca._PermitStore(self.tmp.name,BINDING)
        try:
            self.assertEqual(again.admit(KEY),'SAME_CANDIDATE_REENTRY_GRANTED')
            with self.assertRaises(ca.CertificationStop) as error:again.admit((*KEY[:-1],'bob'))
            self.assertEqual(error.exception.reason,'SECOND_CANDIDATE_BLOCKED')
        finally:again.close()

    def test_concurrent_first_different_candidates_one_wins(self):
        self.store.close()
        ctx=multiprocessing.get_context('spawn');q=ctx.Queue();hold=ctx.Event()
        ps=[ctx.Process(target=child_admit,args=(self.tmp.name,(*KEY[:-1],name),q,hold)) for name in ('alice','bob')]
        for p in ps:p.start()
        results=[q.get(timeout=15),q.get(timeout=15)];hold.set()
        for p in ps:p.join(15);self.assertEqual(p.exitcode,0)
        self.assertEqual(results.count('FIRST_ADMISSION_GRANTED'),1)
        self.assertEqual(results.count('PERMIT_INVALID'),1);q.close()

    def test_missing_ct_and_hint_only_never_open(self):
        for ct,candidate in (('',{'username':'alice'}), ('ct',{'resolved_username_hint':'alice'})):
            with tempfile.TemporaryDirectory() as tmp:
                store=ca._PermitStore(tmp,BINDING);ca._SESSION=(self.claims,dict(BINDING),store)
                with self.assertRaises(ca.CertificationStop) as error:self.gate(candidate,source_ct=ct)
                self.assertEqual(error.exception.reason,ca.IDENTITY_UNPROVED);store.close()
        ca._SESSION=(self.claims,dict(BINDING),self.store)

    def test_symlink_state_is_rejected_without_target_write(self):
        self.store.close();self.store.path.unlink()
        target=Path(self.tmp.name)/'target';target.write_text('kept')
        self.store.path.symlink_to(target)
        with self.assertRaises(ca.CertificationStop):ca._PermitStore(self.tmp.name,BINDING)
        self.assertEqual(target.read_text(),'kept')

    def test_durability_failure_never_returns_grant(self):
        with patch.object(self.store,'_sync_parent',side_effect=OSError('fsync failed')):
            with self.assertRaises(ca.CertificationStop) as error:self.gate()
            self.assertEqual(error.exception.reason,'PERMIT_INVALID')
        # A replace may already be durable: never return AVAILABLE or refund it.
        self.assertEqual(self.store._read()['state'],'CONSUMED_BOUND')
        with self.assertRaises(ca.CertificationStop):self.store.admit((*KEY[:-1],'bob'))

    def test_request_cancel_and_expiry_fail_closed(self):
        import auto_restart_device_lock as locks,account_run_control as requests,supabase_client as runs
        lease={k:BINDING[k] for k in ('device_id','worker_id','request_id','account_id','lease_id')}
        lease['lease_expires_at']=self.claims['certification_expires_at']
        req=dict(id='request',account_id='account',run_id='run',claimed_by='worker',status='running',
                 lease_expires_at=lease['lease_expires_at'],metadata_safe={'device_lock_binding':dict(lease,generation='lease')})
        run=dict(id='run',account_id='account',status='running')
        with patch.object(locks,'_load_device_lock',return_value=lease),patch.object(requests,'get_account_run_request',return_value=req),patch.object(runs,'load_run_row',return_value=run):
            req['cancel_reason']='operator_stop'
            with self.assertRaises(ca.CertificationStop):ca._canonical_authority(BINDING)
            req.pop('cancel_reason');req['lease_expires_at']='2000-01-01T00:00:00Z'
            with self.assertRaises(ca.CertificationStop):ca._canonical_authority(BINDING)

    def test_expired_window(self):
        self.claims['certification_expires_at']='2000-01-01T00:00:00Z'
        with self.assertRaises(ca.CertificationStop):self.gate()

    def test_invalid_configuration_stays_enabled(self):
        self.reset()
        with self.assertRaises(ca.CertificationStop):ca.configure_from_signed_permit(
            manifest_path='/does/not/exist',signature_path='/does/not/exist',permit_id='x',execution_binding={},state_directory=self.tmp.name)
        self.assertTrue(ca.is_enabled())
        with self.assertRaises(ca.CertificationStop):ca.validate_execution_context()

    def test_canonical_authority_rejects_wrong_and_terminal_rows(self):
        import auto_restart_device_lock as locks,account_run_control as requests,supabase_client as runs
        lease={k:BINDING[k] for k in ('device_id','worker_id','request_id','account_id','lease_id')}
        lease['lease_expires_at']=self.claims['certification_expires_at']
        req=dict(id='request',account_id='account',run_id='run',claimed_by='worker',status='running')
        req.update(lease_expires_at=lease['lease_expires_at'],metadata_safe={'device_lock_binding':dict(lease,generation='lease')})
        run=dict(id='run',account_id='account',status='running')
        with patch.object(locks,'_load_device_lock',return_value=lease),patch.object(requests,'get_account_run_request',return_value=req),patch.object(runs,'load_run_row',return_value=run):
            ca._canonical_authority(BINDING)
            for field in ('device_id','worker_id','request_id','account_id','lease_id'):
                saved=lease[field];lease[field]='wrong'
                with self.assertRaises(ca.CertificationStop):ca._canonical_authority(BINDING)
                lease[field]=saved
            run['status']='completed'
            with self.assertRaises(ca.CertificationStop):ca._canonical_authority(BINDING)


class RunnerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=restore_exact_product_delta('runner.py', (ROOT/'runner.py').read_text());cls.tree=ast.parse(cls.source)
        cls.baseline=ast.parse(subprocess.check_output(['git','show',BASE+':runner.py'],cwd=ROOT).decode())

    def test_every_actual_open_is_immediately_guarded(self):
        found=[]
        for parent in ast.walk(self.tree):
            for _,body in ast.iter_fields(parent):
                if not isinstance(body,list):continue
                for i,stmt in enumerate(body):
                    if not isinstance(stmt,ast.stmt):continue
                    direct=[]
                    if isinstance(stmt,ast.Assign):direct=[stmt.value]
                    if isinstance(stmt,ast.If) and isinstance(stmt.test,ast.UnaryOp):direct=[stmt.test.operand]
                    for call in direct:
                        if isinstance(call,ast.Call) and isinstance(call.func,ast.Name) and call.func.id in ('open_follower_profile_from_list','open_visual_follower_candidate_from_screenshot'):
                            hook=body[i-1];self.assertIsInstance(hook,ast.If)
                            self.assertEqual(ast.unparse(hook.test),'certification_admission.is_enabled()')
                            self.assertIn('certification_admission.before_candidate_transport',ast.unparse(hook))
                            found.append((hook,stmt))
        self.assertEqual(len(found),3)
        # Execute the actual hook + actual call AST at each site, with bounded I/O doubles.
        for hook,stmt in found:
            for enabled in (False,True):
                calls=[]
                class Gate:
                    def is_enabled(self):return enabled
                    def before_candidate_transport(self,**kw):raise ca.CertificationStop(ca.IDENTITY_UNPROVED)
                env=dict(certification_admission=Gate(),run_id='run',_CURRENT_RUN_REQUEST_ID='request',
                         _CURRENT_ACCOUNT_ID='account',account_id='account',source_profile_username='ct',
                         best={},pick={},_norm_ig_handle=lambda v:v,_is_plausible_public_ig_username=lambda v:True,
                         d=object(),pkg='package',open_follower_profile_from_list=lambda *a,**k:calls.append('open') or True,
                         open_visual_follower_candidate_from_screenshot=lambda *a,**k:calls.append('open') or {})
                node=ast.If(test=stmt.test,body=[ast.Pass()],orelse=[]) if isinstance(stmt,ast.If) else stmt
                code=compile(ast.fix_missing_locations(ast.Module(body=[hook,node],type_ignores=[])),'actual-runner-hook','exec')
                if enabled:
                    with self.assertRaises(ca.CertificationStop):exec(code,env)
                    self.assertEqual(calls,[])
                else:exec(code,env);self.assertEqual(calls,['open'])

    def test_normal_runner_ast_unchanged_except_authorized_hooks(self):
        class Strip(ast.NodeTransformer):
            def visit_Import(self,n):
                return None if any(a.name=='certification_admission' for a in n.names) else n
            def visit_If(self,n):
                if ast.unparse(n.test)=='certification_admission.is_enabled()':return None
                return self.generic_visit(n)
            def visit_FunctionDef(self,n):
                if n.name=='main':
                    # main audited independently; remove only the dedicated handler/finally and guard.
                    n=self.generic_visit(n);t=n.body[0]
                    t.handlers=[h for h in t.handlers if ast.unparse(h.type)!='certification_admission.CertificationStop']
                    t.finalbody=[]
                    return n
                return self.generic_visit(n)
        neutral = '    # Early acquisition aborts can publish before the Followers loop starts.\n    _followers_loop_finally_stop = ""\n\n'
        self.assertEqual(self.source.count(neutral), 1)
        hook = '        stable, proof_reason = build_stable_candidate_proof_v2(\n            binding=binding,\n            device_context=d,\n'
        original_hook = '        stable, proof_reason = build_stable_candidate_proof_v2(\n            binding=binding,\n'
        self.assertEqual(self.source.count(hook), 1)
        self.assertEqual(self.source.count('            device_context=d,\n'), 1)
        candidate=Strip().visit(ast.parse(restore_exact_self_xml_delta("runner.py", self.source).replace(neutral, "", 1).replace(hook, original_hook, 1)))
        self.assertEqual(ast.dump(candidate,include_attributes=False),ast.dump(self.baseline,include_attributes=False))

    def test_approved_neutral_init_rejects_other_runner_deltas(self):
        original = self.source
        neutral = '    # Early acquisition aborts can publish before the Followers loop starts.\n    _followers_loop_finally_stop = ""\n\n'
        for altered in (original.replace(neutral, '', 1), original + neutral,
                        original.replace(neutral, neutral.replace('= ""', '= "changed"'), 1),
                        original + '\nUNRELATED_PRODUCTION_CHANGE = True\n'):
            with self.subTest(altered_hash=hashlib.sha256(altered.encode()).hexdigest()):
                self.source = altered
                try:
                    with self.assertRaises(AssertionError):
                        self.test_normal_runner_ast_unchanged_except_authorized_hooks()
                finally:
                    self.source = original

    def test_exact_v2_context_wiring_rejects_other_runner_deltas(self):
        original=self.source
        keyword='            device_context=d,\n'
        for altered in (original.replace(keyword,'',1), original.replace(keyword,keyword*2,1),
                        original.replace(keyword,'            device_context=other,\n',1),
                        original+'\nUNRELATED_V2_CHANGE = True\n'):
            with self.subTest(altered=hashlib.sha256(altered.encode()).hexdigest()):
                self.source=altered
                try:
                    with self.assertRaises(AssertionError):
                        self.test_normal_runner_ast_unchanged_except_authorized_hooks()
                finally:self.source=original

    def test_main_stop_reaches_terminal_without_ui_cleanup(self):
        import runner
        exc=ca.CertificationStop(ca.IDENTITY_UNPROVED)
        with patch.object(ca,'is_enabled',return_value=True),patch.object(ca,'validate_execution_context'),patch.object(ca,'mark_terminal') as end,patch.object(runner,'_main_impl',side_effect=exc),patch.object(runner,'log'),patch.object(runner,'_update_run_status_safe') as update,patch.object(runner,'_return_with_cleanup',side_effect=AssertionError('UI cleanup')),patch.object(runner,'_CURRENT_RUN_ID','run'):
            self.assertEqual(runner.main(),1);end.assert_called_once()
            self.assertEqual(update.call_args.kwargs['performance_summary']['root_failure_code'],ca.IDENTITY_UNPROVED)
            self.assertEqual(update.call_args.kwargs['status'],'stopped')

    # Exact hunks authorized by a0-ui-446 Approval1 and its test amendment.
    # No function/AST exclusions: reversing these must reconstruct all A0 bytes.
    _APPROVED_446_HUNKS = {'device.py': [(b'            )\n    # Version-scoped evidence reuse: no additional device read on either version.\n    if (vn, vc) == ("446.0.0.49.77", 385211304) or "_instagram_446_observed_context" in vars(d):\n        from instagram_ui_446_compat import retain_context\n        retain_context(d, serial=serial, package=pkg, version_name=vn,\n                       version_code=vc, unsafe=unsafe)\n    if not unsafe and serial and pkg and type(vc) is int:\n        vars(d)["_instagram_search_observed_version"] = (id(d), serial, pkg, vn, vc)\n    return payload\n', b'            )\n    return payload\n'), (b'    # Revoke before any read: a failed re-check cannot retain Search authority.\n    vars(d)["_instagram_search_observed_version"] = None\n', b'') , (b'    """Send text via FastInputIME broadcast using direct adb-shell argv semantics."""\n    code, out, err = _adb_run(\n        serial,\n        [\n            "shell",\n            "am",\n            "broadcast",\n            "-a",\n            "ADB_INPUT_TEXT",\n            "--es",\n            "msg",\n            str(text or ""),\n        ],\n    )\n', b'    """\n    Send text via FastInputIME broadcast.\n\n    Uses ``adb shell sh -c`` with a single quoted message so spaces, newlines,\n    apostrophes and unicode are not split by the device shell (avoids ``pkg=merci`` /\n    ``Ceci: inaccessible`` failures).\n    """\n    import shlex\n\n    inner = f"am broadcast -a ADB_INPUT_TEXT --es msg {shlex.quote(str(text or \'\'))}"\n    code, out, err = _adb_run(serial, ["shell", "sh", "-c", inner])\n')], 'instagram_navigation.py': [(b'\ndef _follow_ui_state_and_fresh_hierarchy(\n    d: u2.Device, *, expected_username: str = ""\n) -> tuple[str, str]:\n    """Use one hierarchy as both Follow-state proof and late-popup authority."""\n', b'\ndef _follow_ui_state_and_fresh_hierarchy(d: u2.Device) -> tuple[str, str]:\n    """Use one hierarchy as both Follow-state proof and late-popup authority."""\n'), (b'        hierarchy = ""\n    if "_instagram_446_observed_context" in vars(d):\n        from instagram_ui_446_compat import context_matches, relationship_from_xml\n        package = str(getattr(config, "INSTAGRAM_PACKAGE", "") or "")\n        context = vars(d)["_instagram_446_observed_context"]\n        if not hierarchy or not context_matches(context, d, get_device_serial(d), package):\n            return "unknown", hierarchy\n        return relationship_from_xml(hierarchy, package=package,\n                                     expected_username=expected_username), hierarchy\n    if not hierarchy:\n', b'        hierarchy = ""\n    if not hierarchy:\n'), (b'            _fresh_state, _fresh_verify_hierarchy = (\n                _follow_ui_state_and_fresh_hierarchy(d, expected_username=str(username or ""))\n            )\n', b'            _fresh_state, _fresh_verify_hierarchy = (\n                _follow_ui_state_and_fresh_hierarchy(d)\n            )\n'), (b'    if not _open_446_explore_search_entry(d, pkg):\n        return False\n\n', b''), (b'def _open_446_explore_search_entry(d: u2.Device, package: str) -> bool:\n    """One qualified446 Explore header action;372 remains an I/O-free no-op."""\n    from run_latency_observability_v1 import _TimedProxy, _UI_METHODS\n    from instagram_ui_446_compat import context_matches, explore_search_entry_from_xml\n    key = "_instagram_446_observed_context"\n\n    def authority():\n        owner = d\n        if isinstance(d, _TimedProxy):\n            if type(d) is not _TimedProxy or vars(d).get("_kind") != "device":\n                raise ValueError("search_context_owner_unproved")\n            owner = vars(d).get("_raw")\n            if owner is None or isinstance(owner, _TimedProxy):\n                raise ValueError("search_context_owner_unproved")\n        observed = vars(owner).get("_instagram_search_observed_version")\n        serial = get_device_serial(owner)\n        if (type(observed) is not tuple or len(observed) != 5\n                or observed[:3] != (id(owner), serial, package)\n                or not serial or not package or type(observed[4]) is not int):\n            raise ValueError("search_version_authority_unproved")\n        if observed[3:] == ("372.0.0.48.60", 377611059):\n            if key in vars(owner):\n                raise ValueError("search_version_authority_contradictory")\n            return owner, observed, None\n        context = vars(owner).get(key)\n        if (observed[3:] != ("446.0.0.49.77", 385211304)\n                or not context_matches(context, owner, serial, package)):\n            raise ValueError("version_context_unproved")\n        return owner, observed, context\n\n    try:\n        owner, observed, context = authority()\n        if context is None:\n            return True\n        proof = explore_search_entry_from_xml(d.dump_hierarchy(compressed=False), package=package)\n        if proof is None:\n            raise ValueError("explore_entry_unproved")\n        # Re-resolve the exact live ancestry, never any Search/Not-now match.\n        control = d(resourceId=package + ":id/explore_action_bar")\n        for suffix in ("explore_action_bar_container", "action_bar_search_hints_text_layout",\n                       "action_bar_search_edit_text"):\n            control = control.child(resourceId=package + ":id/" + suffix)\n        # child() returns a raw selector in the existing instrumentation API.\n        # Keep the final scoped click observable without changing that API.\n        if type(d) is _TimedProxy:\n            control = _TimedProxy(control, kind="uiautomator",\n                                  emit=vars(d)["_emit"], methods=_UI_METHODS)\n        if control.count != 1:\n            raise ValueError("search_entry_not_unique")\n        tab = d(resourceId=package + ":id/search_tab")\n        if tab.count != 1:\n            raise ValueError("search_tab_not_unique")\n        def matches(info, node, *, search_tab_text=False, qualified_header=False):\n            from instagram_ui_446_compat import _bounds\n            b = info.get("bounds") or {}\n            return ((info.get("resourceName") == node["resource-id"]\n                     or (qualified_header and info.get("resourceName") is None\n                         and node["resource-id"] == package + ":id/action_bar_search_edit_text"))\n                    and info.get("className") == node["class"]\n                    and (info.get("text", "") == node.get("text", "")\n                         or (search_tab_text and info.get("text") is None\n                             and node.get("text") == ""))\n                    and info.get("contentDescription", "") == node.get("content-desc", "")\n                    and info.get("enabled") is True and info.get("clickable") is True\n                    and info.get("selected") == (node.get("selected") == "true")\n                    and info.get("focused") == (node.get("focused") == "true")\n                    and tuple(b.get(k) for k in ("left", "top", "right", "bottom")) == _bounds(node))\n        if not matches(tab.info, proof["search_tab"], search_tab_text=True):\n            raise ValueError("search_tab_changed")\n        # The unique, ancestry-scoped RID selector above proves header identity.\n        # UiObject fallback does not populate its optional resourceName echo.\n        if not matches(control.info, proof["action_bar_search_edit_text"], qualified_header=True):\n            raise ValueError("search_entry_changed")\n        current_owner, current_observed, current_context = authority()\n        if (current_owner is not owner or current_observed is not observed\n                or current_context is not context):\n            raise ValueError("version_context_changed")\n        control.click()\n        log("info", "open_search_446_header_clicked", editable_postcondition="pending")\n        return True\n    except Exception as exc:\n        log("warning", "open_search_446_entry_rejected", reason=str(exc) if isinstance(exc, ValueError)\n            else type(exc).__name__)\n        return False\n\n\n', b''), (b'def _post_open_intent_surface_from_final_proof(\n    evidence: dict[str, Any] | None,\n    *,\n    expected_package: str,\n    device_context: Any = None,\n)', b'def _post_open_intent_surface_from_final_proof(\n    evidence: dict[str, Any] | None,\n    *,\n    expected_package: str,\n)'), (b'    activity_lower = activity.lower()\n    if "instagram" not in activity_lower or "mainactivity" not in activity_lower:\n        # Extend only the existing, version-qualified original446 authority.\n        qualified_main_tab = False\n        if (package == "com.instagram.android" and device_context is not None\n                and activity in ("com.instagram.android.activity.MainTabActivity",\n                                 ".activity.MainTabActivity")):\n            try:\n                qualified_main_tab = _followers_row_activity_allowed(\n                    device_context, package, activity)\n            except (AttributeError, TypeError):\n                qualified_main_tab = False\n        if not qualified_main_tab:\n            return "", ""\n    return package, activity', b'    activity_lower = activity.lower()\n    if "instagram" not in activity_lower or "mainactivity" not in activity_lower:\n        return "", ""\n    return package, activity'), (b'                _post_open_intent_surface_from_final_proof(\n                    _intent_evidence,\n                    expected_package=pkg,\n                    device_context=d,\n                )', b'                _post_open_intent_surface_from_final_proof(\n                    _intent_evidence,\n                    expected_package=pkg,\n                )'), (b'    activity_lower = current_activity.lower()\n    instagram_main_activity = bool(\n        "instagram" in activity_lower and "mainactivity" in activity_lower\n    )\n    if (not instagram_main_activity and package_exact\n            and current_package == "com.instagram.android"\n            and current_activity in ("com.instagram.android.activity.MainTabActivity",\n                                     ".activity.MainTabActivity")):\n        try:\n            instagram_main_activity = _followers_row_activity_allowed(\n                d, current_package, current_activity)\n        except (AttributeError, TypeError):\n            instagram_main_activity = False\n', b'    activity_lower = current_activity.lower()\n    instagram_main_activity = bool(\n        "instagram" in activity_lower and "mainactivity" in activity_lower\n    )\n'), (b'    activity_lower = str(current_activity or "").lower()\n    instagram_main_activity = bool(\n        "instagram" in activity_lower and "mainactivity" in activity_lower\n    )\n    if (not instagram_main_activity and package_exact\n            and current_package == "com.instagram.android"\n            and current_activity in ("com.instagram.android.activity.MainTabActivity",\n                                     ".activity.MainTabActivity")):\n        try:\n            instagram_main_activity = _followers_row_activity_allowed(\n                d, current_package, current_activity)\n        except (AttributeError, TypeError):\n            instagram_main_activity = False\n', b'    activity_lower = str(current_activity or "").lower()\n    instagram_main_activity = bool(\n        "instagram" in activity_lower and "mainactivity" in activity_lower\n    )\n'), (b'        stage_activity_allowed = bool(\n            "InstagramMainActivity" in str(stage_ctx.get("activity") or "")\n        )\n        if (not stage_activity_allowed\n                and str(stage_ctx.get("package") or "") == "com.instagram.android"\n                and str(stage_ctx.get("activity") or "") in (\n                    "com.instagram.android.activity.MainTabActivity",\n                    ".activity.MainTabActivity")\n                and str(stage_ctx.get("activity") or "")\n                == str(meta0.get("current_activity") or "")):\n            try:\n                stage_activity_allowed = _followers_row_activity_allowed(\n                    d, str(stage_ctx.get("package") or ""),\n                    str(stage_ctx.get("activity") or ""))\n            except (AttributeError, TypeError):\n                stage_activity_allowed = False\n        stage_scoped_open_context_ok = bool(\n', b'        stage_scoped_open_context_ok = bool(\n'), (b'            and stage_activity_allowed\n', b'            and "InstagramMainActivity"\n            in str(stage_ctx.get("activity") or "")\n'), (b'        activity_allowed = bool(\n            "instagram" in live_activity.lower() and "mainactivity" in live_activity.lower()\n        )\n        if (not activity_allowed and live_package == "com.instagram.android"\n                and live_activity in (\n                    "com.instagram.android.activity.MainTabActivity", ".activity.MainTabActivity")):\n            try:\n                activity_allowed = _followers_row_activity_allowed(d, live_package, live_activity)\n            except (AttributeError, TypeError):\n                activity_allowed = False\n        if not activity_allowed:\n            return False, "liketapcontext_viewer_mismatch", age_ms\n', b'        if "instagram" not in live_activity.lower() or "mainactivity" not in live_activity.lower():\n            return False, "liketapcontext_viewer_mismatch", age_ms\n')]}
    _APPROVED_446_HUNKS["instagram_navigation.py"].extend([(b'def _followers_row_activity_allowed(d, package: str, activity: str) -> bool:\n    """Local membership only; consumes the already retained version evidence."""\n    if not package:\n        return False\n    if activity == "com.instagram.mainactivity.InstagramMainActivity":\n        return True\n    if package != "com.instagram.android" or activity not in (\n        "com.instagram.android.activity.MainTabActivity", ".activity.MainTabActivity"\n    ):\n        return False\n    from run_latency_observability_v1 import _TimedProxy\n    from instagram_ui_446_compat import context_matches\n    owner = d\n    if isinstance(d, _TimedProxy):\n        if type(d) is not _TimedProxy or vars(d).get("_kind") != "device":\n            return False\n        owner = vars(d).get("_raw")\n        if owner is None or isinstance(owner, _TimedProxy):\n            return False\n    observed = vars(owner).get("_instagram_search_observed_version")\n    serial = get_device_serial(owner)\n    return (type(observed) is tuple and len(observed) == 5\n            and observed[:3] == (id(owner), serial, package) and bool(serial)\n            and type(observed[4]) is int\n            and observed[3:] == ("446.0.0.49.77", 385211304)\n            and context_matches(vars(owner).get("_instagram_446_observed_context"),\n                                owner, serial, package))\n\n\n', b''), (b'    if not _followers_row_activity_allowed(d, pkg, current_activity):\n', b'    if current_activity and "MainActivity" not in current_activity:\n')])

    _APPROVED_446_HUNKS["instagram_navigation.py"].append((b'    if not _followers_row_activity_allowed(d, expected_package, current_activity):\n', b'    if not (\n        "instagrammainactivity" in activity_norm\n        or activity_norm.endswith(".mainactivity")\n        or "profileactivity" in activity_norm\n    ):\n'))

    def _assert_navigation_a0_with_exact_446_delta(self, name, candidate, *, profile_restored=False):
        if name == "device.py" and not profile_restored:
            candidate = restore_exact_ui_profile_delta(name, candidate.decode()).encode()
        normalized = (restore_exact_like_budget_delta(candidate.decode()).encode()
                      if name == "instagram_navigation.py" else candidate)
        for current, original in self._APPROVED_446_HUNKS.get(name, ()):
            self.assertEqual(normalized.count(current), 1,
                             "approved446 hunk missing, duplicated or modified: " + name)
            normalized = normalized.replace(current, original, 1)
        self.assertEqual(normalized, subprocess.check_output(
            ['git', 'show', BASE + ':' + name], cwd=ROOT))

    def test_navigation_exact_a0_bytes(self):
        for name in ('device.py','own_profile_navigation.py','account_identity_guard.py','instagram_navigation.py','follow60_mainline_v2.py'):
            self._assert_navigation_a0_with_exact_446_delta(name, restore_exact_product_delta(name, (ROOT/name).read_text()).encode())

    def test_navigation_freeze_rejects_unrelated_byte_changes(self):
        for name in ('device.py','own_profile_navigation.py','account_identity_guard.py','instagram_navigation.py','follow60_mainline_v2.py'):
            with self.subTest(name=name), self.assertRaises(AssertionError):
                self._assert_navigation_a0_with_exact_446_delta(
                    name, restore_exact_product_delta(name, (ROOT/name).read_text()).encode() + b'\n# unrelated change\n')

    def test_navigation_freeze_rejects_changed_or_missing_approved_hunk(self):
        for name, hunks in self._APPROVED_446_HUNKS.items():
            source = restore_exact_product_delta(name, (ROOT/name).read_text()).encode()
            if name == "device.py":
                source = restore_exact_ui_profile_delta(name, source.decode()).encode()
            for current, original in hunks:
                self.assertEqual(source.count(current), 1, "negative fixture must mutate a present hunk")
                for replacement in (original, current + b'# changed hunk\n'):
                    mutated = source.replace(current, replacement, 1)
                    self.assertNotEqual(mutated, source)
                    with self.subTest(name=name), self.assertRaises(AssertionError):
                        self._assert_navigation_a0_with_exact_446_delta(
                            name, mutated, profile_restored=name == "device.py")

    def test_profile_inverse_rejects_missing_changed_duplicate_moved_and_unrelated(self):
        import base64, zlib
        records = json.loads(zlib.decompress(base64.b64decode(_PROFILE_EXACT_INVERSE)))
        for name, record in records.items():
            source = restore_exact_product_delta(name, (ROOT/name).read_text())
            baseline = subprocess.check_output(['git', 'show', 'baf0bf85701e0c28e65ca8ebe71b9537e7293b73:' + name], cwd=ROOT, text=True)
            self.assertEqual(restore_exact_ui_profile_delta(name, source), baseline)
            for hunk in record['hunks']:
                offset, text = hunk['offset'], hunk['new']
                for changed in (source[:offset] + source[offset + len(text):],
                                source[:offset] + '# changed\n' + source[offset:],
                                source + text,
                                source[:offset] + source[offset + len(text):] + text,
                                source + '\n# unrelated\n'):
                    self.assertNotEqual(changed, source)
                    with self.subTest(name=name, offset=offset), self.assertRaises(AssertionError):
                        restore_exact_ui_profile_delta(name, changed)

    def test_navigation_freeze_rejects_duplicate_approved_hunk(self):
        for name, hunks in self._APPROVED_446_HUNKS.items():
            for current, _ in hunks:
                with self.subTest(name=name), self.assertRaises(AssertionError):
                    self._assert_navigation_a0_with_exact_446_delta(
                        name, restore_exact_product_delta(name, (ROOT/name).read_text()).encode() + current)

    def test_no_ui_dependency_or_bare_except(self):
        tree=ast.parse((ROOT/'certification_admission.py').read_text())
        names=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        self.assertFalse(any(n and (n.startswith('ui_') or 'navigation' in n or n=='runner') for n in names))
        self.assertFalse(any(isinstance(n,ast.ExceptHandler) and n.type is None for n in ast.walk(self.tree)))
        self.assertTrue(issubclass(ca.CertificationStop,BaseException));self.assertFalse(issubclass(ca.CertificationStop,Exception))


if __name__=='__main__':unittest.main()



class LikeBudgetExactFreezeTests(unittest.TestCase):
    def test_exact_hunks_reconstruct_signed_baseline(self):
        source = restore_exact_product_delta('instagram_navigation.py', (ROOT/'instagram_navigation.py').read_text())
        expected = subprocess.check_output(['git','show',
            '8519f1c16284a7f4f4185e898b0b281d8ea27e72:instagram_navigation.py'],cwd=ROOT,text=True)
        self.assertEqual(restore_exact_like_budget_delta(source),expected)
        for approved, original in LIKE_BUDGET_EXACT_HUNKS:
            self.assertEqual(source.count(approved),1)

    def test_missing_changed_duplicate_and_moved_hunks_fail(self):
        source = restore_exact_product_delta('instagram_navigation.py', (ROOT/'instagram_navigation.py').read_text())
        for approved, original in LIKE_BUDGET_EXACT_HUNKS:
            for mutated in (source.replace(approved,original,1),
                            source.replace(approved,approved+'# unauthorized byte\n',1),
                            source+approved,
                            source.replace(approved,'',1)+approved):
                with self.subTest(hunk=hashlib.sha256(approved.encode()).hexdigest()), self.assertRaises(AssertionError):
                    restore_exact_like_budget_delta(mutated)

    def test_unrelated_semantic_and_business_mutations_fail(self):
        source = restore_exact_product_delta('instagram_navigation.py', (ROOT/'instagram_navigation.py').read_text())
        names = ('_ui_post_viewer_action_button_liked_strict',
                 '_ui_post_viewer_liked_state_for_verify','visual_verify_post_liked',
                 '_visual_filled_heart_red_ratio','_hierarchy_liked_state_for_post_verify',
                 '_followers_row_activity_allowed','visual_like_open_post')
        tree = ast.parse(source)
        for name in names:
            node = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name == name)
            lines = source.splitlines(True)
            lines.insert(node.end_lineno,'# unauthorized semantic/budget/selector/ROI/XML/old446 delta\n')
            with self.subTest(name=name), self.assertRaises(AssertionError):
                restore_exact_like_budget_delta(''.join(lines))
        for old,new in [('timeout=0.14','timeout=0.15'),
                        ('row_feed_button_like','row_feed_button_wrong'),
                        ('semantic_deadline=semantic_deadline','semantic_deadline=None')]:
            self.assertIn(old,source)
            with self.subTest(change=old), self.assertRaises(AssertionError):
                restore_exact_like_budget_delta(source.replace(old,new,1))
        with self.assertRaises(AssertionError):
            restore_exact_like_budget_delta(source+'\n')

    def test_reentry_follow_mute_return_ct_v1_routing_remain_exact(self):
        hashes = {'follow_action_engine.py': '3240d8d6aa4f3ed59a3615706f8104644696910ca0e71a9f4e44598f7a6fb9df', 'follow60_mainline_v2.py': '572a6a9e66c6cf434ef7335675f85e73ab215593eb6e86d99f2677c1c7ba0652', 'runner.py': '290ee55729856b9d3f4f821bce43b4e0a10a4903755c95fcfd4b7dbdb90e6829', 'follow60_ordering_v2_behavioral_canary_v1.py': '57f75c698c550e8ac943ac7da67498058c5cbe2c8139200d8eb1966a892eb92e'}
        def assert_exact(name, content):
            if hashlib.sha256(content).hexdigest() != hashes[name]:
                raise AssertionError('business_companion_changed:' + name)
        for name in hashes:
            content = restore_exact_product_delta(name, (ROOT/name).read_text()).encode()
            if name == "runner.py":
                content = restore_exact_self_xml_delta(name, content.decode()).encode()
            assert_exact(name,content)
            for bad in (content+b'\n',content[:-1],b'# changed reentry/fallback\n'+content):
                with self.subTest(name=name), self.assertRaises(AssertionError):
                    assert_exact(name,bad)


class SelfXmlExactFreezeTests(unittest.TestCase):
    def test_exact_inverse_and_missing_altered_duplicate_or_moved_hunks(self):
        for name, hunks in SELF_XML_EXACT_HUNKS.items():
            source = restore_exact_product_delta(name, (ROOT/name).read_text())
            restored = restore_exact_self_xml_delta(name, source)
            self.assertEqual(hashlib.sha256(restored.encode()).hexdigest(), SELF_XML_BASE_HASHES[name])
            for approved, original in hunks:
                for bad in (source.replace(approved, original, 1),
                            source.replace(approved, approved.replace('verified_current_account', 'wrong_current_account'), 1),
                            source + approved,
                            source.replace(approved, '', 1) + approved):
                    with self.subTest(name=name), self.assertRaises(AssertionError):
                        restore_exact_self_xml_delta(name, bad)

    def test_business_and_orchestrator_mutations_remain_rejected(self):
        for name, surfaces in [('runner.py', ('reentry', 'Follow', 'Mute', 'Return CT', 'checkpoint/resume', 'extra byte')),
                               ('account_session_orchestrator.py', ('Target Availability', 'rotation', 'unapproved propagation'))]:
            source = restore_exact_product_delta(name, (ROOT/name).read_text())
            tree = ast.parse(source)
            # Inject a byte inside real functions as well as at file end. Nothing
            # outside the authorized hunks may be swallowed by normalization.
            functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
            for i, surface in enumerate(surfaces):
                node = functions[i % len(functions)]
                lines = source.splitlines(True)
                lines.insert(node.end_lineno, '# unauthorized ' + surface + '\n')
                with self.subTest(name=name, surface=surface), self.assertRaises(AssertionError):
                    restore_exact_self_xml_delta(name, ''.join(lines))
            with self.assertRaises(AssertionError):
                restore_exact_self_xml_delta(name, source + '\n')



class CanonicalBackendFixture:
    """Backend GET and local producer-file fixtures; no admission/reader mock."""
    def __init__(self, root, binding):
        from tests.test_follow_persistence_rpc_v1 import SETTINGS_REVISION
        self.root=Path(root).resolve();self.binding=binding;self.intents=self.root/'intents'
        self.ledgers=[];self.events={};self.interactions={};self.calls=[]
        self.settings=dict(account_id=binding['account_id'],unfollow_after_days=3,updated_at=SETTINGS_REVISION)

    def action(self,name):
        from follow_persistence_rpc import deterministic_action_id
        return deterministic_action_id(self.binding['account_id'],self.binding['run_id'],name)

    def intent_path(self,name):
        return self.intents/self.binding['run_id']/(self.action(name)+'.json')

    def install(self, stack):
        import supabase_client as db
        stack.enter_context(patch.dict(os.environ,{'FOLLOW_PERSISTENCE_INTENT_ROOT':str(self.intents)}))
        stack.enter_context(patch.object(db,'_request_json',side_effect=self.get))

    def get(self,method,table,*,query,request_timeout,max_retries):
        import copy
        assert method=='GET' and request_timeout==5.0 and max_retries==0
        self.calls.append((table,dict(query)))
        b=self.binding
        if table=='follow_60s_completed_cycle_ledger':result=self.ledgers
        elif table=='ig_runs':result=[dict(id=b['run_id'],account_id=b['account_id'])]
        elif table=='account_run_requests':result=[dict(id=b['request_id'],account_id=b['account_id'],run_id=b['run_id'])]
        elif table=='ig_account_unfollow_settings':result=[self.settings]
        elif table=='ig_interaction_events':
            action=query['or'].split('id.eq.')[1].split(',')[0];result=self.events.get(action,[])
        elif table=='ig_interacted_users':result=[self.interactions[query['username'][3:]]] if query['username'][3:] in self.interactions else []
        else:raise AssertionError('unexpected_backend_read:'+table)
        return copy.deepcopy(result)

    def cycle(self,name,*,terminal='verified',complete=True,v2_action=None):
        from tests.test_follow_persistence_rpc_v1 import canonical_evidence, FOLLOWED_AT, SETTINGS_REVISION
        b=self.binding;action=self.action(name);ah=hashlib.sha256(action.encode()).hexdigest();common={k:b[k] for k in ('account_id','run_id','request_id')}
        reason='like_verified' if terminal=='verified' else 'no_posts_verified'
        intent=dict(version=3,receipt_schema='AMBIGUOUS_MUTATION_INTENT_V1',action_id=action,idempotency_key=action,action_type='follow',**common,
                    candidate_username=name,source_ct_username='ct',source_target_id='target',business_session_id='session',attempt_id='1',intent_generation='1',
                    worker_sha=b['candidate_sha'],settings_revision=SETTINGS_REVISION,stage='persisted',physical_attempt_started_at=FOLLOWED_AT,followed_at=FOLLOWED_AT,
                    interaction_row_id=None,receipt_metadata_safe=dict(candidate_username=name,source_profile='ct',tap_sent=True,physical_follow_state='following',
                    tap_ready_boundary_reached=True,verification_method='worker_exact_following_state',post_follow_stage_persist_ok=True,return_ct_exact=True,
                    receipt_ready_before_critical_rpc=True,post_follow_stage_results=dict(mute_posts_verified=True,mute_stories_verified=True,return_ct_exact=True)))
        path=self.intent_path(name);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(intent))
        ev=canonical_evidence(action);follow=ev['event'];follow.update(common,username=name,source_profile='ct',source_target_id='target',source_target_username='ct',evidence_source='worker_follow_state_exact')
        interaction=ev['interaction'];interaction.update(common,username=name,source_target_id='target',interaction_type='follow');interaction['id']='interaction-'+name;follow['payload']['interaction_id']=interaction['id']
        self.interactions[name]=interaction;events=[follow]
        for stage,event_type,it in [('mute_posts_verified','mute_posts_verified','mute'),('mute_stories_verified','mute_success','mute'),('return_ct_exact','return_ct_exact','navigation')]+([('like_verified','post_like_success','like')] if terminal=='verified' else []):
            payload=dict(schema='FOLLOW60_MAINLINE_POST_FOLLOW_V3',stage=stage,action_id_hash=ah,attempt_id=1,business_session_id='session',binding_kind='mainline',binding_id=b['run_id'],control_id=b['run_id'],worker_sha=b['candidate_sha'])
            if stage=='mute_posts_verified':payload['muted_posts']=True
            if stage=='mute_stories_verified':payload['muted_stories']=True
            if stage=='return_ct_exact':payload.update(return_ok=True,return_how='exact_followers',required_mute_verification_complete=True,required_mute_missing_axes=[],like_terminal_status=terminal,like_terminal_reason=reason)
            if stage=='like_verified':payload.update(real_tap_sent=True,fresh_like_verified=True,like_action_state='LIKE_ACTION_PERFORMED_NOW',liked_count=1,candidate_username=name,action_id=v2_action or 'viewer-'+name)
            events.append(dict(id=stage+'-'+name,**common,username=name,source_profile='ct',session_id='session',event_status='success',interaction_status='success',evidence_source='follow60_mainline_post_follow_v3',stage_idempotency_key='follow60:v2:'+ah+':'+stage,event_type=event_type,interaction_type=it,event_at=FOLLOWED_AT,payload=payload))
        self.events[action]=events
        if complete:self.ledgers.append(dict(common,control_id=b['run_id'],binding_kind='mainline',action_id_hash=ah,candidate_username=name,source_profile='ct',business_session_id='session',attempt_id=1,worker_sha=b['candidate_sha'],revision=len(self.ledgers)+1,created_at=FOLLOWED_AT,like_terminal_status=terminal,like_terminal_reason=reason,metadata_safe=dict(schema='FOLLOW60_MAINLINE_CYCLE_LEDGER_V2',binding_kind='mainline',binding_id=b['run_id'],action_id_hash=ah)))
        return action

    def bind_store_context(self, stack, store):
        import auto_restart_device_lock as locks, account_run_control as requests, supabase_client as db
        b=self.binding;end=(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()
        lease={k:b[k] for k in ('device_id','worker_id','request_id','account_id','lease_id')};lease['lease_expires_at']=end
        req=dict(id=b['request_id'],account_id=b['account_id'],run_id=b['run_id'],claimed_by=b['worker_id'],status='running',lease_expires_at=end,metadata_safe={'device_lock_binding':dict(lease,generation=b['lease_id'])})
        stack.enter_context(patch.object(locks,'_load_device_lock',return_value=lease))
        stack.enter_context(patch.object(requests,'get_account_run_request',return_value=req))
        stack.enter_context(patch.object(db,'load_run_row',return_value=dict(id=b['run_id'],account_id=b['account_id'],status='running')))
        stack.enter_context(patch.object(ca,'_REQUESTED',True))
        stack.enter_context(patch.object(ca,'_SESSION',(dict(certification_not_before=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat(),certification_expires_at=end),b,store)))
        return lease


class TwoCandidateDurableCertificationTests(unittest.TestCase):
    """Actual fsynced permit and actual canonical SQLite writer, no admission mock."""
    def setUp(self):
        import follow60_ordering_v2_ledger_v1 as lv
        self.lv=lv
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name).resolve()/'receipts.sqlite'
        p=patch.object(lv,'DEFAULT_PATH',self.path);p.start();self.addCleanup(p.stop)
        self.binding=dict(BINDING,max_candidate_admissions=2)
        from contextlib import ExitStack
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.backend=CanonicalBackendFixture(self.tmp.name,self.binding);self.backend.install(self.stack)
        self.store=ca._PermitStore(self.tmp.name,self.binding);self.addCleanup(self.store.close)
        self.lease=self.backend.bind_store_context(self.stack,self.store)

    def cycle(self, name, action, *, complete=True):
        scope=self.lv.LedgerScope('account','run','request','session','target',action,name)
        store=self.lv.DurableOrderingLedgerV1(scope,path=self.path)
        payloads=[('profile_certified',{'proof_hash':'profile-'+name}),('post_opened',{'verified':True}),
            ('like_verified',{'like_action_state':'LIKE_ACTION_PERFORMED_NOW','real_tap_sent':True,'fresh_like_verified':True,'candidate_username':name,'action_id':action,'stable_proof_hash':'proof-'+name,'media_binding':'media-'+name}),
            ('profile_reentry_verified',{'verified':True}),('follow_pending',{}),
            ('follow_verified',{'tap_sent':True,'follow_state_after':'following','candidate_username':name}),
            ('mute_posts_verified',{'verified':True}),('mute_stories_verified',{'verified':True}),
            ('return_ct_exact',{'verified':True}),('cycle_complete',{'critical_persistence_acknowledged':True,'candidate_username':name})]
        for stage,payload in (payloads if complete else payloads[:-1]):store.apply_receipt(stage,payload)
        self.backend.cycle(name,complete=complete,v2_action=action)
        return store

    def test_two_complete_cycles_third_blocked_zero_transport(self):
        transports=[]
        self.assertEqual(self.store.admit(KEY),'FIRST_ADMISSION_GRANTED');transports.append('alice')
        self.cycle('alice','action-a')
        self.assertEqual(self.store.admit(('run','account','ct','bob')),'SECOND_ADMISSION_GRANTED');transports.append('bob')
        self.cycle('bob','action-b')
        cycles=ca.read_certification_cycles(self.binding,self.store._read()['slots'])
        self.assertEqual([c.scope.action_id for c in cycles],[self.backend.action('alice'),self.backend.action('bob')])
        self.assertEqual(self.store.admit(('run','account','ct','bob')),'SAME_CANDIDATE_REENTRY_GRANTED')
        with self.assertRaises(ca.CertificationStop) as e:
            self.store.admit(('run','account','ct','charlie'));transports.append('charlie')
        self.assertEqual(e.exception.reason,'THIRD_CANDIDATE_BLOCKED')
        self.assertEqual(transports,['alice','bob'])

    def test_missing_or_incomplete_first_cycle_blocks_second(self):
        self.store.admit(KEY);self.cycle('alice','action-a',complete=False)
        with self.assertRaises(ca.CertificationStop) as e:self.store.admit(('run','account','ct','bob'))
        self.assertEqual(e.exception.reason,'PREVIOUS_CYCLE_UNPROVED')
        self.assertEqual(len(self.store._read()['slots']),1)

    def test_old_receipts_cannot_authorize_second(self):
        self.cycle('alice','action-a')
        with self.assertRaises(ca.CertificationStop) as e:self.store.admit(KEY)
        self.assertEqual(e.exception.reason,'STALE_CANDIDATE_INTENT')

    def test_prior_candidate_reentry_rejected_after_second_admission(self):
        self.store.admit(KEY);self.cycle('alice','action-a');self.store.admit(('run','account','ct','bob'))
        with self.assertRaises(ca.CertificationStop) as e:self.store.admit(KEY)
        self.assertEqual(e.exception.reason,'PRIOR_CANDIDATE_REENTRY_BLOCKED')

    def test_restart_never_refunds_slots(self):
        self.store.admit(KEY);self.store.close()
        with self.assertRaises(ca.CertificationStop) as e:ca._PermitStore(self.tmp.name,self.binding)
        self.assertEqual(e.exception.reason,'PERMIT_RESTART_FORBIDDEN')

    def test_concurrent_session_cannot_open_same_permit(self):
        with self.assertRaises(ca.CertificationStop):ca._PermitStore(self.tmp.name,self.binding)
        self.assertEqual(self.store.admit(KEY),'FIRST_ADMISSION_GRANTED')

    def test_changed_first_payload_rejected_after_second(self):
        import sqlite3
        self.store.admit(KEY);self.cycle('alice','action-a');self.store.admit(('run','account','ct','bob'));self.cycle('bob','action-b')
        with sqlite3.connect(self.path) as conn:conn.execute("UPDATE follow60_ordering_v2_receipts SET payload_json='{}' WHERE action_id='action-a' AND stage='return_ct_exact'")
        slots=[{k:v for k,v in x.items() if not k.startswith('completed_')} for x in self.store._read()['slots']]
        with self.assertRaises(ValueError):ca.read_v2_certification_cycles(self.binding,slots)
        self.backend.events[self.backend.action('alice')][-2]['payload']['return_ok']=False
        with self.assertRaises(ValueError):ca.read_certification_cycles(self.binding,self.store._read()['slots'])

    def test_extra_action_rejected(self):
        self.store.admit(KEY);self.cycle('alice','action-a');self.cycle('other','action-x')
        with self.assertRaises(ca.CertificationStop):self.store.admit(('run','account','ct','bob'))

    def test_fresh_tokens_and_memory_between_two_cycles(self):
        import time
        import followers_surface_proof as proof
        import social_memory
        from types import SimpleNamespace
        proof.reset_for_tests();self.addCleanup(proof.reset_for_tests)
        scope=proof.FollowersSurfaceScope('serial','app','account','target','instagram_followers_list')
        tokens=[]
        for name,action in [('alice','action-a'),('bob','action-b')]:
            self.store.admit(('run','account','ct',name))
            # The unchanged opener obtains current-generation geometry after
            # certification admission; this exercises the real token primitive.
            proof.capture_viewport(scope,'<hierarchy/>',[{'username':name,'bounds':{'left':100,'top':700,'right':500,'bottom':820},'row_center':[300,760],'resource_id':'follow_list_username'}],package_name='com.instagram.android',activity_name='.activity.MainTabActivity',captured_at_monotonic=time.monotonic())
            token,reason=proof.issue_row_action_token(scope,name)
            self.assertIsNotNone(token,reason);tokens.append(token)
            self.assertTrue(proof.consume_row_action_token(token)[0])
            self.assertFalse(proof.consume_row_action_token(token)[0])
            self.cycle(name,action)
            proof.invalidate(scope,reason='candidate_cycle_navigation',navigation_changed=True)
        self.assertNotEqual(tokens[0],tokens[1])
        self.assertFalse(proof.consume_row_action_token(tokens[0])[0])
        denied=social_memory.evaluate_follow_eligibility(target_username='alice',source_profile='ct',db_row={'account_id':'account','username':'alice','posts_liked_count':1},runtime_followed={'alice'},runtime_unfollowed=set(),runtime_interacted={'alice'},runtime_skipped=set(),config=SimpleNamespace(SOCIAL_MEMORY_ENABLED=True))
        self.assertFalse(denied.allowed)
        self.assertEqual(len(ca.read_certification_cycles(self.binding,self.store._read()['slots'])),2)

    def test_third_is_blocked_even_without_second_completion(self):
        self.store.admit(KEY);self.cycle('alice','action-a');self.store.admit(('run','account','ct','bob'))
        with self.assertRaises(ca.CertificationStop) as e:self.store.admit(('run','account','ct','charlie'))
        self.assertEqual(e.exception.reason,'THIRD_CANDIDATE_BLOCKED')
        with self.assertRaises(ValueError):ca.read_certification_cycles(self.binding,self.store._read()['slots'])

    def test_terminal_before_first_transport_is_readable(self):
        self.store.terminal(ca.IDENTITY_UNPROVED)
        self.assertEqual(self.store._read()['state'],'TERMINAL')
        with self.assertRaises(ca.CertificationStop):self.store.admit(KEY)



    def test_restart_after_second_slot_is_also_forbidden(self):
        self.store.admit(KEY);self.cycle('alice','action-a');self.store.admit(('run','account','ct','bob'))
        self.store.close()
        with self.assertRaises(ca.CertificationStop) as e:ca._PermitStore(self.tmp.name,self.binding)
        self.assertEqual(e.exception.reason,'PERMIT_RESTART_FORBIDDEN')

    def test_receipt_scope_and_future_time_rejected_without_second_grant(self):
        import sqlite3
        self.store.admit(KEY);self.cycle('alice','action-a')
        with sqlite3.connect(self.path) as conn:
            saved=conn.execute("SELECT created_at_monotonic FROM follow60_ordering_v2_receipts WHERE stage='cycle_complete'").fetchone()[0]
            conn.execute("UPDATE follow60_ordering_v2_receipts SET created_at_monotonic=? WHERE stage='cycle_complete'",(saved+1000,))
        with self.assertRaisesRegex(ValueError,'stale_receipt'):ca.read_v2_certification_cycles(self.binding,self.store._read()['slots'])
        with sqlite3.connect(self.path) as conn:
            conn.execute("UPDATE follow60_ordering_v2_receipts SET created_at_monotonic=? WHERE stage='cycle_complete'",(saved,))
            conn.execute("UPDATE follow60_ordering_v2_receipts SET request_id='different' WHERE stage='cycle_complete'")
        self.backend.ledgers[0]['request_id']='different'
        with self.assertRaises(ca.CertificationStop) as e:self.store.admit(('run','account','ct','bob'))
        self.assertEqual(e.exception.reason,'PREVIOUS_CYCLE_UNPROVED')
        self.assertEqual(len(self.store._read()['slots']),1)


def two_candidate_process(directory, binding, queue=None, hold=None):
    try:
        store=ca._PermitStore(directory,binding)
        grant=store.admit(KEY)
        if queue is None:os._exit(19)
        queue.put(grant)
        if hold is not None:hold.wait(10)
        store.close()
    except ca.CertificationStop as exc:
        if queue is not None:queue.put(exc.reason)
        else:os._exit(20)


class TwoCandidateProcessTests(unittest.TestCase):
    def test_crash_restart_and_concurrent_second_executor_fail_closed(self):
        binding=dict(BINDING,max_candidate_admissions=2)
        ctx=multiprocessing.get_context('spawn')
        with tempfile.TemporaryDirectory() as temp:
            q=ctx.Queue();hold=ctx.Event()
            a=ctx.Process(target=two_candidate_process,args=(temp,binding,q,hold));a.start()
            try:
                self.assertEqual(q.get(timeout=15),'FIRST_ADMISSION_GRANTED')
                b=ctx.Process(target=two_candidate_process,args=(temp,binding,q));b.start();b.join(15)
                self.assertEqual(b.exitcode,0);self.assertEqual(q.get(timeout=2),'PERMIT_INVALID')
            finally:
                hold.set();a.join(15)
                if a.is_alive():a.kill();a.join()
                q.close()
            self.assertEqual(a.exitcode,0)
            with self.assertRaises(ca.CertificationStop) as e:ca._PermitStore(temp,binding)
            self.assertEqual(e.exception.reason,'PERMIT_RESTART_FORBIDDEN')
        with tempfile.TemporaryDirectory() as temp:
            a=ctx.Process(target=two_candidate_process,args=(temp,binding));a.start();a.join(15)
            if a.is_alive():a.kill();a.join();self.fail('child timeout')
            self.assertEqual(a.exitcode,19)
            before=next(Path(temp).glob('*.json')).read_bytes()
            with self.assertRaises(ca.CertificationStop) as e:ca._PermitStore(temp,binding)
            self.assertEqual(e.exception.reason,'PERMIT_RESTART_FORBIDDEN')
            self.assertEqual(next(Path(temp).glob('*.json')).read_bytes(),before)


class TwoCandidateConfigurationTests(unittest.TestCase):
    def configure_fixture(self, mutate=lambda m,b:None, *, verified=True):
        # Only external signature/read RPC responses are fixtures. Exact
        # configure, canonical row validation and durable store execute.
        from contextlib import ExitStack
        import follow60_deployment_gate_v1 as dg
        import auto_restart_device_lock as locks,account_run_control as requests,supabase_client as db
        now=datetime.now(timezone.utc)
        a=dict(permit_id='fixture',nonce='fresh-fixture',candidate_sha=BASE,account_id='76c1dff8-d16a-40cf-83df-8138d2ca5bd4',device_id='ddec36ad-19d8-4f00-9f7d-a8c2d29fe540',serial='RFGL62N646V',package='com.instagram.android',version_name='446.0.0.49.77',certification_not_before=(now-timedelta(minutes=1)).isoformat(),certification_expires_at=(now+timedelta(minutes=2)).isoformat(),max_candidate_admissions=2)
        sql={k:a[k] for k in ('permit_id','nonce','candidate_sha','device_id','serial','package','version_name','max_candidate_admissions')}
        sql.update(account=a['account_id'],app_instance_id='36495109-0581-408c-baf0-d419c5caf534',version_code=385211304,expires_at=a['certification_expires_at'])
        b={k:a[k] for k in ('account_id','device_id','serial','package','version_name','candidate_sha')}
        b.update(run_id='real-generated-run',request_id='real-generated-request',worker_id='fixture-worker',lease_id='fixture-lease')
        m=dict(certified_candidate_sha=BASE,a0_candidate_admission_permits=[a],physical_canary_permits=[sql]);mutate(m,b)
        lease={k:b[k] for k in ('device_id','worker_id','request_id','account_id','lease_id')};lease['lease_expires_at']=(now+timedelta(minutes=3)).isoformat()
        req=dict(id=b['request_id'],run_id=b['run_id'],account_id=b['account_id'],claimed_by=b['worker_id'],status='running',lease_expires_at=lease['lease_expires_at'],metadata_safe={'device_lock_binding':dict(lease,generation=b['lease_id'])})
        run=dict(id=b['run_id'],account_id=b['account_id'],status='running')
        with tempfile.TemporaryDirectory() as t,ExitStack() as stack:
            home=Path(t).resolve();state=home/'Library/Application Support/BotApp/follow60-lock-v3.1/a0-candidate-admissions';state.mkdir(parents=True)
            mp=home/'manifest.json';mp.write_text(json.dumps(m))
            stack.enter_context(patch.object(Path,'home',return_value=home))
            stack.enter_context(patch.object(dg,'verify_deployment_candidate',return_value={'ok':verified}))
            stack.enter_context(patch.object(locks,'_load_device_lock',return_value=lease))
            stack.enter_context(patch.object(requests,'get_account_run_request',return_value=req))
            stack.enter_context(patch.object(db,'load_run_row',return_value=run))
            ca._REQUESTED=False;ca._SESSION=None
            try:
                ca.configure_from_signed_permit(manifest_path=mp,signature_path=home/'fixture.sig',permit_id='fixture',execution_binding=b,state_directory=state)
                ca.validate_execution_context()
                return ca._SESSION[2].limit
            finally:
                if ca._SESSION is not None:ca._SESSION[2].close()
                ca._REQUESTED=False;ca._SESSION=None

    def test_exact_signed_two_and_legacy_one(self):
        self.assertEqual(self.configure_fixture(),2)
        def one(m,b):
            for k in ('a0_candidate_admission_permits','physical_canary_permits'):m[k][0]['max_candidate_admissions']=1
        self.assertEqual(self.configure_fixture(one),1)

    def test_invalid_authority_and_limits(self):
        with self.assertRaises(ca.CertificationStop):self.configure_fixture(verified=False)
        for value in (True,False,0,3,-1,2.0,'2',None):
            def wrong(m,b):
                for k in ('a0_candidate_admission_permits','physical_canary_permits'):m[k][0]['max_candidate_admissions']=value
            with self.subTest(value=value),self.assertRaises(ca.CertificationStop):self.configure_fixture(wrong)

    def test_wrong_exact_tuple_even_when_execution_agrees(self):
        for key in ('account_id','device_id','serial','package','version_name','candidate_sha'):
            def wrong(m,b):
                m['a0_candidate_admission_permits'][0][key]='wrong';b[key]='wrong'
                m['physical_canary_permits'][0]['account' if key=='account_id' else key]='wrong'
            with self.subTest(key=key),self.assertRaises(ca.CertificationStop):self.configure_fixture(wrong)

    def test_collections_must_agree_and_sql_identity_exact(self):
        for key in ('nonce','candidate_sha','account','serial','package','device_id','version_name','app_instance_id','version_code','max_candidate_admissions','expires_at'):
            def wrong(m,b):m['physical_canary_permits'][0][key]='wrong'
            with self.subTest(key=key),self.assertRaises(ca.CertificationStop):self.configure_fixture(wrong)
        def absent(m,b):m['physical_canary_permits']=[]
        with self.assertRaises(ca.CertificationStop):self.configure_fixture(absent)
        def duplicate(m,b):m['physical_canary_permits']*=2
        with self.assertRaises(ca.CertificationStop):self.configure_fixture(duplicate)


class CanonicalCycleAdmissionTests(unittest.TestCase):
    setUp = TwoCandidateDurableCertificationTests.setUp
    cycle = TwoCandidateDurableCertificationTests.cycle

    def read(self):
        return ca.read_certification_cycles(self.binding,self.store._read()['slots'])

    def test_safe_skip_and_verified_in_both_orders_without_synthetic_v2(self):
        self.store.admit(KEY);self.backend.cycle('alice',terminal='safe_skip')
        first=self.read()[0]
        self.assertEqual(first.scope.like_terminal_status,'safe_skip')
        self.assertFalse(first.v2_coverage_proved)
        self.assertFalse(hasattr(first,'stages'))
        self.assertEqual(self.store.admit(('run','account','ct','bob')),'SECOND_ADMISSION_GRANTED')
        self.backend.cycle('bob',terminal='verified')
        self.assertEqual([x.scope.like_terminal_status for x in self.read()],['safe_skip','verified'])
        with self.assertRaises(ca.CertificationStop) as e:self.store.admit(('run','account','ct','third'))
        self.assertEqual(e.exception.reason,'THIRD_CANDIDATE_BLOCKED')

    def test_verified_then_safe_skip_complete_with_distinct_actions(self):
        self.store.admit(KEY);self.cycle('alice','v2-alice')
        self.assertTrue(self.read()[0].v2_coverage_proved)
        self.store.admit(('run','account','ct','bob'));self.backend.cycle('bob',terminal='safe_skip')
        cycles=self.read();self.assertEqual(len({c.scope.action_id for c in cycles}),2)
        self.assertEqual([c.scope.like_terminal_status for c in cycles],['verified','safe_skip'])
        self.assertTrue(cycles[0].v2_coverage_proved);self.assertFalse(cycles[1].v2_coverage_proved)
        self.assertNotEqual(cycles[0].scope.action_id,'v2-alice')
        with self.assertRaises(ca.CertificationStop):self.store.admit(('run','account','ct','third'))

    def test_exact_scope_and_producer_negative_matrix(self):
        import copy
        self.store.admit(KEY);self.backend.cycle('alice')
        action=self.backend.action('alice')
        mutations=[]
        for key in ('control_id','binding_kind','action_id_hash','account_id','run_id','request_id','candidate_username','source_profile','business_session_id','attempt_id','worker_sha','revision','like_terminal_reason','like_terminal_status','metadata_safe'):
            mutations.append(('ledger.'+key,lambda k=key:self.backend.ledgers[0].__setitem__(k,'wrong')))
        for key in ('id','account_id','run_id','request_id','username','source_target_id','source_target_username','source_profile','event_type','event_status','evidence_source'):
            mutations.append(('follow.'+key,lambda k=key:self.backend.events[action][0].__setitem__(k,'wrong')))
        for key in ('account_id','run_id','request_id','source_target_id','interaction_type','follow_status','was_successful','payload','eligible_unfollow_at'):
            mutations.append(('interaction.'+key,lambda k=key:self.backend.interactions['alice'].__setitem__(k,'wrong')))
        for key in ('account_id','run_id','request_id','session_id','username','source_profile','event_type','event_status','evidence_source','stage_idempotency_key'):
            mutations.append(('stage.'+key,lambda k=key:self.backend.events[action][1].__setitem__(k,'wrong')))
        for key in ('schema','stage','action_id_hash','attempt_id','business_session_id','binding_kind','binding_id','control_id','worker_sha','muted_posts'):
            mutations.append(('stage.payload.'+key,lambda k=key:self.backend.events[action][1]['payload'].__setitem__(k,'wrong')))
        original=copy.deepcopy((self.backend.ledgers,self.backend.events,self.backend.interactions))
        for label,change in mutations:
            self.backend.ledgers,self.backend.events,self.backend.interactions=copy.deepcopy(original)
            change()
            with self.subTest(label=label),self.assertRaises((ValueError,TypeError,AttributeError)):self.read()
        self.backend.ledgers,self.backend.events,self.backend.interactions=original
        self.assertEqual(len(self.read()),1)

    def test_intent_negative_matrix_and_stale_run(self):
        self.store.admit(KEY);self.backend.cycle('alice')
        path=self.backend.intent_path('alice');original=json.loads(path.read_text())
        for field in ('account_id','run_id','request_id','worker_sha','business_session_id','attempt_id','source_target_id','source_ct_username','action_id','candidate_username','stage','receipt_schema','settings_revision'):
            bad=dict(original);bad[field]='wrong';path.write_text(json.dumps(bad))
            with self.subTest(field=field),self.assertRaises(ValueError):self.read()
        for field in ('tap_sent','tap_ready_boundary_reached','post_follow_stage_persist_ok','return_ct_exact','receipt_ready_before_critical_rpc'):
            bad=json.loads(json.dumps(original));bad['receipt_metadata_safe'][field]=False;path.write_text(json.dumps(bad))
            with self.subTest(field=field),self.assertRaises(ValueError):self.read()
        path.write_text(json.dumps(original));self.assertEqual(len(self.read()),1)
        stale=dict(self.binding,run_id='old-run')
        with self.assertRaises((ValueError,FileNotFoundError)):ca.read_certification_cycles(stale,self.store._read()['slots'])

    def test_like_terminal_required_not_absence_and_ambiguity_rejected(self):
        import copy
        self.store.admit(KEY);self.backend.cycle('alice',terminal='safe_skip');a=self.backend.action('alice')
        original=copy.deepcopy(self.backend.events[a]);self.assertEqual(len(self.read()),1)
        for key,value in [('like_terminal_status',''),('like_terminal_reason',''),('like_terminal_reason','different'),('return_ok',False),('required_mute_verification_complete',False),('required_mute_missing_axes',['posts']),('like_mutation_ambiguous',True),('real_tap_sent',True)]:
            self.backend.events[a]=copy.deepcopy(original);self.backend.events[a][-1]['payload'][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):self.read()
        self.backend.events[a]=original
        # An independently present Like contradicts safe_skip, never ignored.
        bad=copy.deepcopy(original[-1]);bad['id']='like';bad['payload']['stage']='like_verified';self.backend.events[a].append(bad)
        with self.assertRaises(ValueError):self.read()

    def test_verified_like_requires_current_real_positive_proof(self):
        import copy
        self.store.admit(KEY);self.backend.cycle('alice');a=self.backend.action('alice');original=copy.deepcopy(self.backend.events[a])
        for key,value in [('real_tap_sent',False),('fresh_like_verified',False),('like_action_state','old'),('liked_count',0),('liked_count',True),('candidate_username','other'),('action_id','')]:
            self.backend.events[a]=copy.deepcopy(original);self.backend.events[a][-1]['payload'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):self.read()

    def test_missing_duplicate_extra_and_partial_commit_rows_fail_closed(self):
        import copy
        self.store.admit(KEY);self.backend.cycle('alice');a=self.backend.action('alice');original=copy.deepcopy(self.backend.events[a])
        for i in range(len(original)):
            self.backend.events[a]=copy.deepcopy(original);self.backend.events[a].pop(i)
            with self.subTest(missing=i),self.assertRaises(ValueError):self.read()
        for i in range(len(original)):
            self.backend.events[a]=copy.deepcopy(original)+[copy.deepcopy(original[i])]
            with self.subTest(duplicate=i),self.assertRaises(ValueError):self.read()
        self.backend.events[a]=original
        saved=list(self.backend.ledgers);self.backend.ledgers=[]
        with self.assertRaises(ValueError):self.read()
        self.backend.ledgers=saved*3
        with self.assertRaises(ValueError):self.read()

    def test_completed_hash_detects_same_scope_payload_drift(self):
        self.store.admit(KEY);self.backend.cycle('alice',terminal='safe_skip');self.store.admit(('run','account','ct','bob'));self.backend.cycle('bob',terminal='safe_skip')
        self.backend.events[self.backend.action('alice')][-1]['payload']['return_how']='another_exact_return'
        with self.assertRaisesRegex(ValueError,'completed_cycle_changed'):self.read()

    def test_atomic_retry_same_facts_no_second_consumption(self):
        self.store.admit(KEY);self.backend.cycle('alice',terminal='safe_skip')
        a=self.read()[0].envelope_sha256;b=self.read()[0].envelope_sha256;self.assertEqual(a,b)
        self.assertEqual(self.store.admit(KEY),'SAME_CANDIDATE_REENTRY_GRANTED')
        self.store.admit(('run','account','ct','bob'))
        self.assertEqual(self.store._read()['slots'][0]['completed_envelope_sha256'],a)

    def test_lease_loss_during_read_blocks_reservation(self):
        self.store.admit(KEY);self.backend.cycle('alice',terminal='safe_skip')
        original=self.backend.get
        def read_then_expire(*a,**kw):
            result=original(*a,**kw);self.lease['lease_expires_at']='2020-01-01T00:00:00Z';return result
        import supabase_client as db
        with patch.object(db,'_request_json',side_effect=read_then_expire),self.assertRaises(ca.CertificationStop):self.store.admit(('run','account','ct','bob'))
        self.assertEqual(len(self.store._read()['slots']),1)

    def test_backend_timeout_no_retry_or_slot_grant(self):
        import supabase_client as db
        self.store.admit(KEY);self.backend.cycle('alice')
        with patch.object(db,'_request_json',side_effect=TimeoutError('offline')) as get,self.assertRaises(ca.CertificationStop):self.store.admit(('run','account','ct','bob'))
        self.assertEqual(get.call_count,1);self.assertEqual(len(self.store._read()['slots']),1)

    def test_get_counts_bounded_and_evidence_read_does_not_mutate(self):
        self.store.admit(KEY);self.backend.cycle('alice');before=self.backend.intent_path('alice').read_bytes()
        self.backend.calls.clear();self.read();self.assertEqual(len(self.backend.calls),6)
        self.assertEqual(self.backend.intent_path('alice').read_bytes(),before)
        self.store.admit(('run','account','ct','bob'));self.backend.cycle('bob')
        self.backend.calls.clear();self.read();self.assertEqual(len(self.backend.calls),8)
        self.assertTrue(all(int(q['limit'])<=6 for _,q in self.backend.calls))

    def test_symlink_and_oversized_local_receipt_rejected(self):
        self.store.admit(KEY);self.backend.cycle('alice');path=self.backend.intent_path('alice');content=path.read_bytes();saved=path.with_suffix('.saved');saved.write_bytes(content);path.unlink();path.symlink_to(saved)
        with self.assertRaises(ValueError):self.read()
        path.unlink();path.write_bytes(b' '*(ca._MAX_EVIDENCE_BYTES+1))
        with self.assertRaises(ValueError):self.read()

    def test_projection_drift_rejects_without_repair(self):
        self.store.admit(KEY);self.backend.cycle('alice');self.backend.interactions['alice']['payload']['action_id']='another-action'
        with self.assertRaises(ValueError):self.read()
        self.assertEqual(self.backend.interactions['alice']['payload']['action_id'],'another-action')


class DM446ExactFreezeTests(unittest.TestCase):
    def test_exact_delta_restores_signed_baseline(self):
        source = restore_exact_product_delta('instagram_navigation.py', (ROOT/'instagram_navigation.py').read_text())
        expected = subprocess.check_output(['git','show','ea69818bfb3445dba3459ea56156d3c6206031c5:instagram_navigation.py'],cwd=ROOT,text=True)
        self.assertEqual(restore_exact_dm446_delta(source),expected)

    def test_outer_hash_and_inner_position_checks_reject_mutations(self):
        source = restore_exact_product_delta('instagram_navigation.py', (ROOT/'instagram_navigation.py').read_text())
        for offset, approved, original in DM446_EXACT_HUNKS:
            without = source[:offset]+source[offset+len(approved):]
            mutations = (without,source[:offset]+'# altered\n'+source[offset+1:],approved+without,source+approved)
            for bad in mutations:
                with self.subTest(offset=offset,hash=hashlib.sha256(bad.encode()).hexdigest()):
                    with self.assertRaisesRegex(AssertionError,'dm446_source_changed'):
                        restore_exact_dm446_delta(bad)
                    with patch.dict(globals(),DM446_SOURCE_SHA256=hashlib.sha256(bad.encode()).hexdigest()):
                        with self.assertRaisesRegex(AssertionError,'dm446_delta_shape_changed'):
                            restore_exact_dm446_delta(bad)
        for bad in (source+'\n# unrelated\n',source[:-1]):
            with self.assertRaisesRegex(AssertionError,'dm446_source_changed'):
                restore_exact_dm446_delta(bad)
        wrong = list(DM446_EXACT_HUNKS)
        offset,approved,original=wrong[0]
        wrong[0]=(offset,approved,original+'# wrong baseline\n')
        with patch.dict(globals(),DM446_EXACT_HUNKS=wrong):
            with self.assertRaisesRegex(AssertionError,'dm446_baseline_not_exact'):
                restore_exact_dm446_delta(source)
