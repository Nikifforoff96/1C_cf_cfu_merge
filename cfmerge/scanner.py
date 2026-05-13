from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .classifier import classify_path, classify_xml_root, object_locator
from .io_utils import normalize_rel
from .models import FileRecord
from .object_registry import read_object_ref_from_tree
from .xml_utils import parse_xml


def scan_tree(root: Path, progress_callback: Callable[[int, str], None] | None = None) -> dict[str, FileRecord]:
    records: dict[str, FileRecord] = {}
    owner_names: dict[str, str] = {}
    processed = 0
    for path in sorted(root.rglob("*"), key=lambda p: str(p.relative_to(root)).lower()):
        if not path.is_file():
            continue
        rel = normalize_rel(path.relative_to(root))
        stat = path.stat()
        object_type, object_name = object_locator(rel)
        kind = classify_path(rel)
        tree = None
        if rel.lower().endswith(".xml") and kind not in {"root_configuration", "config_dump_info", "form_visual_xml", "rights_xml", "unknown_xml"}:
            try:
                tree = parse_xml(path)
                kind = classify_xml_root(tree.getroot(), kind)
            except Exception:
                kind = "unknown_xml"
        object_ref = None
        if kind in {"metadata_xml", "form_object_xml"}:
            if tree is None:
                try:
                    tree = parse_xml(path)
                except Exception:
                    tree = None
            if tree is not None:
                object_ref = read_object_ref_from_tree(root, path, tree, owner_names)
            if object_ref is not None:
                object_type = object_ref.metadata_type
                object_name = object_ref.xml_name
                if object_ref.parent_path is None:
                    owner_names[object_ref.rel_path[:-4]] = object_ref.xml_name
        records[rel] = FileRecord(
            rel_path=rel,
            abs_path=path,
            kind=kind,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            object_type=object_type,
            object_name=object_name,
            object_ref=object_ref,
        )
        processed += 1
        if progress_callback is not None:
            progress_callback(processed, rel)
    return records
