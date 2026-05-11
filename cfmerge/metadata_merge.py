from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .classifier import CHILD_TYPE_ORDER, TYPE_TO_DIR
from .io_utils import copy_file, read_text
from .models import MergeReport
from .object_registry import ObjectRegistry, build_object_registry
from .xml_patch import insert_before_close, replace_element_inner_text, span_map, write_patched_like_source
from .xml_utils import (
    NS_MD,
    child,
    child_text,
    children,
    clone_element,
    collect_namespace_declarations,
    element_key,
    is_adopted,
    local_name,
    object_name,
    parse_xml,
    q,
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
    base_child = child(base_cfg, "ChildObjects")
    ext_child = child(ext_cfg, "ChildObjects")
    if base_child is None or ext_child is None:
        extra_ns = collect_namespace_declarations(base_path)
        extra_ns.update(collect_namespace_declarations(ext_path))
        write_xml(out_path, base_tree, NS_MD, extra_namespaces=extra_ns)
        return

    existing = {(local_name(item.tag), (item.text or "").strip()) for item in children(base_child)}
    added: list[ET.Element] = []
    for item in children(ext_child):
        typ = local_name(item.tag)
        name = (item.text or "").strip()
        if not name or (typ, name) in existing:
            continue
        obj_dir = TYPE_TO_DIR.get(typ)
        if not obj_dir:
            continue
        ext_ref = ext_registry.find(typ, name)
        if ext_ref is None:
            report.add_warning("CONFIGURATION_CHILD_OBJECT_FILE_NOT_FOUND", "Configuration.xml", f"Не найден XML объекта расширения {typ}.{name}")
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

    all_items = [clone_element(item) for item in children(base_child)] + added
    order = {name: idx for idx, name in enumerate(CHILD_TYPE_ORDER)}
    all_items.sort(key=lambda e: (order.get(local_name(e.tag), 999), (e.text or "").lower()))
    if not added:
        if base_path.resolve() != out_path.resolve():
            copy_file(base_path, out_path)
        return
    if added:
        base_text = read_text(base_path)
        spans = span_map(base_text)
        child_span = next((s for s in spans.values() if s.local == "ChildObjects"), None)
        if child_span:
            inner = "".join(f"\t\t<{local_name(item.tag)}>{(item.text or '').strip()}</{local_name(item.tag)}>\r\n" for item in all_items)
            base_text = replace_element_inner_text(base_text, child_span, "\r\n" + inner + "\t")
            write_patched_like_source(out_path, base_path, base_text)
        else:
            write_xml(out_path, base_tree, NS_MD)
    report.summary["files_changed"] += 1
    report.objects["modified"].append({
        "type": "Configuration",
        "name": child_text(base_cfg, ["Properties", "Name"]),
        "path": "Configuration.xml",
        "strategy": "merge_configuration_child_objects",
    })


def _child_key_map(parent: ET.Element) -> dict[tuple[str, str], ET.Element]:
    return {element_key(item): item for item in children(parent)}


def _simple_child_reference_snippet(item: ET.Element) -> str | None:
    if item.attrib or children(item):
        return None
    text = (item.text or "").strip()
    if not text:
        return None
    tag = local_name(item.tag)
    return f"<{tag}>{text}</{tag}>"


def merge_metadata_object(base_path: Path, ext_path: Path, out_path: Path, rel_path: str, report: MergeReport) -> str:
    ext_tree = parse_xml(ext_path)
    ext_obj = metadata_root_object(ext_tree)
    if not is_adopted(ext_obj):
        copy_file(ext_path, out_path)
        return "copy_native_metadata"

    base_tree = parse_xml(base_path)
    base_obj = metadata_root_object(base_tree)
    base_child = child(base_obj, "ChildObjects")
    ext_child = child(ext_obj, "ChildObjects")
    changed = False
    snippets: dict[tuple[str, str], str] = {}
    ext_text = read_text(ext_path)
    for span in span_map(ext_text).values():
        if span.local == "ChildObjects":
            for child_span in span.children:
                if child_span.end is not None:
                    snippets[(child_span.local, child_span.key)] = ext_text[child_span.start:child_span.end]
            break
    result_text = read_text(base_path)
    if base_child is not None and ext_child is not None:
        base_keys = _child_key_map(base_child)
        for item in children(ext_child):
            key = element_key(item)
            if key in base_keys:
                continue
            if is_adopted(item):
                report.add_warning("ADOPTED_CHILD_NOT_IN_BASE", rel_path, f"Заимствованный дочерний объект не найден в base: {key}")
                continue
            snippet = snippets.get(key)
            if not snippet:
                snippet = _simple_child_reference_snippet(item)
            target_container = next((s for s in span_map(result_text).values() if s.local == "ChildObjects"), None)
            if snippet and target_container:
                result_text = insert_before_close(result_text, target_container, snippet)
            else:
                report.add_warning("METADATA_CHILD_PATCH_FALLBACK_SKIPPED", rel_path, f"Не удалось минимально вставить {key}")
                continue
            changed = True
            report.objects["added"].append({
                "type": key[0],
                "name": key[1],
                "path": rel_path,
                "strategy": "metadata_native_child_from_extension",
            })

    if changed:
        write_patched_like_source(out_path, base_path, result_text)
        return "merge_adopted_metadata_native_children"
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
