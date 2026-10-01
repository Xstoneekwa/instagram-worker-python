"""Read-only DM evidence. Missing message markers never prove an empty thread.

Only the observed 372/446 structures are accepted. New layouts fail closed.
Vision availability is deliberately independent of conversation history.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET


def classify_dm_history(hierarchy: str, package: str, expected_username: str | None = None, *, surface_context=None) -> tuple[str, dict]:
    evidence = {"history_signal_type": "", "empty_signal_type": ""}

    def unknown(reason):
        return "unknown", dict(evidence, reason=reason)

    try:
        root = ET.fromstring(hierarchy)
    except (ET.ParseError, TypeError):
        return unknown("hierarchy_unavailable")

    extension = None
    if surface_context is not None:
        from dm_ui_446_surface import qualify
        extension = qualify(root, package, expected_username, surface_context)

    def rid(n):
        value = n.get("resource-id", "")
        return value.split(":id/", 1)[1] if value.startswith(package + ":id/") else ""

    nodes = [n for n in root.iter("node") if n.get("package") == package]
    by_id = {}
    for n in nodes:
        by_id.setdefault(rid(n), []).append(n)
    lists = by_id.get("message_list", [])
    if len(lists) != 1 or "message_thread_container" not in by_id:
        return unknown("message_list_not_unique_or_missing")
    message_list = lists[0]
    # Never use an underlying thread through an app/system modal or stale window.
    if any(n.get("package", "").endswith(("permissioncontroller", "packageinstaller"))
           or n.get("resource-id", "") == "android:id/alertTitle"
           for n in root.iter("node")):
        return unknown("modal_obscures_thread")
    if any(n.get("visible-to-user") == "false" for n in message_list.iter()):
        return unknown("message_list_visibility_incomplete")

    def texts(row):
        return [n.get("text", "").strip() for n in row.iter()
                if n.get("text", "").strip()]

    def intro(row):
        if extension and row is extension['intro']:
            return "compose_recipient_intro"
        ids = {rid(n) for n in row.iter()}
        if {"user_avatar", "other_user_full_name_or_username", "network_attribution"} <= ids:
            if ids <= {"", "user_avatar", "other_user_full_name_or_username", "network_attribution"} and all(not n.get("text", "").strip() or rid(n) in {
                    "other_user_full_name_or_username", "network_attribution"}
                   for n in row.iter()):
                return "legacy_recipient_intro"
            return ""
        # 446 moves the profile intro to Compose, just like real message rows.
        # Require an avatar-shaped clickable view plus profile-specific content.
        ts = texts(row)
        profile_info = any(re.fullmatch(r"[\d.,KMkm]+ followers · [\d.,KMkm]+ posts", t)
                           for t in ts) or "View profile" in ts
        names = {n.get("text", "").strip() for n in by_id.get("header_title", [])}
        names.update(n.get("content-desc", "").strip() for n in by_id.get("header_subtitle", []))
        # A compose picker has no header; its recipient name/handle intro is not
        # certified here. Do not guess the selection from an arbitrary TextView.
        allowed = lambda t: (t in names or t in {"View profile", "Follow back", "Follows you",
                            "You don't follow each other on Instagram"}
                            or re.fullmatch(r"[\d.,KMkm]+ followers · [\d.,KMkm]+ posts", t)
                            or t.startswith("You've followed this Instagram account since ")
                            or t.startswith("You started a chat with "))
        if (row.get("class", "").endswith(".MetaComposeView") and profile_info and names
                and ids <= {"", "igds_button", "ig_text"}
                and all(allowed(t) for t in ts)
                and not any(n.get("class") == "android.widget.ImageView" for n in row.iter())
                and sum(n.get("class", "").endswith(".MetaComposeView") for n in row.iter()) == 1
                and any(n.get("clickable") == "true" for n in row.iter())):
            return "compose_recipient_intro"
        return ""

    rows = list(message_list)
    intro_rows = [row for row in rows if intro(row)]
    timestamp = any(re.fullmatch(r"\d{1,2} [A-Za-zÀ-ÿ.]+, \d{1,2}:\d{2}", t)
                    for row in rows if row.get("class") == "android.widget.TextView"
                    for t in texts(row))
    for row in rows:
        if intro(row):
            continue
        ids = {rid(n) for n in row.iter()}
        if any(any(marker in value for marker in ("sticker", "banner", "education", "privacy", "recommend"))
               for value in ids):
            continue
        if "message_content" in ids and any(
                rid(n) in {"direct_text_message_text_view", "message_bubble"}
                and (n.get("text", "").strip() or n.get("content-desc", "").strip())
                for n in row.iter()):
            return "existing_thread", dict(evidence, history_signal_type="legacy_message_content", reason="positive_history")
        if row.get("class", "").endswith(".MetaComposeView") and row.get("long-clickable") == "true":
            ts = texts(row)
            # A reaction footer or sender avatar relationship must corroborate
            # the bubble. Timestamp + generic Compose text could be education.
            footer = "Tap and hold to react" in ts
            sender = any(n is not row and n.get("class") == "android.view.View"
                         and n.get("clickable") == "true" and n.get("long-clickable") == "true"
                         and _dm_bubble_bounds(n.get("bounds", ""), message_list.get("bounds", ""))
                         for n in row.iter())
            content = [t for t in ts if t != "Tap and hold to react"]
            bubble = any(n.get("class") == "android.widget.TextView"
                         and n.get("text", "").strip() in content
                         and _dm_bubble_bounds(n.get("bounds", ""), message_list.get("bounds", ""))
                         for n in row.iter())
            if content and bubble and (footer or (timestamp and sender)):
                return "existing_thread", dict(evidence, history_signal_type="compose_message_bubble", reason="positive_history")

    # A positively recognized recipient intro must be the *entire* conversation
    # list. An unrecognized row, timestamp, spinner, banner or media remains UNKNOWN.
    if len(rows) != 1 or len(intro_rows) != 1:
        return unknown("no_positive_empty_structure")
    # Empty proof is stricter than positive history recognition. Preserve R01/R02.
    identity = extension['identity'] if extension and rows[0] is extension['intro'] else _empty_intro_identity(rows[0], package)
    if identity is None:
        return unknown("unknown_intro_semantics")
    surface_reason = '' if extension and extension['empty_surface'] and rows[0] is extension['intro'] else _empty_surface_reason(root, message_list, package, identity)
    if surface_reason:
        return unknown(surface_reason)
    display_name, handle = identity
    norm = _normalized_identity
    if expected_username and norm(expected_username) != norm(handle):
        return unknown("recipient_target_mismatch")
    headers = by_id.get("header_title", []) + by_id.get("header_subtitle", [])
    header_values = [norm(n.get(channel, "")) for n in headers
                     for channel in ("text", "content-desc") if norm(n.get(channel, ""))]
    recipient_header = (bool(header_values) and norm(handle) in header_values
                        and all(v in {norm(display_name), norm(handle)} for v in header_values))
    # Only the observed selected-recipient container proves the picker identity.
    chips = [n for container in by_id.get("recipients_container", []) for n in container
             if n.get("class") == "android.widget.TextView" and n.get("clickable") == "true"]
    recipient_picker = (not headers and len(chips) == 1
                        and _picker_identity_matches(chips[0], identity)
                        and "direct_new_chat_to_field" in by_id
                        and "recipient_picker_add_recipient_button" in by_id)
    if not (recipient_header or recipient_picker):
        return unknown("recipient_not_positively_proven")
    composer = "row_thread_composer_edittext" in by_id
    new_request = "row_thread_composer_send_button_background" in by_id
    education = "new_friend_bump_sticker_banner_container" in by_id or any(
        rid(n) == "message" and n.get("text", "").startswith("You can send one message in this request.")
        for n in nodes)
    if composer and (recipient_header or recipient_picker) and (education or (new_request and recipient_picker)):
        return "empty_new_thread", dict(evidence, empty_signal_type="recipient_intro_only_with_new_thread_context", reason="positive_empty_structure")
    return unknown("new_thread_context_not_proven")


def _dm_bubble_bounds(value: str, list_value: str) -> bool:
    coords = [int(x) for x in re.findall(r"-?\d+", value)]
    outer = [int(x) for x in re.findall(r"-?\d+", list_value)]
    return (len(coords) == len(outer) == 4 and outer[0] <= coords[0] < coords[2] <= outer[2]
            and outer[1] <= coords[1] < coords[3] <= outer[3]
            and coords[2] - coords[0] < outer[2] - outer[0])


def _normalized_identity(value: str) -> str:
    return " ".join(value.split()).lstrip("@").casefold()


def _rect(value):
    if not re.fullmatch(r"\[-?\d+,-?\d+\]\[-?\d+,-?\d+\]", value or ""):
        return None
    x1, y1, x2, y2 = map(int, re.findall(r"-?\d+", value))
    return (x1, y1, x2, y2) if x1 < x2 and y1 < y2 else None


def _empty_intro_identity(row, package):
    """Recognize complete observed intro grammars, including silent descendants.

    Descriptions may repeat the qualified text; they cannot introduce new content.
    Geometry is relative to the parent, never a device-specific coordinate match.
    """
    def node(n, cls, resource="", clickable=False, long=False, children=None, text=None):
        if (n.get("package") != package or n.get("class") != cls
                or n.get("resource-id", "") != resource
                or n.get("clickable") != str(clickable).lower()
                or n.get("long-clickable") != str(long).lower()
                or n.get("visible-to-user") == "false" or n.get("enabled") != "true"
                or (children is not None and len(n) != children)):
            return False
        value = " ".join(n.get("text", "").split())
        desc = " ".join(n.get("content-desc", "").split())
        return (bool(_rect(n.get("bounds"))) and (text is None or value == text)
                and (not desc or (bool(value) and desc == value)))

    for parent in row.iter():
        outer = _rect(parent.get("bounds"))
        if not outer:
            return None
        for child in parent:
            inner = _rect(child.get("bounds"))
            if not inner or not (outer[0] <= inner[0] < inner[2] <= outer[2]
                                 and outer[1] <= inner[1] < inner[3] <= outer[3]):
                return None
    prefix = package + ":id/"
    if node(row, "android.widget.LinearLayout", long=True, children=3, text=""):
        avatar, name, handle = row
        if (node(avatar, "android.widget.FrameLayout", prefix + "user_avatar", children=1, text="")
                and node(avatar[0], "android.widget.ImageView", prefix + "user_avatar", children=0, text="")
                and node(name, "android.widget.TextView", prefix + "other_user_full_name_or_username", children=0)
                and node(handle, "android.widget.TextView", prefix + "network_attribution", children=0)):
            values = (name.get("text", "").strip(), handle.get("text", "").strip())
            return values if all(values) else None
        return None
    if not node(row, "com.facebook.compose.view.MetaComposeView", long=True, children=1, text=""):
        return None
    body = row[0]
    if not node(body, "android.view.View", text="") or len(body) < 4:
        return None
    avatar, name, handle, *context = body
    if not (node(avatar, "android.view.View", clickable=True, children=1, text="")
            and node(avatar[0], "android.view.View", children=0, text="")
            and node(name, "android.widget.TextView", children=0)
            and node(handle, "android.widget.TextView", children=0)):
        return None
    values = (name.get("text", "").strip(), handle.get("text", "").strip())
    if not all(values):
        return None
    for item in context:
        if node(item, "android.widget.TextView", children=0):
            text = item.get("text", "").strip()
            if not (re.fullmatch(r"[\d.,KMkm]+ followers · [\d.,KMkm]+ posts", text)
                    or text in {"Follows you", "You don't follow each other on Instagram"}):
                return None
        elif node(item, "android.view.View", "igds_button", clickable=True, children=2, text=""):
            if not (node(item[0], "android.widget.TextView", "ig_text", children=0, text="View profile")
                    and node(item[1], "android.widget.Button", children=0, text="")):
                return None
        else:
            return None
    return values


def _empty_surface_reason(root, message_list, package, identity):
    """Fail closed on extra windows and unqualified siblings of the thread path.

    Android dumps in these fixtures omit visible-to-user. Absence is not false;
    explicit hidden ancestors invalidate the whole positive-empty observation.
    This is a hierarchy proof, not an OS compositor/secure-window inspection.
    """
    if not _qualified_conversation_path(root, message_list, package):
        return "conversation_path_unqualified"
    parents = {c: p for p in root.iter() for c in p}
    list_bounds = _rect(message_list.get("bounds"))
    if not list_bounds:
        return "conversation_bounds_unqualified"
    if root.tag != "hierarchy" or len(root) != 1:
        return "topmost_surface_unqualified"
    for n in root.iter("node"):
        if n.get("visible-to-user") == "false":
            return "effective_visibility_incomplete"
        if ("dialog" in n.get("class", "").casefold()
                or "popup" in n.get("class", "").casefold()):
            return "modal_obscures_thread"
    known = {
        "meta_ai_voice_container": "android.widget.FrameLayout",
        "direct_command_picker_container": "android.widget.FrameLayout",
        "message_actions_fragment": "android.widget.FrameLayout",
        "thread_background_view_container": "android.widget.LinearLayout",
        "message_composer_bar": "android.widget.FrameLayout",
        "direct_thread_header": "android.widget.LinearLayout",
        "direct_recipient_picker_action_bar": "android.widget.FrameLayout",
        "direct_recipients_selected_section": "android.widget.LinearLayout",
    }
    empty_containers = {"meta_ai_voice_container", "direct_command_picker_container", "message_actions_fragment"}
    current = message_list
    while current in parents:
        parent = parents[current]
        if parent is not root and (not _rect(parent.get("bounds")) or parent.get("package") != package):
            return "ancestor_surface_unqualified"
        for sibling in parent:
            if sibling is current:
                continue
            resource = sibling.get("resource-id", "")
            if resource in {"android:id/statusBarBackground", "android:id/navigationBarBackground"}:
                bar_bounds = _rect(sibling.get("bounds"))
                if (sibling.get("class") == "android.view.View" and not len(sibling)
                        and sibling.get("package") == package and bar_bounds
                        and not sibling.get("text") and not sibling.get("content-desc")
                        and sibling.get("clickable") == sibling.get("long-clickable") == "false"
                        and (bar_bounds[3] <= list_bounds[1] or bar_bounds[1] >= list_bounds[3])):
                    continue
            key = resource.removeprefix(package + ":id/")
            if (resource != package + ":id/" + key or key not in known
                    or sibling.get("class") != known[key] or not _rect(sibling.get("bounds"))):
                return "sibling_surface_unqualified"
            if not _qualified_empty_surface(sibling, package, identity):
                return "unqualified_surface_descendant"
            if key in {"direct_thread_header", "direct_recipient_picker_action_bar", "direct_recipients_selected_section"}:
                if _rect(sibling.get("bounds"))[3] > _rect(message_list.get("bounds"))[1]:
                    return "header_or_picker_overlaps_conversation"
            if key in empty_containers and (len(sibling) or sibling.get("text")
                    or sibling.get("content-desc") or sibling.get("clickable") == "true"):
                return "overlay_container_not_empty"
            if key == "thread_background_view_container":
                if len(sibling) != 1 or sibling[0].get("resource-id") != package + ":id/thread_background_view" or len(sibling[0]):
                    return "background_surface_unqualified"
        current = parent
    return ""


def _picker_identity_matches(chip, identity):
    # Context/cardinality/flags are certified by the surface grammar below.
    values = [_normalized_identity(chip.get(channel, ""))
              for channel in ("text", "content-desc") if _normalized_identity(chip.get(channel, ""))]
    return bool(values) and all(v in {_normalized_identity(x) for x in identity} for v in values)


# Observed 372/446 critical-surface grammar. Child order/cardinality is explicit;
# a familiar RID is not sufficient to admit arbitrary descendants. Empty IDs
# are named by class and may occur only in their declared parent relationships.
# Tuple: class suffix, clickable, long-clickable, ordered child roles.
_EMPTY_SURFACE_RULES = {
    "message_composer_bar": [("FrameLayout", False, False, "message_composer_reply_bar_container")],
    "message_composer_reply_bar_container": [("LinearLayout", False, False, "message_composer_tray_container row_thread_composer_container")],
    "message_composer_tray_container": [("LinearLayout", False, False, "nux_banner"), ("LinearLayout", False, False, "new_friend_bump_sticker_banner_container")],
    "nux_banner": [("ViewGroup", False, False, "divider message")],
    "divider": [("View", False, False, "")],
    "message": [("TextView", False, False, "")],
    "new_friend_bump_sticker_banner_container": [("LinearLayout", False, False, "divider text_container horizontal_scroll_container")],
    "text_container": [("LinearLayout", False, False, "centered_text_view close_button")],
    "centered_text_view": [("TextView", False, False, "")],
    "close_button": [("ImageView", True, False, "")],
    "horizontal_scroll_container": [("LinearLayout", False, False, "direct_new_friend_bump_tray_horizontal_section_recyclerview")],
    "direct_new_friend_bump_tray_horizontal_section_recyclerview": [("RecyclerView", False, False, "store_sticker_parent store_sticker_parent store_sticker_parent store_sticker_parent")],
    "store_sticker_parent": [("FrameLayout", True, False, "store_sticker_image")],
    "store_sticker_image": [("ImageView", False, False, "")],
    "row_thread_composer_container": [("FrameLayout", False, False, "blurred_text_area_background row_thread_composer_textarea_container"), ("FrameLayout", False, False, "blurred_text_area_background _LinearLayout")],
    "blurred_text_area_background": [("ImageView", False, False, "")],
    "_LinearLayout": [("LinearLayout", False, False, "row_thread_composer_textarea_container"), ("LinearLayout", False, False, "action_bar_title")],
    "row_thread_composer_textarea_container": [("LinearLayout", False, False, "composer_content_container row_thread_composer_buttons_container")],
    "composer_content_container": [("LinearLayout", False, False, "row_thread_composer_edittext")],
    "row_thread_composer_edittext": [("EditText", True, True, "")],
    "row_thread_composer_buttons_container": [("ViewGroup", False, False, "row_thread_composer_send_button_container")],
    "row_thread_composer_send_button_container": [("Button", True, False, "row_thread_composer_send_button_background row_thread_composer_send_button_icon"), ("Button", True, True, "row_thread_composer_send_button_background row_thread_composer_send_button_icon")],
    "row_thread_composer_send_button_background": [("ImageView", False, False, "")],
    "row_thread_composer_send_button_icon": [("ImageView", False, False, "")],
    "direct_thread_header": [("LinearLayout", False, False, "header_left_button header_avatar header_title_subtitle_container")],
    "header_left_button": [("ImageView", True, False, "")],
    "header_avatar": [("FrameLayout", True, False, "header_avatar header_avatar"), ("FrameLayout", True, True, "header_avatar header_avatar"), ("ImageView", False, False, ""), ("View", False, False, "")],
    "header_title_subtitle_container": [("Button", True, False, "header_title header_subtitle"), ("Button", True, False, "header_subtitle")],
    "header_title": [("Button", True, False, "")],
    "header_subtitle": [("Button", True, False, ""), ("View", False, False, "")],
    "direct_recipient_picker_action_bar": [("FrameLayout", False, False, "action_bar_wrapper")],
    "action_bar_wrapper": [("FrameLayout", False, False, "action_bar")],
    "action_bar": [("LinearLayout", True, False, "action_bar_new_title_container action_bar_textview_custom_title_container")],
    "action_bar_new_title_container": [("LinearLayout", True, False, "action_bar_button_back")],
    "action_bar_button_back": [("ImageView", True, False, "")],
    "action_bar_textview_custom_title_container": [("FrameLayout", False, False, "_LinearLayout")],
    "action_bar_title": [("TextView", False, False, "")],
    "direct_recipients_selected_section": [("LinearLayout", False, False, "direct_new_chat_to_field recipients_scroll_container recipient_picker_add_recipient_button")],
    "direct_new_chat_to_field": [("TextView", False, False, "")],
    "recipients_scroll_container": [("HorizontalScrollView", False, False, "recipients_container")],
    "recipients_container": [("LinearLayout", True, False, "_TextView _FrameLayout")],
    "_TextView": [("TextView", True, False, "")],
    "_FrameLayout": [("FrameLayout", False, False, "recipient_picker_typeahead_pill"), ("FrameLayout", False, False, "search_edit_text")],
    "recipient_picker_typeahead_pill": [("FrameLayout", False, False, "_FrameLayout")],
    "search_edit_text": [("EditText", True, True, "")],
    "recipient_picker_add_recipient_button": [("ImageView", True, False, "")],
    "thread_background_view_container": [("LinearLayout", False, False, "thread_background_view")],
    "thread_background_view": [("ImageView", False, False, "")],
    "meta_ai_voice_container": [("FrameLayout", False, False, "")],
    "direct_command_picker_container": [("FrameLayout", False, False, "")],
    "message_actions_fragment": [("FrameLayout", False, False, "")],
}


def _qualified_empty_surface(surface, package, identity):
    def role(n):
        resource = n.get("resource-id", "")
        if resource.startswith(package + ":id/"):
            return resource.split(":id/", 1)[1]
        return "_" + n.get("class", "").rsplit(".", 1)[-1] if not resource else ""

    def semantics(n, key):
        text = " ".join(n.get("text", "").split())
        desc = " ".join(n.get("content-desc", "").split())
        if key == "_TextView":
            return _picker_identity_matches(n, identity)
        if key in {"header_title", "header_subtitle"}:
            return all(_normalized_identity(v) in {_normalized_identity(x) for x in identity}
                       for v in (text, desc) if v)
        if key == "header_avatar" and len(n):
            return not text and (not desc or _normalized_identity(desc) == "open story of " + _normalized_identity(identity[1]))
        expected = {
            "header_left_button": ("", "Back"), "action_bar_button_back": ("", "Back"),
            "recipient_picker_add_recipient_button": ("", "Add people"),
            "search_edit_text": ("", "Search"), "direct_new_chat_to_field": ("To:", ""),
            "action_bar_title": ("New message", ""),
            "centered_text_view": ("Say hello by sending a sticker", ""),
            "store_sticker_image": ("", "Send this sticker"),
            "row_thread_composer_edittext": ("Message…", ""),
            "row_thread_composer_send_button_background": ("", "Send"),
            "message": ("You can send one message in this request. Once they accept, you can send more messages.", ""),
        }.get(key, ("", ""))
        return (text, desc) == expected or (bool(expected[0]) and text == desc == expected[0] and not expected[1])

    def signature(n):
        # Only identical, non-actionable text leaves may be accessibility duplicates.
        # Index/drawing order are not identity. Bounds and every semantic flag are.
        return {k: v for k, v in n.attrib.items() if k not in {"index", "drawing-order"}}

    def visit(n, parent_bounds=None):
        bounds = _rect(n.get("bounds"))
        if (n.tag != "node" or n.get("package") != package or not bounds
                or n.get("visible-to-user") == "false"
                or n.get("enabled") != ("false" if role(n) == "row_thread_composer_send_button_container" else "true")
                or n.get("selected") != "false"):
            return False
        if parent_bounds and not (parent_bounds[0] <= bounds[0] < bounds[2] <= parent_bounds[2]
                                  and parent_bounds[1] <= bounds[1] < bounds[3] <= parent_bounds[3]):
            return False
        key = role(n)
        if not semantics(n, key):
            return False
        children = []
        for child in n:
            if not visit(child, bounds):
                return False
            if (children and not len(child) and child.get("class") == "android.widget.TextView"
                    and child.get("clickable") == child.get("long-clickable") == "false"
                    and signature(child) == signature(children[-1])):
                continue
            children.append(child)
        for cls, clickable, long_clickable, child_roles in _EMPTY_SURFACE_RULES.get(key, []):
            full_class = ("androidx.recyclerview.widget.RecyclerView" if cls == "RecyclerView"
                          else "android.view." + cls if cls in {"View", "ViewGroup"} else "android.widget." + cls)
            if (n.get("class") == full_class and n.get("clickable") == str(clickable).lower()
                    and n.get("long-clickable") == str(long_clickable).lower()
                    and [role(c) for c in children] == child_roles.split()):
                return True
        return False

    return visit(surface)


# Role graph, not arbitrary class transparency. The anonymous decorators have
# distinct contextual roles, so inserting another FrameLayout is not benign.
# State -> (class, raw RID suffix, permitted ordered direct-child role sequences).
# @ prefixes denote the two observed Android-owned resource names, not packages.
_CONVERSATION_PATH = {
    "window": ("android.widget.FrameLayout", "", (("decor_linear",), ("decor_linear", "navigation_bar"), ("decor_linear", "status_bar", "navigation_bar"))),
    "decor_linear": ("android.widget.LinearLayout", "", (("decor_frame",),)),
    "decor_frame": ("android.widget.FrameLayout", "", (("action_bar_root",),)),
    "action_bar_root": ("android.widget.LinearLayout", "action_bar_root", (("android_content",),)),
    "android_content": ("android.widget.FrameLayout", "@content", (("layout_container_parent",),)),
    "layout_container_parent": ("android.view.ViewGroup", "layout_container_parent", (("layout_container_main",),)),
    "layout_container_main": ("android.widget.FrameLayout", "layout_container_main", (("thread_fragment_container",), ("picker_layout",))),
    "picker_layout": ("android.widget.LinearLayout", "", (("direct_recipient_picker_action_bar", "direct_recipients_selected_section", "dynamic_fragment_container"),)),
    "dynamic_fragment_container": ("android.widget.LinearLayout", "dynamic_fragment_container", (("thread_fragment_container",),)),
    "thread_fragment_container": ("android.widget.FrameLayout", "thread_fragment_container", (("thread_background_view_container", "message_actions_fragment", "direct_thread_header", "direct_thread_content_below_action_bar"), ("thread_background_view_container", "message_actions_fragment", "direct_thread_content_below_action_bar"))),
    "direct_thread_content_below_action_bar": ("android.view.ViewGroup", "direct_thread_content_below_action_bar", (("thread_toggle_child_fragment_container", "direct_command_picker_container", "message_composer_bar"),)),
    "thread_toggle_child_fragment_container": ("android.widget.FrameLayout", "thread_toggle_child_fragment_container", (("thread_view_root",),)),
    "thread_view_root": ("android.widget.LinearLayout", "thread_view_root", (("message_thread_container",),)),
    "message_thread_container": ("android.widget.FrameLayout", "message_thread_container", (("message_list_refresh_container", "meta_ai_voice_container"),)),
    "message_list_refresh_container": ("android.widget.FrameLayout", "message_list_refresh_container", (("message_list",),)),
    "message_list": ("androidx.recyclerview.widget.RecyclerView", "message_list", ()),
}


def _qualified_conversation_path(root, message_list, package):
    """Iteratively certify root -> list and sibling membership at every level.

    A.2 still qualifies sibling subtrees. This function closes the gap on the
    path itself, without changing message recognition, picker or reread rules.
    """
    if (root.tag != "hierarchy" or len(root) != 1
            or set(root.attrib) - {"rotation"}
            or root.get("rotation", "0") not in {"0", "1", "2", "3"}
            or (root.text or "").strip()):
        return False

    def rid_for(role):
        suffix = _CONVERSATION_PATH[role][1] if role in _CONVERSATION_PATH else role
        if role == "status_bar": return "android:id/statusBarBackground"
        if role == "navigation_bar": return "android:id/navigationBarBackground"
        return "android:id/" + suffix[1:] if suffix.startswith("@") else package + ":id/" + suffix if suffix else ""

    def fits(child, outer):
        return bool(child and outer and outer[0] <= child[0] < child[2] <= outer[2]
                    and outer[1] <= child[1] < child[3] <= outer[3])

    current, role, parent_bounds = root[0], "window", None
    # The graph is acyclic; cap is derived from its number of roles, not a retry.
    for _ in range(len(_CONVERSATION_PATH)):
        cls, _, sequences = _CONVERSATION_PATH[role]
        bounds = _rect(current.get("bounds"))
        if (current.tag != "node" or current.get("package") != package
                or current.get("class") != cls or current.get("resource-id", "") != rid_for(role)
                or current.get("text", "").strip() or current.get("content-desc", "").strip()
                or current.get("visible-to-user") == "false"
                or current.get("clickable") != "false" or current.get("long-clickable") != "false"
                or current.get("enabled") != "true" or current.get("selected") != "false"
                or not bounds or (parent_bounds and not fits(bounds, parent_bounds))):
            return False
        if role == "window" and bounds[:2] != (0, 0):
            return False
        if role == "message_list":
            return (current is message_list and current.get("scrollable") == "true"
                    and all(fits(_rect(c.get("bounds")), bounds) for c in current))
        if current.get("scrollable") != "false":
            return False
        children = list(current)
        matched = None
        for sequence in sequences:
            if len(sequence) != len(children):
                continue
            if all(child.get("resource-id", "") == rid_for(child_role)
                   and (child_role not in _CONVERSATION_PATH
                        or child.get("class") == _CONVERSATION_PATH[child_role][0])
                   for child, child_role in zip(children, sequence)):
                matched = sequence
                break
        if matched is None or not all(fits(_rect(c.get("bounds")), bounds) for c in children):
            return False
        if role == "window":
            rects = [_rect(c.get("bounds")) for c in children]
            envelope = (min(b[0] for b in rects), min(b[1] for b in rects),
                        max(b[2] for b in rects), max(b[3] for b in rects))
            if bounds != envelope:
                return False
        path_children = [(c, state) for c, state in zip(children, matched) if state in _CONVERSATION_PATH]
        if len(path_children) != 1:
            return False
        parent_bounds = bounds
        current, role = path_children[0]
    return False
