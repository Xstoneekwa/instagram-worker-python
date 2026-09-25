"""446-only local relationship evidence; no device actions or migration layer."""
from __future__ import annotations

from dataclasses import dataclass
import re
import xml.etree.ElementTree as ET

VERSION = ('446.0.0.49.77', 385211304)
CONTEXT_KEY = '_instagram_446_observed_context'


@dataclass(frozen=True)
class ObservedContext:
    serial: str
    package: str
    version: tuple[str, int]
    device_identity: int


def retain_context(device, *, serial, package, version_name, version_code, unsafe):
    """Consumes existing version-lock result, not a second metadata observation."""
    values = vars(device)
    if (version_name, version_code) == VERSION and not unsafe and serial and package:
        values[CONTEXT_KEY] = ObservedContext(str(serial), str(package), VERSION, id(device))
    elif CONTEXT_KEY in values or (version_name, version_code) == VERSION:
        # Revocation must not route a previously qualified446 device into legacy.
        values[CONTEXT_KEY] = None


def context_matches(context, device, serial, package):
    return (type(context) is ObservedContext and context.device_identity == id(device)
            and context.serial == serial and context.package == package
            and context.version == VERSION)


def _bounds(node):
    match = re.fullmatch(r'\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]', node.get('bounds', ''))
    if not match:
        return None
    box = tuple(map(int, match.groups()))
    return box if box[2] > box[0] and box[3] > box[1] else None


def _visible(node):
    return (node.get('visible-to-user', 'true') != 'false'
            and node.get('displayed', 'true') != 'false')


def relationship_from_xml(xml, *, package, expected_username):
    """Exact target header CTA, never counters or suggested-profile controls."""
    if not package or not re.fullmatch(r'[A-Za-z0-9_.]+', expected_username or ''):
        return 'unknown'
    try:
        root = ET.fromstring(xml)
    except (ET.ParseError, TypeError):
        return 'unknown'
    nodes = list(root.iter('node'))
    parents = {child: parent for parent in root.iter() for child in parent}
    prefix = package + ':id/'
    buttons = [n for n in nodes if n.get('resource-id') == prefix+'profile_header_follow_button']
    titles = [n for n in nodes if n.get('resource-id') == prefix+'action_bar_title']
    if len(buttons) != 1 or len(titles) != 1:
        return 'unknown'
    button, title = buttons[0], titles[0]
    if title.get('package') != package or not _visible(title):
        return 'unknown'
    ancestor = title
    while ancestor is not None:
        if not _visible(ancestor) or ancestor.get('enabled') == 'false':
            return 'unknown'
        ancestor = parents.get(ancestor)
    channels = [title.get(k, '').strip().lstrip('@') for k in ('text', 'content-desc')]
    if not any(channels) or any(v.casefold() != expected_username.casefold() for v in channels if v):
        return 'unknown'
    box = _bounds(button)
    if (not box or min(box[:2]) < 0 or button.get('package') != package
            or button.get('class') not in ('android.widget.TextView', 'android.widget.Button')
            or button.get('enabled') != 'true' or button.get('clickable') != 'true'):
        return 'unknown'
    chain = []
    node = button
    while node is not None:
        chain.append(node)
        if not _visible(node) or node.get('enabled') == 'false':
            return 'unknown'
        if node.tag == 'node' and node.get('package') != package:
            return 'unknown'
        node = parents.get(node)
    required = ('profile_header_user_action_follow_button', 'profile_header_actions_top_row',
                'profile_header_fixed_list', 'profile_header_container')
    ids = [n.get('resource-id') for n in chain]
    if any(prefix+rid not in ids for rid in required):
        return 'unknown'
    positions = [ids.index(prefix+rid) for rid in required]
    if positions != sorted(positions):
        return 'unknown'
    # The bound CTA must be contained by its own observed profile header.
    header = chain[ids.index(prefix+'profile_header_container')]
    hb = _bounds(header)
    if not hb or not (hb[0] <= box[0] < box[2] <= hb[2] and hb[1] <= box[1] < box[3] <= hb[3]):
        return 'unknown'
    label = button.get('text', '').strip().casefold()
    desc = button.get('content-desc', '').strip().casefold()
    states = {'follow': 'follow', 'follow back': 'follow_back', 'following': 'following', 'requested': 'requested'}
    if label not in states:
        return 'unknown'
    if desc and not (desc == label or desc.startswith(label+' ')):
        return 'unknown'
    # Follow back must not be accepted as Follow by prefix alone.
    if label == 'follow' and desc.startswith('follow back'):
        return 'unknown'
    for n in nodes:
        if not _visible(n):
            continue
        semantic = ' '.join(n.get(k, '').casefold() for k in ('text', 'content-desc', 'resource-id', 'class'))
        if any(s in semantic for s in ('introducing the instagram map', 'introducing instants',
                'android.app.dialog', 'dialog_container', 'challenge', 'confirm you', 'log in', 'review before')):
            return 'unknown'
        if n in chain or n in list(button.iter()):
            continue
        nb = _bounds(n)
        if (nb and n.get('enabled') != 'false' and n.get('clickable') == 'true'
                and max(nb[0], box[0]) < min(nb[2], box[2])
                and max(nb[1], box[1]) < min(nb[3], box[3])):
            return 'unknown'
    return states[label]
