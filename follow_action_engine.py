"""
FOLLOW ACTION ENGINE V2 — hybrid follow surface detection (XML + vision heuristics + state).

Used as an additional path for ``profile_already_open`` + visual candidate flows.
Does not replace ``wait_for_follow_button_safe`` for other callers.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from typing import Any

import uiautomator2 as u2

from logs import log

# --- CTA classification (Follow Action Engine); contact/email/call kept for future scraper hooks ---

CTA_TYPE_FOLLOW = "follow"
CTA_TYPE_MESSAGE = "message"
CTA_TYPE_CONTACT = "contact"
CTA_TYPE_CALL = "call"
CTA_TYPE_EMAIL = "email"
CTA_TYPE_WHATSAPP = "whatsapp"
CTA_TYPE_INVITE = "invite"
CTA_TYPE_UNKNOWN = "unknown"

FOLLOW_ENGINE_EXCLUDED_CTA_TYPES: frozenset[str] = frozenset(
    {
        CTA_TYPE_MESSAGE,
        CTA_TYPE_CONTACT,
        CTA_TYPE_CALL,
        CTA_TYPE_EMAIL,
        CTA_TYPE_WHATSAPP,
        CTA_TYPE_INVITE,
    }
)

_FUTURE_CONTACT_SCRAPER = "contact_scraper_candidate"

# Strong substring / pattern hits that must never score as Follow (even with high pill score).
_SECONDARY_CTA_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bwhatsapp\b"),
    re.compile(r"(?i)\bemail\b"),
    re.compile(r"(?i)e-mail"),
    re.compile(r"(?i)courriel"),
    re.compile(r"(?i)\bcall\b"),
    re.compile(r"(?i)\bappel\b"),
    re.compile(r"(?i)appeler"),
    re.compile(r"(?i)\bmessage\b"),
    re.compile(r"(?i)messager"),
    re.compile(r"(?i)\bcontact\b"),
    re.compile(r"(?i)contacter"),
    re.compile(r"(?i)\binvite\b"),
    re.compile(r"(?i)invitation\b"),
    re.compile(r"(?i)suggest"),
    re.compile(r"(?i)learn more"),
    re.compile(r"(?i)\bbook\b"),
    re.compile(r"(?i)\breserve\b"),
)


def classify_follow_control_cta(
    text: str,
    content_desc: str,
    resource_id: str,
) -> tuple[str, str | None]:
    """
    Classify a header / action control for follow vs secondary CTAs.

    Returns (cta_type, future_use) where future_use tags scraper-eligible controls
    without removing them from the hierarchy (contact / email / call).
    """
    t = (text or "").strip()
    d = (content_desc or "").strip()
    r = (resource_id or "").strip()
    tl = t.casefold()
    dl = d.casefold()
    rl = r.casefold()
    blob = f"{tl} {dl} {rl}"

    if re.search(r"(?i)\bwhatsapp\b", blob):
        return CTA_TYPE_WHATSAPP, None
    if re.search(r"(?i)\bemail\b|e-mail|courriel", blob):
        return CTA_TYPE_EMAIL, _FUTURE_CONTACT_SCRAPER
    if re.search(r"(?i)\bcall\b|appeler|\bappel\b", blob):
        return CTA_TYPE_CALL, _FUTURE_CONTACT_SCRAPER
    if re.search(r"(?i)\bmessage\b|messager", blob):
        return CTA_TYPE_MESSAGE, None
    if re.search(r"(?i)\bcontact\b|contacter", blob):
        return CTA_TYPE_CONTACT, _FUTURE_CONTACT_SCRAPER
    if re.search(r"(?i)\binvite\b|invitation\b", blob):
        return CTA_TYPE_INVITE, None
    if re.search(r"(?i)suggest", blob):
        return CTA_TYPE_INVITE, None

    # Resource-id hints (Instagram often encodes action in id).
    if rl:
        if (
            re.search(r"(?i)contact", rl)
            and "follow" not in rl
            and "following" not in rl
        ):
            return CTA_TYPE_CONTACT, _FUTURE_CONTACT_SCRAPER
        if re.search(r"(?i)message|direct|dm_thread|inbox", rl) and "follow" not in rl:
            return CTA_TYPE_MESSAGE, None
        if re.search(r"(?i)call|phone|tel", rl) and "follow" not in rl:
            return CTA_TYPE_CALL, _FUTURE_CONTACT_SCRAPER
        if re.search(r"(?i)email|mail", rl) and "follow" not in rl:
            return CTA_TYPE_EMAIL, _FUTURE_CONTACT_SCRAPER
        if re.search(r"(?i)whatsapp", rl):
            return CTA_TYPE_WHATSAPP, None
        if re.search(r"(?i)invite", rl) and "follow" not in rl:
            return CTA_TYPE_INVITE, None
        if re.search(r"(?i)suggest", rl):
            return CTA_TYPE_INVITE, None

    # Exact primary follow labels (locale).
    if tl == "follow":
        return CTA_TYPE_FOLLOW, None
    if tl == "suivre":
        return CTA_TYPE_FOLLOW, None
    if dl == "follow" or dl == "suivre":
        if "following" not in dl:
            return CTA_TYPE_FOLLOW, None

    if rl and re.search(r"(?i)follow(?!ing)", rl):
        if "requested" in rl or "following" in rl:
            pass
        else:
            return CTA_TYPE_FOLLOW, None

    if _follow_primary_label_match(t, d) and not _secondary_cta_penalty_match(blob):
        return CTA_TYPE_FOLLOW, None

    return CTA_TYPE_UNKNOWN, None


def _secondary_cta_penalty_match(blob: str) -> bool:
    b = blob.casefold()
    for pat in _SECONDARY_CTA_PATTERNS:
        if pat.search(b):
            return True
    return False


def classify_follow_control_from_inf(inf: dict[str, Any]) -> tuple[str, str | None]:
    return classify_follow_control_cta(
        str(inf.get("text") or ""),
        str(inf.get("contentDescription") or ""),
        str(inf.get("resourceName") or ""),
    )


def _is_exact_follow_primary_label(text: str, content_desc: str) -> bool:
    tl = (text or "").strip().casefold()
    dl = (content_desc or "").strip().casefold()
    if tl == "follow" or tl == "suivre":
        return True
    if dl == "follow" or dl == "suivre":
        return True
    return False


def _follow_engine_reject_cta(
    cta_type: str,
    text: str,
    content_desc: str,
    resource_id: str,
) -> bool:
    if cta_type in FOLLOW_ENGINE_EXCLUDED_CTA_TYPES:
        return True
    blob = f"{(text or '').casefold()} {(content_desc or '').casefold()} {(resource_id or '').casefold()}"
    if cta_type != CTA_TYPE_FOLLOW and _secondary_cta_penalty_match(blob):
        return True
    return False


def _log_follow_control_cta_decision(
    *,
    visual_candidate_id: str,
    cta_type: str,
    future_use: str | None,
    text: str,
    content_desc: str,
    resource_id: str,
    score: float,
    harvest_source: str,
    allowed: bool,
) -> None:
    payload = {
        "visual_candidate_id": str(visual_candidate_id or ""),
        "cta_type": cta_type,
        "future_use": future_use,
        "text": (text or "")[:80],
        "content_desc": (content_desc or "")[:80],
        "resource_id": (resource_id or "")[:120],
        "score": round(float(score), 4),
        "harvest_source": harvest_source,
    }
    log("info", "follow_control_cta_classified", **payload)
    if allowed:
        log("info", "follow_control_cta_allowed", **payload)
    else:
        log("info", "follow_control_cta_rejected", **payload)


class _HarvestedFollowControlProxy:
    """
    Minimal UiObject-like surface: ``.info`` for scoring and coordinate ``.click()`` on harvest target.
    """

    __slots__ = ("_d", "_info", "_cx", "_cy")

    def __init__(self, d: u2.Device, info: dict[str, Any], cx: int, cy: int) -> None:
        self._d = d
        self._info = dict(info)
        self._cx = int(cx)
        self._cy = int(cy)

    @property
    def info(self) -> dict[str, Any]:
        return dict(self._info)

    def click(self) -> None:
        self._d.click(self._cx, self._cy)


PROFILE_HEADER_FOLLOW_BUTTON_RES_TOKEN = "profile_header_follow_button"


def _resource_id_is_profile_header_follow_button(rid: str) -> bool:
    return PROFILE_HEADER_FOLLOW_BUTTON_RES_TOKEN in (rid or "").casefold()


def _bounds_dict_from_inf_bounds(b: Any) -> dict[str, int] | None:
    if not isinstance(b, dict):
        return None
    try:
        return {
            "left": int(b["left"]),
            "top": int(b["top"]),
            "right": int(b["right"]),
            "bottom": int(b["bottom"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _snap_click_inside_bounds(bd: dict[str, int], cx: int, cy: int) -> tuple[int, int]:
    """Ensure tap coordinates lie inside bounds (avoids leaking to sibling CTAs)."""
    try:
        l, t, r, bot = bd["left"], bd["top"], bd["right"], bd["bottom"]
    except KeyError:
        return cx, cy
    if r <= l or bot <= t:
        return cx, cy
    mx = (l + r) // 2
    my = (t + bot) // 2
    if l <= cx <= r and t <= cy <= bot:
        return cx, cy
    return mx, my


class _ExactFollowFastPathProxy:
    """
    Instagram ``profile_header_follow_button``: always ``d.click`` on bounds center
    (avoids stale UiObject taps landing on Contact/Message).
    """

    __slots__ = ("_d", "_cx", "_cy", "_info")

    def __init__(
        self,
        d: u2.Device,
        inf: dict[str, Any],
        *,
        visual_candidate_id: str,
    ) -> None:
        self._d = d
        bd_i = _bounds_dict_from_inf_bounds(inf.get("bounds") or {})
        if bd_i is None:
            self._cx, self._cy = 0, 0
        else:
            mx, my = _bounds_center_xy(bd_i)
            self._cx, self._cy = _snap_click_inside_bounds(bd_i, mx, my)
        self._info = dict(inf)
        self._info["bounds"] = dict(bd_i or (inf.get("bounds") or {}))
        self._info["clickable"] = True
        self._info["cta_type"] = CTA_TYPE_FOLLOW
        self._info["exact_follow_fast_path"] = True
        self._info["harvest_source"] = "exact_profile_header_follow_button"
        self._info["visual_candidate_id"] = str(visual_candidate_id or "")

    @property
    def info(self) -> dict[str, Any]:
        return dict(self._info)

    def click(self) -> None:
        self._d.click(self._cx, self._cy)


def try_select_exact_profile_header_follow_fast(
    d: u2.Device,
    ign: Any,
    pkg: str,
    *,
    visual_candidate_id: str,
    ui_snap: str,
    raw_inv: bool,
    screen_class: str | None,
) -> tuple[Any | None, dict[str, Any] | None]:
    """
    One-shot resolve of the official header Follow control by resource-id + exact label.
    Returns (proxy, pick_meta) or (None, None).
    """
    if screen_class is not None and str(screen_class) != "profile_like":
        return None, None
    if ui_snap != "follow" and not raw_inv:
        return None, None
    pkg_use = (pkg or str(getattr(ign.config, "INSTAGRAM_PACKAGE", "") or "")).strip()
    if not pkg_use:
        return None, None
    rid_full = f"{pkg_use}:id/{PROFILE_HEADER_FOLLOW_BUTTON_RES_TOKEN}"
    try:
        sel = d(resourceId=rid_full)
        if not sel.exists(timeout=0.1):
            return None, None
        inf = ign._follow_safe_info(sel)
        tx = str(inf.get("text") or "")
        dc = str(inf.get("contentDescription") or "")
        rn = str(inf.get("resourceName") or "")
        cta, fu = classify_follow_control_cta(tx, dc, rn)
        if cta != CTA_TYPE_FOLLOW or not _is_exact_follow_primary_label(tx, dc):
            return None, None
        if not _resource_id_is_profile_header_follow_button(rn):
            return None, None
        bd_i = _bounds_dict_from_inf_bounds(inf.get("bounds") or {})
        if bd_i is None:
            return None, None
        mx, my = _bounds_center_xy(bd_i)
        sx, sy = _snap_click_inside_bounds(bd_i, mx, my)
        meta = {
            "resource_id": rn,
            "text": tx,
            "content_desc": dc,
            "bounds": dict(bd_i),
            "derived_click_target": [sx, sy],
            "center_x": sx,
            "center_y": sy,
            "cta_type": CTA_TYPE_FOLLOW,
            "future_use": fu,
            "acceptance_mode": "exact_follow_fast",
            "exact_follow_fast_path": True,
        }
        log(
            "info",
            "follow_action_exact_follow_fast_path_selected",
            visual_candidate_id=str(visual_candidate_id or ""),
            bounds=dict(bd_i),
            derived_click_target=[sx, sy],
            center_x=sx,
            center_y=sy,
            resource_id=rn[:120],
            text=tx[:40],
        )
        return (
            _ExactFollowFastPathProxy(
                d, inf, visual_candidate_id=str(visual_candidate_id or "")
            ),
            meta,
        )
    except Exception:
        return None, None


def _parse_android_bounds_attr(bounds: str | None) -> dict[str, int] | None:
    if not bounds:
        return None
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds.strip())
    if not m:
        return None
    l, t, r, b = (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))
    if r <= l or b <= t:
        return None
    return {"left": l, "top": t, "right": r, "bottom": b}


def _bounds_center_xy(bd: dict[str, int]) -> tuple[int, int]:
    return (bd["left"] + bd["right"]) // 2, (bd["top"] + bd["bottom"]) // 2


def _rect_contains_point(bd: dict[str, int], x: int, y: int) -> bool:
    return bd["left"] <= x <= bd["right"] and bd["top"] <= y <= bd["bottom"]


def _rect_iou(a: dict[str, int], b: dict[str, int]) -> float:
    il = max(a["left"], b["left"])
    it = max(a["top"], b["top"])
    ir = min(a["right"], b["right"])
    ib = min(a["bottom"], b["bottom"])
    iw = max(0, ir - il)
    ih = max(0, ib - it)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    aa = max(1, (a["right"] - a["left"]) * (a["bottom"] - a["top"]))
    ba = max(1, (b["right"] - b["left"]) * (b["bottom"] - b["top"]))
    return float(inter) / float(aa + ba - inter)


def _xml_parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    pmap: dict[ET.Element, ET.Element] = {}
    for parent in root.iter():
        for ch in list(parent):
            pmap[ch] = parent
    return pmap


def _follow_primary_label_match(text: str, content_desc: str) -> bool:
    blob = f"{text or ''} {content_desc or ''}".strip().lower()
    if not blob:
        return False
    if any(
        n in blob
        for n in (
            "following",
            "requested",
            "follow back",
            "friends",
            "suivis",
            "demandé",
            "suivie",
            "abonnements",
        )
    ):
        return False
    if re.search(r"(?i)\bfollow\b", blob) and "following" not in blob:
        return True
    if re.search(r"(?i)\bsuivre\b", blob):
        return True
    if re.search(r"(?i)\bfol\b", blob) and "following" not in blob:
        return True
    if re.search(r"(?i)\bsuiv\b", blob) and "suivis" not in blob:
        return True
    return False


def _promote_clickable_target(
    node: ET.Element,
    pmap: dict[ET.Element, ET.Element],
    leaf_bd: dict[str, int],
    *,
    max_hops: int = 16,
) -> tuple[ET.Element, str]:
    """
    Walk ancestors (and one sibling pass) to find a plausible tap target.
    Returns (target_element, harvest_source_tag).
    """
    cx, cy = _bounds_center_xy(leaf_bd)
    cur: ET.Element | None = node
    if (node.get("clickable") or "").lower() == "true":
        pbd = _parse_android_bounds_attr(node.get("bounds"))
        if pbd and _rect_contains_point(pbd, cx, cy):
            return node, "harvested_text"

    hops = 0
    while cur is not None and hops < max_hops:
        parent = pmap.get(cur)
        if parent is None:
            break
        if (parent.get("clickable") or "").lower() == "true":
            pbd = _parse_android_bounds_attr(parent.get("bounds"))
            if pbd and _rect_contains_point(pbd, cx, cy):
                return parent, "harvested_parent"
        cur = parent
        hops += 1

    parent0 = pmap.get(node)
    if parent0 is not None:
        for sib in list(parent0):
            if sib is node:
                continue
            if (sib.get("clickable") or "").lower() != "true":
                continue
            sbd = _parse_android_bounds_attr(sib.get("bounds"))
            if not sbd:
                continue
            mid_y = (leaf_bd["top"] + leaf_bd["bottom"]) // 2
            if abs(((sbd["top"] + sbd["bottom"]) // 2) - mid_y) <= max(24, (leaf_bd["bottom"] - leaf_bd["top"]) * 3):
                if _rect_iou(leaf_bd, sbd) < 0.92:
                    return sib, "harvested_parent"

    return node, "harvested_text"


def _harvest_dict_from_node(
    target: ET.Element,
    *,
    text: str,
    content_desc: str,
    harvest_source: str,
    sw: int,
    sh: int,
) -> dict[str, Any] | None:
    pbd = _parse_android_bounds_attr(target.get("bounds"))
    if not pbd:
        return None
    cx, cy = _bounds_center_xy(pbd)
    cls = str(target.get("class") or target.tag or "")
    rid = str(target.get("resource-id") or "")
    clk = (target.get("clickable") or "").lower() == "true"
    inf = {
        "bounds": dict(pbd),
        "text": text or str(target.get("text") or ""),
        "contentDescription": content_desc or str(target.get("content-desc") or ""),
        "resourceName": rid,
        "clickable": clk,
        "enabled": (target.get("enabled") or "true").lower() == "true",
        "className": cls,
    }
    vscore, _ = _vision_follow_pill_score(inf, screen_w=sw, screen_h=sh)
    src = harvest_source
    if harvest_source == "harvested_parent" and vscore >= 0.34:
        src = "harvested_hybrid"
    elif harvest_source == "harvested_text" and vscore >= 0.36 and cx >= int(sw * 0.40):
        src = "harvested_hybrid"

    return {
        "node_type": cls,
        "text": text or str(target.get("text") or ""),
        "content_desc": content_desc or str(target.get("content-desc") or ""),
        "resource_id": rid,
        "clickable": clk,
        "bounds": dict(pbd),
        "derived_click_target": [cx, cy],
        "harvest_source": src,
    }


def harvest_follow_control_candidates(
    d: u2.Device,
    ign: Any,
    sw: int,
    sh: int,
    *,
    visual_candidate_id: str = "",
) -> list[dict[str, Any]]:
    """
    XML / layout harvest for non-standard Follow controls (RN overlays, parent containers, header pills).

    Each item matches the contract:
    node_type, text, content_desc, resource_id, clickable, bounds, derived_click_target, harvest_source.
    """
    log(
        "info",
        "follow_control_harvest_started",
        visual_candidate_id=str(visual_candidate_id or ""),
        screen_w=int(sw),
        screen_h=int(sh),
    )
    raw = ""
    try:
        raw = d.dump_hierarchy() or ""
    except Exception as e:
        log(
            "warning",
            "follow_control_harvest_result",
            visual_candidate_id=str(visual_candidate_id or ""),
            error=str(e),
            count=0,
        )
        return []

    if not raw.strip():
        log(
            "info",
            "follow_control_harvest_result",
            visual_candidate_id=str(visual_candidate_id or ""),
            count=0,
            note="empty_hierarchy",
        )
        return []

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        log(
            "warning",
            "follow_control_harvest_result",
            visual_candidate_id=str(visual_candidate_id or ""),
            error=str(e),
            count=0,
        )
        return []

    pmap = _xml_parent_map(root)
    out: list[dict[str, Any]] = []
    seen_sig: set[tuple[int, int, int, int]] = set()

    def _push(h: dict[str, Any] | None, *, parent_promoted: bool = False) -> None:
        if not h:
            return
        bd = h.get("bounds") or {}
        if not isinstance(bd, dict):
            return
        try:
            sig = (
                int(bd["left"]),
                int(bd["top"]),
                int(bd["right"]),
                int(bd["bottom"]),
            )
        except (KeyError, TypeError, ValueError):
            return
        for ex in out:
            eb = ex.get("bounds") or {}
            if isinstance(eb, dict) and _rect_iou(bd, eb) > 0.88:
                return
        if sig in seen_sig:
            return

        tx = str(h.get("text") or "")
        cd = str(h.get("content_desc") or "")
        rid = str(h.get("resource_id") or "")
        cta, fu = classify_follow_control_cta(tx, cd, rid)
        h["cta_type"] = cta
        h["future_use"] = fu
        inf_sc: dict[str, Any] = {
            "bounds": bd,
            "text": tx,
            "contentDescription": cd,
            "resourceName": rid,
            "clickable": bool(h.get("clickable")),
            "className": str(h.get("node_type") or ""),
        }
        vscore, _ = _vision_follow_pill_score(inf_sc, screen_w=sw, screen_h=sh)
        hs_src = str(h.get("harvest_source") or "")
        reject = _follow_engine_reject_cta(cta, tx, cd, rid)
        if hs_src == "harvested_header_zone" and cta != CTA_TYPE_FOLLOW:
            reject = True
        allowed = not reject
        _log_follow_control_cta_decision(
            visual_candidate_id=str(visual_candidate_id or ""),
            cta_type=cta,
            future_use=fu,
            text=tx,
            content_desc=cd,
            resource_id=rid,
            score=vscore,
            harvest_source=hs_src,
            allowed=allowed,
        )
        if reject:
            return

        seen_sig.add(sig)
        out.append(h)
        log(
            "info",
            "follow_control_harvest_candidate",
            visual_candidate_id=str(visual_candidate_id or ""),
            harvest_source=h.get("harvest_source"),
            node_type=h.get("node_type"),
            text=(tx[:80]),
            content_desc=(cd[:80]),
            resource_id=(rid[:120]),
            clickable=bool(h.get("clickable")),
            bounds=bd,
            derived_click_target=h.get("derived_click_target"),
            cta_type=cta,
            future_use=fu,
        )
        if parent_promoted:
            log(
                "info",
                "follow_control_harvest_parent_promoted",
                visual_candidate_id=str(visual_candidate_id or ""),
                harvest_source=h.get("harvest_source"),
                bounds=bd,
            )

    for el in root.iter():
        t = str(el.get("text") or "")
        cd = str(el.get("content-desc") or "")
        if not _follow_primary_label_match(t, cd):
            continue
        leaf_bd = _parse_android_bounds_attr(el.get("bounds"))
        if not leaf_bd:
            continue
        tgt, src_tag = _promote_clickable_target(el, pmap, leaf_bd)
        hd = _harvest_dict_from_node(
            tgt,
            text=t,
            content_desc=cd,
            harvest_source=src_tag,
            sw=sw,
            sh=sh,
        )
        _push(hd, parent_promoted=bool(tgt is not el))

    header_band: list[tuple[float, dict[str, Any]]] = []
    for el in root.iter():
        pbd = _parse_android_bounds_attr(el.get("bounds"))
        if not pbd:
            continue
        w = pbd["right"] - pbd["left"]
        h = pbd["bottom"] - pbd["top"]
        cx, cy = _bounds_center_xy(pbd)
        if cy > int(sh * 0.46) or cx < int(sw * 0.34):
            continue
        if cx < int(sw * 0.48) and cy > int(sh * 0.22):
            continue
        if (el.get("clickable") or "").lower() != "true":
            continue
        if w < int(sw * 0.045) or w > int(sw * 0.58):
            continue
        if h < 12 or h > int(sh * 0.13):
            continue
        cls = str(el.get("class") or "")
        if not any(
            k in cls
            for k in (
                "Button",
                "TextView",
                "ImageView",
                "FrameLayout",
                "LinearLayout",
                "ViewGroup",
                "View",
            )
        ):
            continue
        inf = {
            "bounds": dict(pbd),
            "text": str(el.get("text") or ""),
            "contentDescription": str(el.get("content-desc") or ""),
            "resourceName": str(el.get("resource-id") or ""),
            "clickable": True,
            "enabled": True,
            "className": cls,
        }
        vscore, _ = _vision_follow_pill_score(inf, screen_w=sw, screen_h=sh)
        if vscore < 0.26:
            continue
        hd = {
            "node_type": cls,
            "text": str(el.get("text") or ""),
            "content_desc": str(el.get("content-desc") or ""),
            "resource_id": str(el.get("resource-id") or ""),
            "clickable": True,
            "bounds": dict(pbd),
            "derived_click_target": [cx, cy],
            "harvest_source": "harvested_header_zone",
        }
        header_band.append((vscore, hd))

    header_band.sort(key=lambda x: -x[0])
    for vscore, hd in header_band[:6]:
        log(
            "info",
            "follow_control_harvest_header_zone_candidate",
            visual_candidate_id=str(visual_candidate_id or ""),
            vision_score=round(float(vscore), 4),
            bounds=hd.get("bounds"),
            class_name=(str(hd.get("node_type") or ""))[:120],
        )
        _push(hd)

    log(
        "info",
        "follow_control_harvest_result",
        visual_candidate_id=str(visual_candidate_id or ""),
        count=len(out),
        sources={str(x.get("harvest_source") or "") for x in out},
    )
    return out


def _proxy_from_harvest_item(d: u2.Device, h: dict[str, Any]) -> _HarvestedFollowControlProxy:
    bd = h.get("bounds") or {}
    bd_i = _bounds_dict_from_inf_bounds(bd) if isinstance(bd, dict) else None
    dt = h.get("derived_click_target") or [0, 0]
    cx, cy = int(dt[0]), int(dt[1])
    if bd_i is not None and str(h.get("cta_type") or "") == CTA_TYPE_FOLLOW:
        cx, cy = _snap_click_inside_bounds(bd_i, cx, cy)
    inf: dict[str, Any] = {
        "bounds": dict(bd) if isinstance(bd, dict) else {},
        "text": h.get("text") or "",
        "contentDescription": h.get("content_desc") or "",
        "resourceName": h.get("resource_id") or "",
        "clickable": bool(h.get("clickable", True)),
        "enabled": True,
        "className": h.get("node_type") or "",
        "harvest_source": str(h.get("harvest_source") or ""),
        "cta_type": str(h.get("cta_type") or CTA_TYPE_UNKNOWN),
        "future_use": h.get("future_use"),
    }
    if bd_i is not None and str(h.get("cta_type") or "") == CTA_TYPE_FOLLOW:
        inf["derived_click_target"] = [cx, cy]
    return _HarvestedFollowControlProxy(d, inf, cx, cy)


def _vision_follow_pill_score(
    inf: dict[str, Any],
    *,
    screen_w: int,
    screen_h: int,
) -> tuple[float, str]:
    """
    Lightweight non-OCR vision heuristic: pill aspect ratio + header-right placement.
    Returns (score 0..1, reason_tag).
    """
    b = inf.get("bounds") or {}
    try:
        l, t, r, bot = int(b["left"]), int(b["top"]), int(b["right"]), int(b["bottom"])
    except (KeyError, TypeError, ValueError):
        return 0.0, "no_bounds"
    w = max(1, r - l)
    h = max(1, bot - t)
    ar = w / float(h)
    cx = (l + r) // 2
    cy = (t + bot) // 2
    score = 0.0
    reasons: list[str] = []
    if 1.6 <= ar <= 12.0:
        score += 0.38
        reasons.append("pill_aspect")
    if cx >= int(screen_w * 0.38) and cy <= int(screen_h * 0.48):
        score += 0.35
        reasons.append("header_right_band")
    if 8 <= w <= int(screen_w * 0.55) and 10 <= h <= int(screen_h * 0.12):
        score += 0.22
        reasons.append("size_band")
    return min(1.0, score), "+".join(reasons) if reasons else "weak"


def _hybrid_follow_soft_acceptance_gate(
    *,
    ui_snap: str,
    raw_inv: bool,
    nav_state: str,
    screen_class: str | None,
) -> bool:
    if str(screen_class or "") != "profile_like":
        return False
    if nav_state not in ("PROFILE", "CANDIDATE_PROFILE"):
        return False
    return (ui_snap == "follow") or bool(raw_inv)


def _norm_follow_handle(value: Any) -> str:
    return str(value or "").strip().lstrip("@").casefold()


def _pre_follow_context_has_strong_profile_proof(
    ctx: dict[str, Any] | None,
    *,
    username: str,
    source_profile_username: str = "",
) -> bool:
    if not isinstance(ctx, dict):
        return False
    if str(ctx.get("kind") or "") != "pre_follow_tap_context_v1":
        return False
    if _norm_follow_handle(ctx.get("follower_username")) != _norm_follow_handle(username):
        return False
    req_src = _norm_follow_handle(source_profile_username)
    ctx_src = _norm_follow_handle(ctx.get("source_profile_username"))
    if req_src and ctx_src and req_src != ctx_src:
        return False
    try:
        age_s = time.monotonic() - float(ctx.get("captured_at_mono") or 0.0)
    except (TypeError, ValueError):
        return False
    if age_s < 0.0 or age_s > 12.0:
        return False
    if not bool(ctx.get("screen_guard_ok", True)):
        return False
    if str(ctx.get("navigation_state") or "") != "CANDIDATE_PROFILE":
        return False
    if _norm_follow_handle(ctx.get("action_bar_title")) != _norm_follow_handle(username):
        return False
    if str(ctx.get("follow_header_state") or "") != "follow":
        return False
    if bool(ctx.get("requested")) or bool(ctx.get("following")):
        return False
    pg = ctx.get("private_gate") or {}
    if not isinstance(pg, dict):
        return False
    if bool(pg.get("reject")) or bool(pg.get("private_profile_detected")):
        return False
    priv = ctx.get("private_probe_payload") or {}
    if not isinstance(priv, dict) or bool(priv.get("private_profile_detected")):
        return False
    return True


def _ambiguous_prefollow_no_tap_reason(surf: dict[str, Any]) -> str:
    signals = surf.get("signals") if isinstance(surf, dict) else {}
    if not isinstance(signals, dict):
        signals = {}
    if bool(surf.get("follow_available")):
        return ""
    if str(signals.get("screen_class") or "") == "followers_list_strong":
        return "ambiguous_followers_list_strong_no_exact_follow_control"
    if bool(signals.get("raw_follow_invite")):
        return "ambiguous_raw_follow_invite_without_exact_follow_control"
    if str(signals.get("xml_guess") or "") == "likely_profile":
        return "ambiguous_likely_profile_without_exact_follow_control"
    if str(surf.get("reason") or "") == "no_acceptable_follow_control":
        return "ambiguous_no_acceptable_follow_control"
    return ""


def _collect_follow_elements_expanded(
    d: u2.Device, ign: Any, *, visual_candidate_id: str = ""
) -> list[Any]:
    try:
        sw, sh = d.window_size()
    except Exception:
        sw, sh = 1080, 1920

    seen: set[tuple[Any, ...]] = set()
    out: list[Any] = []

    def _sig(el: Any) -> tuple[Any, ...] | None:
        inf = ign._follow_safe_info(el)
        b = inf.get("bounds") or {}
        try:
            return (
                int(b.get("left", -1)),
                int(b.get("top", -1)),
                int(b.get("right", -1)),
                int(b.get("bottom", -1)),
            )
        except (TypeError, ValueError):
            return None

    def _add(el: Any) -> None:
        s = _sig(el)
        if s is None or s in seen:
            return
        seen.add(s)
        out.append(el)

    try:
        for el in ign._follow_collect_elements(d):
            _add(el)
    except Exception:
        pass
    for lab in ("Follow", "Suivre"):
        try:
            for el in d(text=lab).all():
                _add(el)
        except Exception:
            continue
    try:
        for el in d(textContains="Follow").all():
            _add(el)
    except Exception:
        pass
    try:
        for el in d(textContains="Suivre").all():
            _add(el)
    except Exception:
        pass
    for desc_lab in ("Follow", "Suivre"):
        try:
            for el in d(descriptionContains=desc_lab).all():
                _add(el)
        except Exception:
            continue
    try:
        for el in d(description="Follow").all():
            _add(el)
    except Exception:
        pass
    try:
        for el in d(description="Suivre").all():
            _add(el)
    except Exception:
        pass
    for pat in (
        r"(?i).*FrameLayout.*",
        r"(?i).*ViewGroup.*",
        r"(?i).*LinearLayout.*",
    ):
        try:
            for el in d(classNameMatches=pat, textContains="Follow").all():
                _add(el)
        except Exception:
            pass
        try:
            for el in d(classNameMatches=pat, textContains="Suivre").all():
                _add(el)
        except Exception:
            pass
        try:
            for el in d(classNameMatches=pat, descriptionContains="Follow").all():
                _add(el)
        except Exception:
            pass
    try:
        for el in d(clickable=True, textContains="Follow").all():
            _add(el)
    except Exception:
        pass
    try:
        for el in d(clickable=True, textContains="Suivre").all():
            _add(el)
    except Exception:
        pass
    try:
        for el in d(clickable=False, textContains="Follow").all():
            _add(el)
    except Exception:
        pass
    try:
        for el in d(clickable=False, textContains="Suivre").all():
            _add(el)
    except Exception:
        pass

    try:
        harvested = harvest_follow_control_candidates(
            d, ign, sw, sh, visual_candidate_id=str(visual_candidate_id or "")
        )
    except Exception:
        harvested = []
    for h in harvested:
        try:
            _add(_proxy_from_harvest_item(d, h))
        except Exception:
            continue
    return out


def _soft_hybrid_follow_score(
    inf: dict[str, Any],
    *,
    screen_w: int,
    screen_h: int,
    ui_snap: str,
    raw_inv: bool,
    nav_state: str,
    screen_class: str | None,
) -> tuple[float, str]:
    """
    Relaxed scoring for open-profile + strong contextual Follow signals (post-verify still mandatory).
    """
    txt_raw = str(inf.get("text") or "")
    desc_raw = str(inf.get("contentDescription") or "")
    rid_raw = str(inf.get("resourceName") or "")
    blob_pen = f"{txt_raw.casefold()} {desc_raw.casefold()} {rid_raw.casefold()}"

    cta, _fu = classify_follow_control_from_inf(inf)
    if inf.get("cta_type"):
        cta = str(inf.get("cta_type") or cta)
    if _follow_engine_reject_cta(cta, txt_raw, desc_raw, rid_raw):
        return 0.0, f"soft|cta_rejected|type={cta}"
    if _secondary_cta_penalty_match(blob_pen):
        return 0.0, "soft|secondary_cta_penalty"

    b = inf.get("bounds") or {}
    try:
        l, t, r, bot = int(b["left"]), int(b["top"]), int(b["right"]), int(b["bottom"])
    except (KeyError, TypeError, ValueError):
        return 0.0, "no_bounds"
    w = max(1, r - l)
    h = max(1, bot - t)
    cx = (l + r) // 2
    cy = (t + bot) // 2
    if cy < int(screen_h * 0.035) or cy > int(screen_h * 0.58):
        return 0.0, "off_soft_vertical_band"
    if cx < int(screen_w * 0.30):
        return 0.0, "not_header_right"

    ar = w / float(h)
    ar_bonus = 0.14 if 1.2 <= ar <= 14.0 else 0.0

    vscore, vreason = _vision_follow_pill_score(inf, screen_w=screen_w, screen_h=screen_h)
    txt = txt_raw.strip().lower()
    desc = desc_raw.strip().lower()
    blob = f"{txt} {desc}"
    exact_follow = _is_exact_follow_primary_label(txt_raw, desc_raw)

    text_bonus = 0.0
    if exact_follow:
        text_bonus = 0.18
    elif "follow" in blob or "suivre" in blob:
        if "following" in blob or "requested" in blob:
            text_bonus = 0.0
        else:
            text_bonus = 0.09
    elif "fol" in blob or "suiv" in blob:
        text_bonus = 0.03

    strong_follow_context = (ui_snap == "follow" or raw_inv) and str(
        screen_class or ""
    ) == "profile_like"
    follow_only_bonus = 0.14 if exact_follow and strong_follow_context else 0.0

    nav_bonus = 0.12 if nav_state in ("PROFILE", "CANDIDATE_PROFILE", "PRIVATE_PROFILE") else 0.03
    fp_bonus = 0.1 if str(screen_class or "") == "profile_like" else 0.0
    raw_bonus = 0.14 if raw_inv else 0.0
    ui_bonus = 0.0
    if ui_snap == "follow":
        ui_bonus = 0.12 if exact_follow else 0.05
    elif ui_snap != "unknown":
        ui_bonus = 0.03

    rid = rid_raw.lower()
    rid_bonus = 0.06 if "follow" in rid and "following" not in rid and "requested" not in rid else 0.0

    click_nudge = 0.04 if bool(inf.get("clickable")) else 0.0

    hs = str(inf.get("harvest_source") or "")
    harvest_bonus = 0.0
    if exact_follow and cta == CTA_TYPE_FOLLOW:
        if hs == "harvested_text":
            harvest_bonus = 0.095
        elif hs == "harvested_parent":
            harvest_bonus = 0.085
        elif hs == "harvested_header_zone":
            harvest_bonus = 0.11 if raw_inv else 0.06
        elif hs == "harvested_hybrid":
            harvest_bonus = 0.12
    elif hs.startswith("harvested") and not exact_follow:
        harvest_bonus = 0.0

    if not exact_follow and not strong_follow_context:
        vscore = min(vscore, 0.22)

    score = min(
        1.0,
        vscore * 0.42
        + ar_bonus
        + text_bonus
        + follow_only_bonus
        + nav_bonus
        + fp_bonus
        + raw_bonus
        + ui_bonus
        + rid_bonus
        + click_nudge
        + harvest_bonus,
    )
    hb = round(harvest_bonus, 3) if harvest_bonus else 0.0
    fob = round(follow_only_bonus, 3) if follow_only_bonus else 0.0
    return (
        score,
        f"soft|{vreason}|cta={cta}|exact_follow={exact_follow}|ar={round(ar_bonus,3)}|hb={hb}|fob={fob}|hs={hs or '-'}",
    )


def detect_follow_action_surface(
    d: u2.Device,
    *,
    pkg: str,
    username: str = "",
    profile_already_open: bool = False,
    visual_candidate_id: str | None = None,
    source_profile_username: str = "",
) -> dict[str, Any]:
    """
    Observe and score the follow action surface (multi-signal). Does not tap.

    Returns keys including ``follow_control_element`` (UiObject or None) for callers.
    """
    import instagram_navigation as ign

    try:
        sw, sh = d.window_size()
    except Exception:
        sw, sh = 1080, 1920

    ui_snap = ""
    try:
        ui_snap = ign._follow_ui_state_snapshot(d)
    except Exception:
        ui_snap = "unknown"

    raw_inv = False
    try:
        raw_inv = ign._visual_raw_follow_invite_visible_quick(d)
    except Exception:
        raw_inv = False

    follow_back = False
    try:
        follow_back = bool(
            d(textContains="Follow back").exists(timeout=0.04)
            or d(textContains="Suivre en retour").exists(timeout=0.03)
        )
    except Exception:
        follow_back = False

    blocked = False
    try:
        blocked = bool(
            d(textContains="Action blocked").exists(timeout=0.03)
            or d(textContains="Try again later").exists(timeout=0.03)
        )
    except Exception:
        blocked = False

    unavailable = False
    try:
        unavailable = bool(
            d(textContains="User not found").exists(timeout=0.03)
            or d(textContains="Compte introuvable").exists(timeout=0.03)
        )
    except Exception:
        unavailable = False

    nav_state = ""
    nav_conf = 0.0
    xml_guess = ""
    try:
        from navigation_engine import NavigationEngineState, observe_instagram_state

        det = ign.detect_followers_list_screen(
            d, source_profile_username=source_profile_username or ""
        )
        xml_guess = str(det.get("current_screen_guess") or "")
        nav = observe_instagram_state(
            d,
            expected_package=pkg or "",
            last_known_state=NavigationEngineState.CANDIDATE_PROFILE.value,
            context={
                "phase": "follow_action_surface",
                "visual_candidate_id": str(visual_candidate_id or ""),
                "source_profile_username": source_profile_username,
                "disable_followers_visual_fallback": True,
                "expected_state": "CANDIDATE_PROFILE",
                "det": det,
            },
        )
        nav_state = str(nav.get("state") or "")
        nav_conf = float(nav.get("confidence") or 0.0)
    except Exception:
        pass

    fp_sig: dict[str, Any] = {}
    try:
        from screen_fingerprint import build_screen_fingerprint

        meta = ign._followers_current_pkg_activity(d) or {}
        ab = ""
        try:
            ab = ign.read_current_profile_username_for_follow_gate(d)
        except Exception:
            ab = ""
        det2 = ign.detect_followers_list_screen(
            d, source_profile_username=source_profile_username or ""
        )
        fh = ""
        try:
            fh = ign._follow_ui_state_snapshot(d)
        except Exception:
            fh = ""
        fp_sig = build_screen_fingerprint(
            action_bar_title=ab,
            current_package=str(meta.get("current_package") or ""),
            current_activity=str(meta.get("current_activity") or ""),
            visible_texts=None,
            nav_state=nav_state,
            visual_signals={
                "is_followers_list": bool(det2.get("is_followers_list")),
                "strict_list_open": bool(det2.get("strict_list_open")),
                "relaxed_list_open": bool(det2.get("relaxed_list_open")),
                "follow_header_state": fh,
                "raw_follow_invite_visible": raw_inv,
                "nav_confidence": nav_conf,
            },
            xml_guess=xml_guess,
            visual_guess="",
        )
    except Exception:
        fp_sig = {}

    # --- terminal follow states (no tap needed)
    if blocked:
        return {
            "follow_available": False,
            "follow_state": "blocked",
            "confidence": 0.95,
            "signals": {
                "ui_snapshot": ui_snap,
                "raw_follow_invite": raw_inv,
                "nav_state": nav_state,
                "xml_guess": xml_guess,
                "fingerprint_id": fp_sig.get("fingerprint_id"),
            },
            "candidate_buttons": [],
            "best_candidate": None,
            "reason": "follow_surface_blocked",
            "follow_control_element": None,
        }
    if unavailable:
        return {
            "follow_available": False,
            "follow_state": "unavailable",
            "confidence": 0.85,
            "signals": {
                "ui_snapshot": ui_snap,
                "nav_state": nav_state,
                "fingerprint_id": fp_sig.get("fingerprint_id"),
            },
            "candidate_buttons": [],
            "best_candidate": None,
            "reason": "follow_surface_unavailable",
            "follow_control_element": None,
        }
    if follow_back:
        return {
            "follow_available": False,
            "follow_state": "follow_back",
            "confidence": 0.82,
            "signals": {
                "ui_snapshot": ui_snap,
                "raw_follow_invite": raw_inv,
                "nav_state": nav_state,
                "fingerprint_id": fp_sig.get("fingerprint_id"),
            },
            "candidate_buttons": [],
            "best_candidate": None,
            "reason": "follow_back_no_primary_follow_cta",
            "follow_control_element": None,
        }
    if ui_snap == "requested":
        return {
            "follow_available": False,
            "follow_state": "requested",
            "confidence": 0.9,
            "signals": {
                "ui_snapshot": ui_snap,
                "nav_state": nav_state,
                "fingerprint_id": fp_sig.get("fingerprint_id"),
            },
            "candidate_buttons": [],
            "best_candidate": None,
            "reason": "already_requested",
            "follow_control_element": None,
        }
    if ui_snap == "following":
        return {
            "follow_available": False,
            "follow_state": "following",
            "confidence": 0.9,
            "signals": {
                "ui_snapshot": ui_snap,
                "nav_state": nav_state,
                "fingerprint_id": fp_sig.get("fingerprint_id"),
            },
            "candidate_buttons": [],
            "best_candidate": None,
            "reason": "already_following",
            "follow_control_element": None,
        }

    prof_like_sc = str(fp_sig.get("screen_class") or "")
    hybrid_soft_gate = _hybrid_follow_soft_acceptance_gate(
        ui_snap=ui_snap,
        raw_inv=raw_inv,
        nav_state=nav_state,
        screen_class=prof_like_sc,
    )

    elements: list[Any] = []
    try:
        elements = ign._follow_collect_elements(d)
    except Exception:
        elements = []

    candidate_buttons: list[dict[str, Any]] = []
    strict_best_el: Any | None = None
    strict_best_hybrid = -1.0
    strict_best_meta: dict[str, Any] = {}
    exact_profile_header_follow_el: Any | None = None

    for el in elements:
        inf = ign._follow_safe_info(el)
        txe = str(inf.get("text") or "")
        dce = str(inf.get("contentDescription") or "")
        rne = str(inf.get("resourceName") or "")
        cta_s, fu_s = classify_follow_control_from_inf(inf)
        vscore, vreason = _vision_follow_pill_score(inf, screen_w=sw, screen_h=sh)
        if _follow_engine_reject_cta(cta_s, txe, dce, rne) or _secondary_cta_penalty_match(
            f"{txe.casefold()} {dce.casefold()} {rne.casefold()}"
        ):
            _log_follow_control_cta_decision(
                visual_candidate_id=str(visual_candidate_id or ""),
                cta_type=cta_s,
                future_use=fu_s,
                text=txe,
                content_desc=dce,
                resource_id=rne,
                score=float(vscore),
                harvest_source="strict_xml",
                allowed=False,
            )
            bd0 = inf.get("bounds") or {}
            candidate_buttons.append(
                {
                    "score": 0.0,
                    "xml_score": None,
                    "vision_score": round(vscore, 4),
                    "source": "strict_rejected_cta",
                    "bounds": dict(bd0) if isinstance(bd0, dict) else {},
                    "reason": f"cta_rejected:{cta_s}|vision:{vreason}",
                    "resource_id": rne,
                    "text": inf.get("text"),
                    "acceptance_mode": "strict",
                    "cta_type": cta_s,
                    "future_use": fu_s,
                }
            )
            continue

        scored = ign._follow_score_candidate(
            d,
            el,
            username=username,
            header_band_relaxed=bool(profile_already_open),
        )
        score, meta = scored
        xml_part = min(1.0, (float(score or 0) / 220.0)) if score is not None else 0.0
        hybrid = min(1.0, xml_part * 0.62 + vscore * 0.38)
        src = "hybrid"
        if score is None:
            src = "vision" if vscore >= 0.45 else "xml_rejected"
            hybrid = vscore * 0.85
        elif vscore < 0.12:
            src = "xml"
            hybrid = xml_part

        _log_follow_control_cta_decision(
            visual_candidate_id=str(visual_candidate_id or ""),
            cta_type=cta_s,
            future_use=fu_s,
            text=txe,
            content_desc=dce,
            resource_id=rne,
            score=float(hybrid),
            harvest_source="strict_xml",
            allowed=True,
        )
        if (
            cta_s == CTA_TYPE_FOLLOW
            and _is_exact_follow_primary_label(txe, dce)
            and _resource_id_is_profile_header_follow_button(rne)
            and exact_profile_header_follow_el is None
        ):
            exact_profile_header_follow_el = el

        bd = inf.get("bounds") or {}
        rec = {
            "score": round(hybrid, 4),
            "xml_score": score,
            "vision_score": round(vscore, 4),
            "source": src,
            "bounds": dict(bd) if isinstance(bd, dict) else {},
            "reason": f"xml:{meta.get('reject') or 'ok'}|vision:{vreason}",
            "resource_id": str(meta.get("resource_id") or ""),
            "text": meta.get("text"),
            "acceptance_mode": "strict",
            "cta_type": cta_s,
            "future_use": fu_s,
        }
        candidate_buttons.append(rec)

        if hybrid > strict_best_hybrid and score is not None:
            strict_best_hybrid = hybrid
            strict_best_el = el
            strict_best_meta = dict(meta)
            strict_best_meta["hybrid_score"] = hybrid
            strict_best_meta["vision_reason"] = vreason

    strict_threshold = 0.28
    strict_ok = bool(
        strict_best_el is not None and strict_best_hybrid >= strict_threshold
    )

    exact_follow_fast_path = False
    inf_ef_fast: dict[str, Any] | None = None
    exact_pick_meta: dict[str, Any] = {}

    if (
        bool(profile_already_open)
        and exact_profile_header_follow_el is not None
        and (ui_snap == "follow" or raw_inv)
        and prof_like_sc == "profile_like"
    ):
        inf_ef_fast = ign._follow_safe_info(exact_profile_header_follow_el)
        bd_i = _bounds_dict_from_inf_bounds(inf_ef_fast.get("bounds") or {})
        if bd_i is not None:
            sx, sy = _snap_click_inside_bounds(bd_i, *_bounds_center_xy(bd_i))
            exact_pick_meta = {
                "resource_id": str(inf_ef_fast.get("resourceName") or ""),
                "text": str(inf_ef_fast.get("text") or ""),
                "bounds": dict(bd_i),
                "derived_click_target": [sx, sy],
                "center_x": sx,
                "center_y": sy,
                "cta_type": CTA_TYPE_FOLLOW,
                "acceptance_mode": "exact_follow_fast",
            }
            log(
                "info",
                "follow_action_exact_follow_fast_path_selected",
                visual_candidate_id=str(visual_candidate_id or ""),
                bounds=dict(bd_i),
                derived_click_target=[sx, sy],
                center_x=sx,
                center_y=sy,
                resource_id=str(inf_ef_fast.get("resourceName") or "")[:120],
                text=str(inf_ef_fast.get("text") or "")[:40],
                source="strict_xml_pipeline",
            )
            exact_follow_fast_path = True

    best_el: Any | None = strict_best_el
    best_hybrid = float(strict_best_hybrid)
    best_meta: dict[str, Any] = dict(strict_best_meta)
    acceptance_mode = "strict"

    soft_min = float(
        getattr(ign.config, "FOLLOW_ACTION_V2_SOFT_SCORE_MIN", 0.175) or 0.175
    )
    vision_single_min = float(
        getattr(ign.config, "FOLLOW_ACTION_V2_VISION_SINGLE_MIN", 0.14) or 0.14
    )

    if exact_follow_fast_path and inf_ef_fast is not None:
        best_el = _ExactFollowFastPathProxy(
            d,
            inf_ef_fast,
            visual_candidate_id=str(visual_candidate_id or ""),
        )
        best_hybrid = 1.0
        best_meta = {
            "resource_id": exact_pick_meta.get("resource_id"),
            "text": exact_pick_meta.get("text"),
            "hybrid_score": 1.0,
            "vision_reason": "exact_profile_header_follow_button",
            "harvest_source": "exact_profile_header_follow_button",
        }
        acceptance_mode = "exact_follow_fast"
    elif strict_ok:
        acceptance_mode = "strict"
    elif hybrid_soft_gate and (
        not strict_ok or float(strict_best_hybrid) < float(strict_threshold)
    ):
        # Always run full soft pipeline when gate is on and strict did not clear the bar.
        acceptance_mode = "hybrid_soft"
        log(
            "info",
            "follow_action_enter_soft_pipeline",
            visual_candidate_id=str(visual_candidate_id or ""),
            strict_best_hybrid=round(strict_best_hybrid, 4),
            strict_threshold=strict_threshold,
            strict_ok=bool(strict_ok),
        )
        log(
            "info",
            "follow_action_soft_acceptance_enabled",
            visual_candidate_id=str(visual_candidate_id or ""),
            ui_snapshot=ui_snap,
            raw_follow_invite=raw_inv,
            nav_state=nav_state,
            screen_class=prof_like_sc,
            strict_best_hybrid=round(strict_best_hybrid, 4),
        )

        # Do not carry a sub-threshold strict pick into soft / availability.
        best_el = None
        best_hybrid = -1.0
        best_meta = {}

        expanded = _collect_follow_elements_expanded(
            d, ign, visual_candidate_id=str(visual_candidate_id or "")
        )
        log(
            "info",
            "follow_action_soft_pipeline_candidates_count",
            visual_candidate_id=str(visual_candidate_id or ""),
            expanded_count=len(expanded),
            xml_pass_count=len(elements),
        )

        soft_best_el: Any | None = None
        soft_best_score = -1.0
        soft_best_reason = ""
        _rej_logged = 0

        for el in expanded:
            inf = ign._follow_safe_info(el)
            cta_sf, fu_sf = classify_follow_control_from_inf(inf)
            if inf.get("cta_type"):
                cta_sf = str(inf.get("cta_type") or cta_sf)
            if inf.get("future_use") is not None:
                fu_sf = str(inf.get("future_use") or "") or fu_sf
            s_soft, r_soft = _soft_hybrid_follow_score(
                inf,
                screen_w=sw,
                screen_h=sh,
                ui_snap=ui_snap,
                raw_inv=raw_inv,
                nav_state=nav_state,
                screen_class=prof_like_sc,
            )
            bd = inf.get("bounds") or {}
            hs = str(inf.get("harvest_source") or "")
            src_soft = "hybrid_soft"
            if hs.startswith("harvested"):
                src_soft = f"hybrid_soft|{hs}"
            candidate_buttons.append(
                {
                    "score": round(s_soft, 4),
                    "xml_score": None,
                    "vision_score": None,
                    "source": src_soft,
                    "bounds": dict(bd) if isinstance(bd, dict) else {},
                    "reason": r_soft,
                    "resource_id": str(inf.get("resourceName") or ""),
                    "text": inf.get("text"),
                    "acceptance_mode": "hybrid_soft",
                    "harvest_source": hs,
                    "cta_type": cta_sf,
                    "future_use": fu_sf,
                }
            )
            if s_soft < soft_min and _rej_logged < 8:
                _rej_logged += 1
                log(
                    "info",
                    "follow_action_soft_candidate_rejected",
                    visual_candidate_id=str(visual_candidate_id or ""),
                    soft_score=round(s_soft, 4),
                    min_required=soft_min,
                    reason=r_soft,
                )
            if s_soft > soft_best_score:
                soft_best_score = s_soft
                soft_best_el = el
                soft_best_reason = r_soft

        log(
            "info",
            "follow_action_soft_pipeline_best_score",
            visual_candidate_id=str(visual_candidate_id or ""),
            soft_best_score=round(soft_best_score, 4),
            soft_min_required=soft_min,
        )

        if soft_best_el is not None and soft_best_score >= soft_min:
            best_el = soft_best_el
            best_hybrid = soft_best_score
            _binf = ign._follow_safe_info(best_el)
            best_meta = {
                "resource_id": _binf.get("resourceName"),
                "text": _binf.get("text"),
                "hybrid_score": soft_best_score,
                "vision_reason": soft_best_reason,
                "harvest_source": str(_binf.get("harvest_source") or ""),
            }
            log(
                "info",
                "follow_action_soft_candidate_selected",
                visual_candidate_id=str(visual_candidate_id or ""),
                soft_score=round(soft_best_score, 4),
                acceptance_mode=acceptance_mode,
                reason=soft_best_reason,
                harvest_source=str(_binf.get("harvest_source") or ""),
            )
        elif raw_inv and (
            soft_best_el is None or float(soft_best_score) < float(soft_min)
        ):
            # Ultimate fallback: single plausible header-right pill
            plausible: list[tuple[float, Any, str]] = []
            for el in expanded:
                inf = ign._follow_safe_info(el)
                txp = str(inf.get("text") or "")
                dcp = str(inf.get("contentDescription") or "")
                rnp = str(inf.get("resourceName") or "")
                cta_p, _ = classify_follow_control_from_inf(inf)
                if inf.get("cta_type"):
                    cta_p = str(inf.get("cta_type") or cta_p)
                if _follow_engine_reject_cta(cta_p, txp, dcp, rnp):
                    continue
                if _secondary_cta_penalty_match(
                    f"{txp.casefold()} {dcp.casefold()} {rnp.casefold()}"
                ):
                    continue
                vs, vr = _vision_follow_pill_score(inf, screen_w=sw, screen_h=sh)
                ss, sr = _soft_hybrid_follow_score(
                    inf,
                    screen_w=sw,
                    screen_h=sh,
                    ui_snap=ui_snap,
                    raw_inv=raw_inv,
                    nav_state=nav_state,
                    screen_class=prof_like_sc,
                )
                if ss <= 0.0:
                    continue
                comb = max(vs, ss * 0.95)
                if comb >= vision_single_min:
                    plausible.append((comb, el, f"vision_single|{vr}|{sr}"))
            plausible.sort(key=lambda x: -x[0])
            if len(plausible) == 1:
                comb, el_one, rone = plausible[0]
                best_el = el_one
                best_hybrid = float(comb)
                _oinf = ign._follow_safe_info(el_one)
                best_meta = {
                    "resource_id": _oinf.get("resourceName"),
                    "text": _oinf.get("text"),
                    "hybrid_score": best_hybrid,
                    "vision_reason": rone,
                    "harvest_source": str(_oinf.get("harvest_source") or ""),
                }
                acceptance_mode = "vision_soft"
                log(
                    "info",
                    "follow_action_soft_candidate_selected",
                    visual_candidate_id=str(visual_candidate_id or ""),
                    soft_score=round(best_hybrid, 4),
                    acceptance_mode=acceptance_mode,
                    reason="vision_soft_single_candidate_mode",
                    harvest_source=str(_oinf.get("harvest_source") or ""),
                )
            elif len(plausible) > 1:
                log(
                    "info",
                    "follow_action_soft_candidate_rejected",
                    visual_candidate_id=str(visual_candidate_id or ""),
                    reason="vision_soft_multiple_plausible_header_controls",
                    plausible_count=len(plausible),
                )
            else:
                log(
                    "info",
                    "follow_action_soft_pipeline_no_candidate",
                    visual_candidate_id=str(visual_candidate_id or ""),
                    raw_follow_invite=raw_inv,
                    ui_snapshot=ui_snap,
                    soft_best_score=round(soft_best_score, 4),
                )
        else:
            log(
                "info",
                "follow_action_soft_pipeline_no_candidate",
                visual_candidate_id=str(visual_candidate_id or ""),
                raw_follow_invite=raw_inv,
                ui_snapshot=ui_snap,
                soft_best_score=round(soft_best_score, 4),
            )

    candidate_buttons.sort(key=lambda x: float(x.get("score") or 0), reverse=True)

    avail_threshold = (
        soft_min if acceptance_mode in ("hybrid_soft", "vision_soft") else strict_threshold
    )
    follow_available = bool(best_el is not None and best_hybrid >= avail_threshold)

    if hybrid_soft_gate and acceptance_mode == "strict" and not strict_ok:
        log(
            "warning",
            "follow_action_soft_pipeline_bypass_bug",
            visual_candidate_id=str(visual_candidate_id or ""),
            strict_best_hybrid=round(strict_best_hybrid, 4),
            note="gate_active_but_mode_strict_without_strict_ok",
        )

    if ui_snap == "unknown" and raw_inv and best_el is None:
        fs = "ambiguous"
        reason = "raw_invite_without_scored_control"
    elif best_el is None:
        fs = "ambiguous"
        if (
            hybrid_soft_gate
            and raw_inv
            and ui_snap == "follow"
            and prof_like_sc == "profile_like"
            and nav_state in ("PROFILE", "CANDIDATE_PROFILE")
        ):
            reason = "follow_action_soft_pipeline_no_candidate"
        else:
            reason = "no_acceptable_follow_control"
    else:
        fs = "follow"
        reason = (
            "follow_control_ready_soft"
            if acceptance_mode in ("hybrid_soft", "vision_soft")
            else (
                "follow_control_ready_exact_fast"
                if acceptance_mode == "exact_follow_fast"
                else "follow_control_ready"
            )
        )

    best_candidate: dict[str, Any] | None = None
    if best_el is not None:
        _bci = ign._follow_safe_info(best_el)
        _bcta, _bfu = classify_follow_control_from_inf(_bci)
        if _bci.get("cta_type"):
            _bcta = str(_bci.get("cta_type") or _bcta)
        best_candidate = {
            "score": round(best_hybrid, 4),
            "source": acceptance_mode,
            "bounds": dict(_bci.get("bounds") or {}),
            "reason": str(best_meta.get("vision_reason") or "picked"),
            "resource_id": best_meta.get("resource_id"),
            "text": best_meta.get("text"),
            "follow_candidate_acceptance_mode": acceptance_mode,
            "harvest_source": str(best_meta.get("harvest_source") or ""),
            "cta_type": _bcta,
            "future_use": _bfu,
        }

    conf_out = round(max(best_hybrid, 0.15 if raw_inv else 0.0), 4)

    return {
        "exact_follow_fast_path": bool(exact_follow_fast_path),
        "follow_available": follow_available,
        "follow_state": fs,
        "confidence": conf_out,
        "follow_candidate_acceptance_mode": acceptance_mode,
        "signals": {
            "ui_snapshot": ui_snap,
            "raw_follow_invite": raw_inv,
            "nav_state": nav_state,
            "nav_confidence": nav_conf,
            "xml_guess": xml_guess,
            "profile_already_open": bool(profile_already_open),
            "visual_candidate_id": str(visual_candidate_id or ""),
            "fingerprint_id": fp_sig.get("fingerprint_id"),
            "screen_class": fp_sig.get("screen_class"),
            "xml_candidate_count": len(elements),
            "hybrid_follow_soft_acceptance_gate": hybrid_soft_gate,
            "follow_candidate_acceptance_mode": acceptance_mode,
            "strict_best_hybrid": round(strict_best_hybrid, 4),
            "strict_threshold": strict_threshold,
            "exact_follow_fast_path": bool(exact_follow_fast_path),
        },
        "candidate_buttons": candidate_buttons[:32],
        "best_candidate": best_candidate,
        "reason": reason,
        "follow_control_element": best_el,
    }


def follow_action_surface_wait_and_select_element(
    d: u2.Device,
    username: str,
    pkg: str,
    *,
    visual_candidate_id: str,
    source_profile_username: str = "",
    initial_ui_state: str | None = None,
    pre_follow_context: dict[str, Any] | None = None,
) -> tuple[Any | None, dict[str, Any]]:
    """
    Poll with recovery micro-adjustments until a follow control is selected or timeout.
    Return shape compatible with ``wait_for_follow_button_safe`` (element, meta dict + events).
    """
    import instagram_navigation as ign

    events: list[tuple[str, dict[str, Any]]] = []

    def _emit(name: str, payload: dict[str, Any]) -> None:
        pl = dict(payload)
        events.append((name, pl))
        log("info", name, **pl)

    pkg = pkg or str(getattr(ign.config, "INSTAGRAM_PACKAGE", "") or "")
    deadline = time.monotonic() + float(
        getattr(ign.config, "FOLLOW_BUTTON_WAIT_S", 4.0) or 4.0
    )
    poll_s = float(getattr(ign.config, "POLL_INTERVAL_S", 0.08) or 0.08)
    recovery_attempts = 0
    max_recovery = int(getattr(ign.config, "FOLLOW_ACTION_V2_MAX_RECOVERY", 3) or 3)
    last_ui = "unknown"
    try:
        sw, sh = d.window_size()
    except Exception:
        sw, sh = 1080, 1920

    started_at = time.perf_counter()
    attempt = 0
    exact_selector_absent_after_reused_proof = False
    strong_profile_context = _pre_follow_context_has_strong_profile_proof(
        pre_follow_context,
        username=username,
        source_profile_username=source_profile_username,
    )
    while time.monotonic() < deadline:
        attempt += 1
        _fg_t0 = time.perf_counter()
        _fg_ok = ign.verify_app_foreground(d, pkg)
        _emit(
            "follow_action_timing_foreground_check_completed",
            {
                "duration_ms": round((time.perf_counter() - _fg_t0) * 1000.0, 2),
                "caller": "follow_action_surface_wait_and_select_element",
                "result": bool(_fg_ok),
                "visual_candidate_id": str(visual_candidate_id or ""),
                "attempt": attempt,
                "fallback_used": False,
            },
        )
        if not _fg_ok:
            time.sleep(poll_s)
            continue

        _ui_reused = bool(
            attempt == 1
            and str(initial_ui_state or "").strip() == "follow"
        )
        _ui_t0 = time.perf_counter()
        if _ui_reused:
            ui_q = "follow"
        else:
            try:
                ui_q = ign._follow_ui_state_snapshot(d)
            except Exception:
                ui_q = "unknown"
        _emit(
            "pre_follow_timing_ui_state_snapshot_completed",
            {
                "duration_ms": round((time.perf_counter() - _ui_t0) * 1000.0, 2),
                "caller": "follow_action_engine",
                "result": ui_q,
                "signals_found": [ui_q] if ui_q else [],
                "visual_candidate_id": str(visual_candidate_id or ""),
                "source_profile_username": str(source_profile_username or ""),
                "follow_button": ui_q == "follow",
                "following": ui_q == "following",
                "requested": ui_q == "requested",
                "private": False,
                "message_or_contact": False,
                "follow_header_state_reused": _ui_reused,
                "fallback_used": not _ui_reused,
                "attempt": attempt,
            },
        )
        _raw_t0 = time.perf_counter()
        try:
            raw_q = ign._visual_raw_follow_invite_visible_quick(d)
        except Exception:
            raw_q = False
        _raw_ms = round((time.perf_counter() - _raw_t0) * 1000.0, 2)

        _exact_t0 = time.perf_counter()
        _opened_to_exact_probe_ms = None
        try:
            captured_at = float((pre_follow_context or {}).get("captured_at_mono") or 0.0)
            if captured_at > 0.0:
                _opened_to_exact_probe_ms = round((time.monotonic() - captured_at) * 1000.0, 2)
        except (TypeError, ValueError):
            _opened_to_exact_probe_ms = None
        _emit(
            "exact_probe_started",
            {
                "caller": "follow_action_surface_wait_and_select_element",
                "visual_candidate_id": str(visual_candidate_id or ""),
                "source_profile_username": str(source_profile_username or ""),
                "attempt": attempt,
                "follow_header_state": ui_q,
                "follow_header_state_reused": _ui_reused,
                "raw_follow_invite_visible": bool(raw_q),
                "strong_profile_context": bool(strong_profile_context),
                "opened_to_exact_probe_ms": _opened_to_exact_probe_ms,
            },
        )
        probe_el, probe_meta = try_select_exact_profile_header_follow_fast(
            d,
            ign,
            pkg,
            visual_candidate_id=str(visual_candidate_id or ""),
            ui_snap=ui_q,
            raw_inv=raw_q,
            screen_class="profile_like" if strong_profile_context else "__unproved_profile__",
        )
        _exact_ms = round((time.perf_counter() - _exact_t0) * 1000.0, 2)
        _emit(
            "follow_action_timing_exact_probe_completed",
            {
                "duration_ms": _exact_ms,
                "caller": "follow_action_surface_wait_and_select_element",
                "result": bool(probe_el is not None),
                "reason": "exact_profile_header_follow_found"
                if probe_el is not None
                else "exact_profile_header_follow_absent",
                "signals_found": ["exact_profile_header_follow_button"]
                if probe_el is not None
                else [],
                "visual_candidate_id": str(visual_candidate_id or ""),
                "source_profile_username": str(source_profile_username or ""),
                "follow_header_state": ui_q,
                "raw_follow_invite_visible": bool(raw_q),
                "raw_invite_duration_ms": _raw_ms,
                "opened_to_exact_probe_ms": _opened_to_exact_probe_ms,
                "attempt": attempt,
                "fallback_used": probe_el is None,
            },
        )
        _emit(
            "exact_probe_found" if probe_el is not None else "exact_probe_absent",
            {
                "caller": "follow_action_surface_wait_and_select_element",
                "visual_candidate_id": str(visual_candidate_id or ""),
                "source_profile_username": str(source_profile_username or ""),
                "attempt": attempt,
                "bounds": dict((probe_meta or {}).get("bounds") or {}),
                "bounds_present": bool((probe_meta or {}).get("bounds")),
                "exact_follow_fast_path": bool(probe_el is not None),
                "opened_to_exact_probe_ms": _opened_to_exact_probe_ms,
            },
        )
        if probe_el is not None and strong_profile_context:
            pm = probe_meta or {}
            _emit(
                "follow_action_best_candidate",
                {
                    "visual_candidate_id": str(visual_candidate_id or ""),
                    "score": 1.0,
                    "source": "exact_follow_fast",
                    "reason": "resource_id_probe",
                    "resource_id": pm.get("resource_id"),
                    "follow_candidate_acceptance_mode": "exact_follow_fast",
                },
            )
            _emit(
                "follow_action_timing_surface_selection_completed",
                {
                    "duration_ms": round((time.perf_counter() - started_at) * 1000.0, 2),
                    "caller": "follow_action_surface_wait_and_select_element",
                    "result": "ready",
                    "reason": "exact_follow_fast_path_selected",
                    "visual_candidate_id": str(visual_candidate_id or ""),
                    "source_profile_username": str(source_profile_username or ""),
                    "attempt": attempt,
                    "fallback_used": False,
                    "exact_follow_fast_path": True,
                },
            )
            return probe_el, {
                "outcome": "ready",
                "pick_meta": pm,
                "events": events,
                "surface": None,
                "exact_follow_fast_path": True,
                "prefollow_profile_proof_reused": True,
            }
        if (
            probe_el is None
            and strong_profile_context
            and (ui_q == "follow" or raw_q)
        ):
            reason_exact_absent = "profile_proof_exact_follow_control_absent"
            _emit(
                "pre_follow_observation_proof_invalidated",
                {
                    "caller": "follow_action_surface_wait_and_select_element",
                    "visual_candidate_id": str(visual_candidate_id or ""),
                    "source_profile_username": str(source_profile_username or ""),
                    "proof_reused": False,
                    "proof_age_ms": _opened_to_exact_probe_ms,
                    "blocks_avoided": [],
                    "invalidation_reason": reason_exact_absent,
                    "full_fallback_used": True,
                    "attempt": attempt,
                },
            )
            strong_profile_context = False
            exact_selector_absent_after_reused_proof = True

        _surface_t0 = time.perf_counter()
        surf = detect_follow_action_surface(
            d,
            pkg=pkg,
            username=username,
            profile_already_open=True,
            visual_candidate_id=visual_candidate_id,
            source_profile_username=source_profile_username,
        )
        _surface_ms = round((time.perf_counter() - _surface_t0) * 1000.0, 2)
        last_ui = str(surf.get("follow_state") or "unknown")

        _emit(
            "follow_action_surface_detected",
            {
                "follow_available": bool(surf.get("follow_available")),
                "follow_state": surf.get("follow_state"),
                "confidence": surf.get("confidence"),
                "reason": surf.get("reason"),
                "visual_candidate_id": str(visual_candidate_id or ""),
                "follow_candidate_acceptance_mode": surf.get(
                    "follow_candidate_acceptance_mode"
                ),
                "signals": surf.get("signals"),
                "exact_follow_fast_path": bool(surf.get("exact_follow_fast_path")),
            },
        )
        _emit(
            "follow_action_timing_surface_probe_completed",
            {
                "duration_ms": _surface_ms,
                "caller": "follow_action_surface_wait_and_select_element",
                "result": str(surf.get("reason") or ""),
                "visual_candidate_id": str(visual_candidate_id or ""),
                "source_profile_username": str(source_profile_username or ""),
                "follow_state": surf.get("follow_state"),
                "follow_available": bool(surf.get("follow_available")),
                "exact_follow_fast_path": bool(surf.get("exact_follow_fast_path")),
                "attempt": attempt,
                "fallback_used": True,
            },
        )

        for cb in (surf.get("candidate_buttons") or [])[:6]:
            if not isinstance(cb, dict):
                continue
            _emit(
                "follow_action_candidate_scored",
                {
                    "visual_candidate_id": str(visual_candidate_id or ""),
                    "score": cb.get("score"),
                    "source": cb.get("source"),
                    "reason": cb.get("reason"),
                    "resource_id": cb.get("resource_id"),
                },
            )

        if surf.get("follow_state") in ("following", "requested"):
            _emit(
                "follow_action_timing_surface_selection_completed",
                {
                    "duration_ms": round((time.perf_counter() - started_at) * 1000.0, 2),
                    "caller": "follow_action_surface_wait_and_select_element",
                    "result": "already_connected",
                    "reason": str(surf.get("follow_state") or ""),
                    "visual_candidate_id": str(visual_candidate_id or ""),
                    "source_profile_username": str(source_profile_username or ""),
                    "attempt": attempt,
                    "fallback_used": True,
                    "exact_follow_fast_path": bool(surf.get("exact_follow_fast_path")),
                },
            )
            return None, {
                "outcome": "already_connected",
                "ui_state": surf.get("follow_state"),
                "already_following": True,
                "events": events,
                "surface": surf,
            }

        if exact_selector_absent_after_reused_proof:
            _emit(
                "follow_action_ambiguous_surface_fail_fast",
                {
                    "visual_candidate_id": str(visual_candidate_id or ""),
                    "source_profile_username": str(source_profile_username or ""),
                    "reason": "profile_proof_exact_follow_control_absent",
                    "surface_reason": surf.get("reason"),
                    "signals": surf.get("signals"),
                    "safe_to_tap": False,
                    "exact_follow_fast_path": False,
                    "attempt": attempt,
                },
            )
            return None, {
                "outcome": "not_found",
                "last_ui_state": str(surf.get("follow_state") or "ambiguous"),
                "events": events,
                "surface": surf,
                "visual_follow_failure_reason": "profile_proof_exact_follow_control_absent",
                "safe_to_tap": False,
            }

        el_pick = surf.get("follow_control_element")
        ambiguous_no_tap_reason = _ambiguous_prefollow_no_tap_reason(surf)
        if ambiguous_no_tap_reason:
            _emit(
                "follow_action_ambiguous_surface_fail_fast",
                {
                    "visual_candidate_id": str(visual_candidate_id or ""),
                    "source_profile_username": str(source_profile_username or ""),
                    "reason": ambiguous_no_tap_reason,
                    "surface_reason": surf.get("reason"),
                    "signals": surf.get("signals"),
                    "safe_to_tap": False,
                    "exact_follow_fast_path": False,
                    "attempt": attempt,
                },
            )
            _emit(
                "follow_action_timing_surface_selection_completed",
                {
                    "duration_ms": round((time.perf_counter() - started_at) * 1000.0, 2),
                    "caller": "follow_action_surface_wait_and_select_element",
                    "result": "not_found",
                    "reason": ambiguous_no_tap_reason,
                    "visual_candidate_id": str(visual_candidate_id or ""),
                    "source_profile_username": str(source_profile_username or ""),
                    "attempt": attempt,
                    "fallback_used": False,
                    "exact_follow_fast_path": False,
                    "safe_to_tap": False,
                },
            )
            return None, {
                "outcome": "not_found",
                "last_ui_state": str(surf.get("follow_state") or "ambiguous"),
                "events": events,
                "surface": surf,
                "visual_follow_failure_reason": ambiguous_no_tap_reason,
                "safe_to_tap": False,
            }
        if surf.get("follow_available") and el_pick is not None:
            bc = surf.get("best_candidate") or {}
            _emit(
                "follow_action_best_candidate",
                {
                    "visual_candidate_id": str(visual_candidate_id or ""),
                    "score": bc.get("score"),
                    "source": bc.get("source"),
                    "reason": bc.get("reason"),
                    "resource_id": bc.get("resource_id"),
                    "follow_candidate_acceptance_mode": surf.get(
                        "follow_candidate_acceptance_mode"
                    ),
                },
            )
            _emit(
                "follow_action_timing_surface_selection_completed",
                {
                    "duration_ms": round((time.perf_counter() - started_at) * 1000.0, 2),
                    "caller": "follow_action_surface_wait_and_select_element",
                    "result": "ready",
                    "reason": str(bc.get("reason") or surf.get("reason") or ""),
                    "visual_candidate_id": str(visual_candidate_id or ""),
                    "source_profile_username": str(source_profile_username or ""),
                    "attempt": attempt,
                    "fallback_used": True,
                    "exact_follow_fast_path": bool(surf.get("exact_follow_fast_path")),
                },
            )
            return el_pick, {
                "outcome": "ready",
                "pick_meta": surf.get("best_candidate") or {},
                "events": events,
                "surface": surf,
                "exact_follow_fast_path": bool(surf.get("exact_follow_fast_path")),
            }

        _emit(
            "follow_action_retry",
            {
                "visual_candidate_id": str(visual_candidate_id or ""),
                "recovery_attempt": recovery_attempts,
                "surface_reason": surf.get("reason"),
            },
        )
        if recovery_attempts < max_recovery:
            recovery_attempts += 1
            skip_micro_scroll = bool(
                ui_q == "follow"
                or raw_q
                or bool(surf.get("exact_follow_fast_path"))
            )
            if not skip_micro_scroll:
                try:
                    sx = int(sw // 2)
                    y0 = int(sh * 0.21)
                    y1 = int(sh * 0.17)
                    d.swipe(sx, y0, sx, y1, 0.06)
                except Exception:
                    pass
                try:
                    ign.followers_force_hierarchy_refresh(
                        d, source_profile_username or None
                    )
                except Exception:
                    pass
                time.sleep(0.22)
        time.sleep(poll_s)

    _emit(
        "follow_action_failed",
        {
            "visual_candidate_id": str(visual_candidate_id or ""),
            "last_ui_state": last_ui,
            "wait_s": round(
                float(getattr(ign.config, "FOLLOW_BUTTON_WAIT_S", 4.0)), 3
            ),
        },
    )
    _emit(
        "follow_action_timing_surface_selection_completed",
        {
            "duration_ms": round((time.perf_counter() - started_at) * 1000.0, 2),
            "caller": "follow_action_surface_wait_and_select_element",
            "result": "not_found",
            "reason": str(last_ui or "timeout"),
            "visual_candidate_id": str(visual_candidate_id or ""),
            "source_profile_username": str(source_profile_username or ""),
            "attempt": attempt,
            "fallback_used": True,
            "exact_follow_fast_path": False,
        },
    )
    return None, {
        "outcome": "not_found",
        "last_ui_state": last_ui,
        "events": events,
    }
