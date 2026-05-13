from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .classifier import TYPE_TO_DIR
from .io_utils import normalize_rel
from .models import MergeReport
from .object_registry import ObjectRegistry, build_object_registry
from .xml_utils import child, child_text, children, collect_namespace_declarations, local_name, namespace, parse_xml, write_xml


STANDARD_COMMAND_RE = re.compile(r"^(?P<type>[^.]+)\.(?P<object>[^.]+)\.StandardCommand\.(?P<command>[^.]+)$")
OBJECT_COMMAND_RE = re.compile(r"^(?P<type>[^.]+)\.(?P<object>[^.]+)\.Command\.(?P<command>[^.]+)$")
COMMON_COMMAND_RE = re.compile(r"^CommonCommand\.(?P<command>[^.]+)$")


def _result_root_for(dst: Path, rel: str) -> Path:
    root = dst
    for _ in rel.replace("\\", "/").split("/"):
        root = root.parent
    return root


def _object_use_standard_commands_false(command: str, registry: ObjectRegistry) -> bool:
    match = STANDARD_COMMAND_RE.match(command)
    if match is None:
        return False
    ref = registry.find(match.group("type"), match.group("object"))
    if ref is None:
        return False
    try:
        root_obj = next((item for item in list(parse_xml(ref.abs_path).getroot()) if isinstance(item.tag, str)), None)
    except Exception:
        return False
    if root_obj is None:
        return False
    return (child_text(root_obj, ["Properties", "UseStandardCommands"]) or "").casefold() == "false"


def _iter_command_elements(root: ET.Element) -> list[ET.Element]:
    return [
        item
        for item in root.iter()
        if isinstance(item.tag, str) and local_name(item.tag) == "Command" and "name" in item.attrib
    ]


def _remove_disabled_standard_commands(root: ET.Element, registry: ObjectRegistry) -> list[str]:
    removed: list[str] = []
    for parent in root.iter():
        if not isinstance(parent.tag, str):
            continue
        for item in list(parent):
            if not isinstance(item.tag, str) or local_name(item.tag) != "Command":
                continue
            command = item.attrib.get("name", "")
            if not _object_use_standard_commands_false(command, registry):
                continue
            parent.remove(item)
            removed.append(command)
    return sorted(set(removed))


def copy_command_interface_resource(
    src: Path,
    dst: Path,
    rel: str,
    report: MergeReport,
    registry: ObjectRegistry | None = None,
) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    result_root = _result_root_for(dst, rel)
    registry = registry or build_object_registry(result_root)
    tree = parse_xml(src)
    removed = _remove_disabled_standard_commands(tree.getroot(), registry)
    write_xml(dst, tree, namespace(tree.getroot().tag), extra_namespaces=collect_namespace_declarations(src))
    for command in removed:
        report.add_metadata_action(
            object_path=rel,
            object_type="CommandInterface",
            property_path=command,
            action="command_interface_command_removed",
            old_value=command,
            new_value=None,
            reason="standard_command_disabled_in_target",
            source_path=rel,
        )
    return "copy_command_interface_filtered" if removed else "copy_command_interface"


def _metadata_object(path: Path) -> ET.Element | None:
    try:
        return next((item for item in list(parse_xml(path).getroot()) if isinstance(item.tag, str)), None)
    except Exception:
        return None


def _object_path(command_type: str, object_name: str, registry: ObjectRegistry) -> Path | None:
    ref = registry.find(command_type, object_name)
    if ref is not None:
        return ref.abs_path
    directory = TYPE_TO_DIR.get(command_type)
    if directory is None:
        return None
    candidate = registry.root / directory / f"{object_name}.xml"
    return candidate if candidate.exists() else None


def _has_child_command(object_path: Path, command_name: str) -> bool:
    obj = _metadata_object(object_path)
    child_objects = child(obj, "ChildObjects") if obj is not None else None
    if child_objects is None:
        return False
    for item in children(child_objects, "Command"):
        if child_text(item, ["Properties", "Name"]) == command_name:
            return True
    return False


def _use_standard_commands_false(object_path: Path) -> bool:
    obj = _metadata_object(object_path)
    if obj is None:
        return False
    return (child_text(obj, ["Properties", "UseStandardCommands"]) or "").casefold() == "false"


def _validate_command_reference(command: str, registry: ObjectRegistry) -> tuple[str, str] | None:
    standard = STANDARD_COMMAND_RE.match(command)
    if standard is not None:
        object_path = _object_path(standard.group("type"), standard.group("object"), registry)
        if object_path is None:
            return "COMMAND_INTERFACE_TARGET_NOT_FOUND", command
        if _use_standard_commands_false(object_path):
            return "COMMAND_INTERFACE_STANDARD_COMMAND_DISABLED", command
        return None

    object_command = OBJECT_COMMAND_RE.match(command)
    if object_command is not None:
        object_path = _object_path(object_command.group("type"), object_command.group("object"), registry)
        if object_path is None:
            return "COMMAND_INTERFACE_TARGET_NOT_FOUND", command
        if not _has_child_command(object_path, object_command.group("command")):
            return "COMMAND_INTERFACE_COMMAND_NOT_FOUND", command
        return None

    common_command = COMMON_COMMAND_RE.match(command)
    if common_command is not None and registry.find("CommonCommand", common_command.group("command")) is None:
        return "COMMAND_INTERFACE_TARGET_NOT_FOUND", command

    return None


def validate_command_interface_resource(path: Path, out_dir: Path, registry: ObjectRegistry, report: MergeReport) -> None:
    try:
        root = parse_xml(path).getroot()
    except Exception as exc:
        report.add_conflict("XML_PARSE_FAILED", str(path), str(exc))
        return
    if local_name(root.tag) != "CommandInterface":
        return
    seen: set[tuple[str, str]] = set()
    rel = normalize_rel(path.relative_to(out_dir)) if path.is_relative_to(out_dir) else str(path)
    for item in _iter_command_elements(root):
        command = item.attrib.get("name", "")
        problem = _validate_command_reference(command, registry)
        if problem is None or problem in seen:
            continue
        seen.add(problem)
        code, details = problem
        report.add_conflict(code, rel, details, object_type="CommandInterface", object_name=command)
