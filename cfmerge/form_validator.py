from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from .bsl_parser import parse_module
from .models import MergeReport
from .xml_utils import child, children, local_name, parse_xml


OPAQUE_DATAPATH_RE = re.compile(r"^\d+/[-0-9a-fA-F:]+$")
INDEXED_HEAD_RE = re.compile(r"^(?P<head>[^.\[]+)\[\d+\](?:\.|$)")


def _childitem_elements(root: ET.Element) -> tuple[list[str], list[str]]:
    container = child(root, "ChildItems")
    if container is None:
        return [], []
    names: list[str] = []
    ids: list[str] = []
    for item in container.iter():
        if not isinstance(item.tag, str):
            continue
        if local_name(item.tag) in {"Event", "Action"}:
            continue
        if "name" in item.attrib and "id" in item.attrib:
            names.append(item.attrib["name"])
        if "id" in item.attrib:
            ids.append(item.attrib["id"])
    return names, ids


def _collect_handlers(root: ET.Element) -> list[str]:
    handlers: list[str] = []
    for item in root.iter():
        if not isinstance(item.tag, str):
            continue
        if local_name(item.tag) not in {"Event", "Action"}:
            continue
        value = (item.text or "").strip()
        if value:
            handlers.append(value)
    return handlers


def _collect_attribute_names(root: ET.Element) -> set[str]:
    attrs = child(root, "Attributes")
    if attrs is None:
        return set()
    return {item.attrib.get("name", "") for item in children(attrs, "Attribute")}


def _collect_child_item_names(root: ET.Element) -> set[str]:
    result: set[str] = set()
    container = child(root, "ChildItems")
    if container is None:
        return result
    for item in container.iter():
        if not isinstance(item.tag, str):
            continue
        name = item.attrib.get("name", "")
        if name:
            result.add(name)
    return result


def _is_resolved_data_path(value: str, attribute_names: set[str], child_item_names: set[str]) -> bool:
    if not value:
        return True
    if value.startswith(("Object.", "Объект.", "ThisObject.", "РћР±СЉРµРєС‚.")):
        return True
    if OPAQUE_DATAPATH_RE.match(value):
        return True

    normalized = value[1:] if value.startswith("~") else value
    if normalized.startswith("Items."):
        parts = normalized.split(".")
        if len(parts) < 2:
            return True
        item_name = re.sub(r"\[\d+\]$", "", parts[1])
        return item_name in child_item_names

    indexed = INDEXED_HEAD_RE.match(normalized)
    if indexed:
        return indexed.group("head") in attribute_names

    head = normalized.split(".", 1)[0]
    return head in attribute_names or head in child_item_names


def validate_form_result(form_path: Path, report: MergeReport) -> None:
    try:
        tree = parse_xml(form_path)
    except Exception as exc:
        report.add_conflict("XML_PARSE_FAILED", str(form_path), str(exc))
        return
    root = tree.getroot()
    names, ids = _childitem_elements(root)
    duplicate_names = sorted(name for name, count in Counter(names).items() if count > 1)
    duplicate_ids = sorted(ident for ident, count in Counter(ids).items() if count > 1)
    for name in duplicate_names[:50]:
        report.add_conflict("FORM_DUPLICATE_CHILD_ITEM_NAME", str(form_path), name)
    for ident in duplicate_ids[:50]:
        report.add_conflict("FORM_DUPLICATE_CHILD_ITEM_ID", str(form_path), ident)

    module_path = form_path.parent / "Form" / "Module.bsl"
    if module_path.exists():
        try:
            methods = {
                method.local_name.lower()
                for method in parse_module(module_path.read_text(encoding="utf-8-sig", errors="ignore")).methods
            }
        except Exception as exc:
            report.add_conflict("FORM_MODULE_PARSE_FAILED", str(module_path), str(exc))
            methods = set()
        for handler in _collect_handlers(root):
            if handler.lower() not in methods:
                report.add_conflict("FORM_HANDLER_MISSING", str(form_path), handler)

    attribute_names = _collect_attribute_names(root)
    child_item_names = _collect_child_item_names(root)
    for item in root.iter():
        if not isinstance(item.tag, str) or local_name(item.tag) != "DataPath":
            continue
        value = (item.text or "").strip()
        if not value:
            continue
        if not _is_resolved_data_path(value, attribute_names, child_item_names):
            report.add_conflict("FORM_DATAPATH_UNRESOLVED", str(form_path), value)

    text = form_path.read_text(encoding="utf-8-sig", errors="ignore")
    if "<ConditionalAppearance>" in text:
        if 'xmlns:dcsset="http://v8.1c.ru/8.1/data-composition-system/settings"' not in text:
            report.add_conflict("FORM_CONDITIONAL_APPEARANCE_NAMESPACE_MISSING", str(form_path), "dcsset")
        if "<dcsset:item" in text and "xsi:type=" not in text:
            report.add_conflict("FORM_CONDITIONAL_APPEARANCE_XSI_TYPE_MISSING", str(form_path), "xsi:type")

    commands = child(root, "Commands")
    command_names = {item.attrib.get("name", "") for item in children(commands, "Command")} if commands is not None else set()
    command_interface = child(root, "CommandInterface")
    if command_interface is not None:
        for item in command_interface.iter():
            if not isinstance(item.tag, str) or local_name(item.tag) != "Command":
                continue
            value = (item.text or "").strip()
            if value.startswith("Form.Command.") and value.split(".")[-1] not in command_names:
                report.add_conflict("FORM_COMMAND_INTERFACE_BROKEN_REFERENCE", str(form_path), value)

    if 'callType="' in text:
        report.add_conflict("FORM_CALLTYPE_LEFT", str(form_path), "В plain Form.xml остался callType")
    if re.search(r"<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?BaseForm\b", text):
        report.add_conflict("FORM_BASEFORM_LEFT", str(form_path), "В plain Form.xml остался BaseForm")
