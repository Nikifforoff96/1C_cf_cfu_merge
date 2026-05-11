from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .classifier import CHILD_TYPE_ORDER, TYPE_TO_DIR
from .io_utils import copy_file
from .metadata_property_merge import MetadataMergeContext, merge_metadata_element
from .models import MergeReport
from .object_registry import ObjectRegistry, build_object_registry
from .xml_utils import (
    NS_MD,
    child,
    child_text,
    children,
    clone_element,
    collect_namespace_declarations,
    is_adopted,
    local_name,
    object_name,
    parse_xml,
    write_xml,
)


def metadata_root_object(tree: ET.ElementTree) -> ET.Element:
    root = tree.getroot()
    for item in list(root):
        if isinstance(item.tag, str):
            return item
    raise ValueError("MetaDataObject does not contain metadata object")


def xml_is_adopted(path: Path) -> bool:
    try:
        tree = parse_xml(path)
        return is_adopted(metadata_root_object(tree))
    except Exception:
        return False


def xml_object_name(path: Path) -> str | None:
    try:
        tree = parse_xml(path)
        return object_name(metadata_root_object(tree))
    except Exception:
        return None


def _write_metadata_xml(out_path: Path, base_path: Path, ext_path: Path, tree: ET.ElementTree) -> None:
    extra_ns = collect_namespace_declarations(base_path)
    extra_ns.update(collect_namespace_declarations(ext_path))
    write_xml(out_path, tree, NS_MD, extra_namespaces=extra_ns)


def merge_configuration(
    base_path: Path,
    ext_path: Path,
    out_path: Path,
    report: MergeReport,
    base_registry: ObjectRegistry | None = None,
    ext_registry: ObjectRegistry | None = None,
) -> None:
    base_registry = base_registry or build_object_registry(base_path.parent)
    ext_registry = ext_registry or build_object_registry(ext_path.parent)
    base_tree = parse_xml(base_path)
    ext_tree = parse_xml(ext_path)
    base_cfg = metadata_root_object(base_tree)
    ext_cfg = metadata_root_object(ext_tree)
    context = MetadataMergeContext(
        report=report,
        rel_path="Configuration.xml",
        object_path=f"Configuration.{child_text(base_cfg, ['Properties', 'Name']) or ''}",
        object_type="Configuration",
    )
    changed = False

    base_child = child(base_cfg, "ChildObjects")
    ext_child = child(ext_cfg, "ChildObjects")
    if base_child is not None and ext_child is not None:
        existing = {(local_name(item.tag), (item.text or "").strip()) for item in children(base_child)}
        added: list[ET.Element] = []
        for item in children(ext_child):
            typ = local_name(item.tag)
            name = (item.text or "").strip()
            if not name or (typ, name) in existing:
                continue
            if typ not in TYPE_TO_DIR:
                continue
            ext_ref = ext_registry.find(typ, name)
            if ext_ref is None:
                report.add_warning(
                    "CONFIGURATION_CHILD_OBJECT_FILE_NOT_FOUND",
                    "Configuration.xml",
                    f"Extension object XML not found for {typ}.{name}",
                )
                continue
            if ext_ref.is_adopted:
                continue
            added.append(clone_element(item))
            existing.add((typ, name))
            report.objects["added"].append({
                "type": typ,
                "name": name,
                "path": ext_ref.rel_path,
                "strategy": "configuration_child_native_extension",
            })
            report.add_metadata_action(
                object_path=context.object_path,
                object_type="Configuration",
                property_path="ChildObjects",
                action="child_object_added",
                old_value=None,
                new_value=f"{typ}.{name}",
                reason="native_extension_top_level_object",
                source_path="Configuration.xml",
            )

        if added:
            all_items = [clone_element(item) for item in children(base_child)] + added
            order = {name: idx for idx, name in enumerate(CHILD_TYPE_ORDER)}
            all_items.sort(key=lambda e: (order.get(local_name(e.tag), 999), (e.text or "").lower()))
            for item in list(base_child):
                base_child.remove(item)
            for item in all_items:
                base_child.append(item)
            changed = True

    if not changed:
        if base_path.resolve() != out_path.resolve():
            copy_file(base_path, out_path)
        return

    _write_metadata_xml(out_path, base_path, ext_path, base_tree)
    report.summary["files_changed"] += 1
    report.objects["modified"].append({
        "type": "Configuration",
        "name": child_text(base_cfg, ["Properties", "Name"]),
        "path": "Configuration.xml",
        "strategy": "merge_configuration_child_objects",
    })


def merge_metadata_object(base_path: Path, ext_path: Path, out_path: Path, rel_path: str, report: MergeReport) -> str:
    ext_tree = parse_xml(ext_path)
    ext_obj = metadata_root_object(ext_tree)
    if not is_adopted(ext_obj):
        copy_file(ext_path, out_path)
        return "copy_native_metadata"

    base_tree = parse_xml(base_path)
    base_obj = metadata_root_object(base_tree)
    context = MetadataMergeContext(
        report=report,
        rel_path=rel_path,
        object_path=f"{local_name(base_obj.tag)}.{object_name(base_obj) or ''}",
        object_type=local_name(base_obj.tag),
    )
    if merge_metadata_element(base_obj, ext_obj, context):
        _write_metadata_xml(out_path, base_path, ext_path, base_tree)
        return "merge_adopted_metadata_semantic"

    if base_path.resolve() != out_path.resolve():
        copy_file(base_path, out_path)
    return "keep_base_adopted_metadata"


def top_level_xml_is_native(cfu_dir: Path, rel_path: str) -> bool:
    path = cfu_dir / rel_path
    if not path.exists() or not rel_path.endswith(".xml"):
        return False
    return not xml_is_adopted(path)


def metadata_full_name(path: Path) -> tuple[str | None, str | None, str | None]:
    try:
        tree = parse_xml(path)
        obj = metadata_root_object(tree)
        typ = local_name(obj.tag)
        name = child_text(obj, ["Properties", "Name"])
        uuid = obj.attrib.get("uuid")
        return typ, name, uuid
    except Exception:
        return None, None, None
