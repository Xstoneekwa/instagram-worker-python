"""446 read-only composer evidence extracted from qualified Phase B/B1.
No popup action, destination validator, history semantics or Send authority.
"""
from __future__ import annotations
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
SCHEMAS = json.loads(Path(__file__).with_name("dm_ui_446_composer_shapes.json").read_text())
FLAGS = ("class", "clickable", "enabled", "focusable", "long-clickable", "scrollable", "password", "selected", "checkable", "checked")
COMPOSER = "row_thread_composer_edittext"


def _role(n, package):
    rid = n.get("resource-id", "")
    return rid.replace(package + ":id/", "app:id/", 1) if rid.startswith(package + ":id/") else rid


def surface_shape(root, package, kind):
    """Schema projection. Only composer message-list *contents* are opaque.

    Their existence/ancestry is validated; history belongs exclusively to A.
    Other descendants, including nested overlays, remain in the role graph.
    """
    def visit(n):
        if n.tag == "hierarchy":
            return ["hierarchy", [visit(c) for c in n]]
        role = _role(n, package)
        flags = [n.get(k, "") for k in FLAGS]
        if kind == "composer" and role == "app:id/row_thread_composer_send_button_container":
            flags[2] = "draft-dependent"
        label = [n.get("text", ""), n.get("content-desc", "")] if kind in {"map", "instants"} else []
        children, previous = [], None
        for c in ([] if kind == "composer" and role == "app:id/message_list" else n):
            signature = {k: v for k, v in c.attrib.items() if k not in {"index", "drawing-order"}}
            duplicate = (kind == "composer" and not len(c) and c.get("class") == "android.widget.TextView"
                         and c.get("clickable") == c.get("long-clickable") == "false" and signature == previous)
            if not duplicate:
                children.append(visit(c))
            previous = signature
        return [role, flags, label, children]
    return visit(root)


def _rect(n):
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.get("bounds", ""))
    if not match:
        return None
    b = tuple(map(int, match.groups()))
    return b if b[0] < b[2] and b[1] < b[3] else None


def _fits(inner, outer):
    return bool(inner and outer and outer[0] <= inner[0] < inner[2] <= outer[2]
                and outer[1] <= inner[1] < inner[3] <= outer[3])


def _parse(xml, package, kind):
    root = ET.fromstring(xml)
    if (root.tag != "hierarchy" or len(root) != 1 or set(root.attrib) - {"rotation"}
            or root.get("rotation", "0") != "0" or (root.text or "").strip()):
        return None
    pending = [(root[0], None, False)]
    while pending:
        n, parent, inbox_rows = pending.pop()
        b = _rect(n)
        if (n.tag != "node" or n.get("package") != package or not b
                or n.get("visible-to-user") == "false"
                or n.get("focused", "false") not in {"true", "false"}
                or n.get("enabled") not in {"true", "false"}
                or (parent and not _fits(b, parent) and not (
                    kind == "inbox" and inbox_rows and not n.get("resource-id")
                    and n.get("class") == "android.view.View"
                    and b[:3] == parent[:3] and _fits(b, _rect(root[0]))))):
            return None
        if kind == "composer" and _role(n, package) == "app:id/message_list":
            continue  # Existing bubbles may be clipped. Not history evidence.
        inbox_rows = inbox_rows or _role(n, package) == "app:id/inbox_refreshable_thread_list_recyclerview"
        pending.extend((c, b, inbox_rows) for c in n)
    if surface_shape(root, package, kind) not in SCHEMAS[kind]:
        return None
    return root


def _one(root, package, suffix):
    matches = [n for n in root.iter("node") if n.get("resource-id") == package + ":id/" + suffix]
    return matches[0] if len(matches) == 1 else None


def _display_rect(display_size):
    if (not isinstance(display_size, (tuple, list)) or len(display_size) != 2
            or any(type(v) is not int or v <= 0 for v in display_size)):
        return None
    return (0, 0, display_size[0], display_size[1])


def _display_anchor(root, display_size):
    """Only the observed fullscreen coordinate system is qualified (no slack)."""
    display = _display_rect(display_size)
    return bool(display and _rect(root[0]) == display)


def _history_can_occlude(root, package, composer_bounds):
    """Geometry/interaction authority only. Never interpret message contents.

    The recycler itself extends under the composer in real fixtures; its
    descendants must not introduce an unqualified surface over that composer.
    Hidden subtrees are excluded, but unfamiliar names/packages are not exempt.
    """
    message_list = _one(root, package, "message_list")
    if message_list is None:
        return True
    pending = list(message_list)
    while pending:
        n = pending.pop()
        if n.get("visible-to-user") == "false":
            continue
        if "dialog" in n.get("class", "").lower():
            return True
        bounds = _rect(n)
        if bounds is None:
            return True  # Visibility/position cannot be qualified.
        elif (bounds[0] < composer_bounds[2] and composer_bounds[0] < bounds[2]
              and bounds[1] < composer_bounds[3] and composer_bounds[1] < bounds[3]):
            # No arbitrary "significant area" threshold or RID blacklist. Even
            # inert views can draw over text. A visible descendant intersecting
            # the editor is not certified safe; none of the real rows do this.
            return True
        pending.extend(n)
    return False


def composer_evidence(xml, package, *, display_size=None, surface_context=None):
    """Fresh positive widget proof; never an EMPTY proof or cached selector."""
    failure = {"visible": False, "reason": "unqualified_composer_context"}
    try:
        if surface_context is not None and tuple(display_size or ()) == surface_context[2:]:
            from dm_ui_446_surface import qualify
            extension = qualify(ET.fromstring(xml), package, None, surface_context)
            if extension is not None:
                return {"visible": True, "reason": "contextual_composer", "context": "thread",
                        **{k: extension[k] for k in ("bounds", "app_viewport", "window_bounds", "focused")},
                        "surface_context": surface_context,
                        "ime_type": "not_exposed_by_app_xml", "ime_visible": "not_exposed_by_app_xml"}
        root = _parse(xml, package, "composer")
        if root is None:
            return failure
        if not _display_anchor(root, display_size):
            return {"visible": False, "reason": "unqualified_display_anchor"}
        ed = _one(root, package, COMPOSER)
        viewport = _one(root, package, "direct_thread_content_below_action_bar")
        row = _one(root, package, "row_thread_composer_container")
        if ed is None or viewport is None or row is None:
            return failure
        if (ed.get("hint") not in {"Message…", "Message..."}
                or ed.get("content-desc", "")
                or ed.get("enabled") != "true" or ed.get("clickable") != "true"
                or ed.get("focusable") != "true"
                or _rect(row)[3] != _rect(viewport)[3]):
            return failure
        if _history_can_occlude(root, package, _rect(ed)):
            return {"visible": False, "reason": "unqualified_composer_occlusion"}
        header = _one(root, package, "direct_thread_header")
        picker = _one(root, package, "direct_recipients_selected_section")
        if header is not None and picker is None:
            titles = [_one(root, package, k) for k in ("header_title", "header_subtitle")]
            values = [n.get("text", "") or n.get("content-desc", "") for n in titles if n is not None]
            if not any(values) or any(v.strip().lower() in {"search", "meta ai", "search or ask meta ai"} for v in values):
                return failure
            context = "thread"
        elif picker is not None and header is None:
            title = _one(root, package, "action_bar_title")
            recipients = _one(root, package, "recipients_container")
            if (title is None or title.get("text") != "New message" or recipients is None
                    or not any(c.get("class") == "android.widget.TextView" and
                               (c.get("text", "").strip() or c.get("content-desc", "").strip()) for c in recipients)):
                return failure
            context = "new_compose"
        else:
            return failure
        return {"visible": True, "reason": "contextual_composer", "context": context,
                "bounds": dict(zip(("left", "top", "right", "bottom"), _rect(ed))),
                "app_viewport": _rect(viewport), "window_bounds": _rect(root[0]),
                "focused": ed.get("focused") == "true",
                "ime_type": "not_exposed_by_app_xml", "ime_visible": "not_exposed_by_app_xml"}
    except (ET.ParseError, ValueError, TypeError, RecursionError):
        return failure
