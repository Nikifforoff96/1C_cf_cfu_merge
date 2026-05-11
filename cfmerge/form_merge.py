from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from .bsl_merge import EventHook
from .form_model import load_form_model, strip_extension_artifacts, write_form_model
from .form_report import apply_form_stats
from .form_three_way_merge import merge_form_tree
from .models import MergeReport


def merge_form_visual(
    base_path: Path,
    ext_path: Path,
    out_path: Path,
    rel_path: str,
    report: MergeReport,
    module_text: str | None = None,
) -> list[EventHook]:
    base_model = load_form_model(base_path)
    ext_model = load_form_model(ext_path)
    ancestor_root = ext_model.base_form
    result = merge_form_tree(
        base_model.root,
        ancestor_root,
        ext_model.root,
        rel_path=rel_path,
        report=report,
        extension_module_text=module_text,
    )
    strip_extension_artifacts(base_model.root)
    write_form_model(out_path, base_model.tree, namespaces={**base_model.namespaces, **ext_model.namespaces})
    if result.hooks or any(value for value in asdict(result.stats).values()):
        report.summary["files_changed"] += 1
    apply_form_stats(report, rel_path, result.stats)
    return result.hooks


def clean_native_form_xml(src_path: Path, out_path: Path) -> None:
    model = load_form_model(src_path)
    strip_extension_artifacts(model.root)
    write_form_model(out_path, model.tree, namespaces=model.namespaces)
