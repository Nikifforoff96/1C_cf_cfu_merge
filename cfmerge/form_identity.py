from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from copy import deepcopy
from dataclasses import dataclass

from .xml_utils import child, child_text, local_name


@dataclass(frozen=True, slots=True)
class FormIdentity:
    domain: str
    parts: tuple[str, ...]

    def render(self) -> str:
        return f"{self.domain}:{'/'.join(self.parts)}"


def normalize_xml_fragment(element: ET.Element | None) -> str:
    if element is None:
        return ""
    cloned = deepcopy(element)
    for item in cloned.iter():
        if not isinstance(item.tag, str):
            continue
        item.attrib.pop("id", None)
        item.attrib.pop("callType", None)
    text = ET.tostring(cloned, encoding="unicode", short_empty_elements=True)
    text = re.sub(r">\s+<", "><", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def child_item_identity(element: ET.Element) -> FormIdentity:
    return FormIdentity("child_item", (local_name(element.tag), element.attrib.get("name", "")))


def attribute_identity(element: ET.Element) -> FormIdentity:
    return FormIdentity("attribute", (element.attrib.get("name", ""),))


def column_identity(owner_name: str, element: ET.Element) -> FormIdentity:
    return FormIdentity("column", (owner_name, element.attrib.get("name", "")))


def command_identity(element: ET.Element) -> FormIdentity:
    return FormIdentity("command", (element.attrib.get("name", ""),))


def parameter_identity(element: ET.Element) -> FormIdentity:
    return FormIdentity("parameter", (element.attrib.get("name", ""),))


def event_identity(owner: FormIdentity | None, element: ET.Element) -> FormIdentity:
    owner_key = owner.render() if owner is not None else "form"
    return FormIdentity("event", (owner_key, element.attrib.get("name", "")))


def action_identity(owner: FormIdentity | None) -> FormIdentity:
    owner_key = owner.render() if owner is not None else "form"
    return FormIdentity("action", (owner_key, "Action"))


def command_interface_item_identity(panel_name: str, element: ET.Element) -> FormIdentity:
    command = child_text(element, ["Command"]) or ""
    command_group = child_text(element, ["CommandGroup"]) or ""
    return FormIdentity("command_interface", (panel_name, command, command_group))


def conditional_appearance_rule_signatures(element: ET.Element) -> tuple[str, str]:
    selection = normalize_xml_fragment(child(element, "selection"))
    filter_text = normalize_xml_fragment(child(element, "filter"))
    appearance = normalize_xml_fragment(child(element, "appearance"))
    strict = "||".join((selection, filter_text, appearance))
    loose = "||".join((selection, filter_text))
    return strict, loose
