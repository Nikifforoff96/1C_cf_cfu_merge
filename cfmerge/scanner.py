from __future__ import annotations

from pathlib import Path

from .classifier import classify_path, object_locator
from .io_utils import detect_encoding_and_newline, normalize_rel, sha256_file
from .models import FileRecord
from .object_registry import read_object_ref


def scan_tree(root: Path) -> dict[str, FileRecord]:
    records: dict[str, FileRecord] = {}
    for path in sorted(root.rglob("*"), key=lambda p: str(p.relative_to(root)).lower()):
        if not path.is_file():
            continue
        rel = normalize_rel(path.relative_to(root))
        encoding, newline = detect_encoding_and_newline(path)
        object_type, object_name = object_locator(rel)
        kind = classify_path(rel)
        if kind in {"metadata_xml", "form_object_xml"}:
            ref = read_object_ref(root, path)
            if ref is not None:
                object_type = ref.metadata_type
                object_name = ref.xml_name
        records[rel] = FileRecord(
            rel_path=rel,
            abs_path=path,
            kind=kind,
            encoding=encoding,
            newline=newline,
            sha256=sha256_file(path),
            object_type=object_type,
            object_name=object_name,
        )
    return records
