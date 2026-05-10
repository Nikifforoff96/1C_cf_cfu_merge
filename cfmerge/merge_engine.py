from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile

from .bsl_merge import EventHook, clean_extension_module, merge_bsl
from .classifier import classify_path
from .config_dump_info import regenerate_config_dump_info, validate_config_dump_info
from .conflicts import MergeConflict
from .form_merge import clean_native_form_xml, merge_form_visual
from .io_utils import copy_file, copy_tree_contents, prepare_output_dir, read_text, write_text
from .metadata_merge import merge_configuration, merge_metadata_object, xml_is_adopted
from .models import MergeAction, MergeConfig, MergeReport
from .object_registry import build_object_registry
from .report_merge import merge_configuration_report
from .reporters import write_human_report, write_json_report
from .scanner import scan_tree
from .validators import run_1c_validation, validate_bsl_tree, validate_xml_tree


def _is_xml_native(path: Path) -> bool:
    if not path.exists() or path.suffix.lower() != ".xml":
        return False
    return not xml_is_adopted(path)


def _prefix_for_xml_rel(rel: str) -> str:
    if rel.endswith(".xml"):
        return rel[:-4]
    return rel


def _owner_xml_rel(rel: str) -> str | None:
    parts = rel.split("/")
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}.xml"
    return None


def _module_for_form_visual(rel: str) -> str:
    return rel[:-len("Form.xml")] + "Form/Module.bsl"


def _record_added(report: MergeReport, rel: str, strategy: str) -> None:
    report.summary["files_added"] += 1
    report.add_action(MergeAction(path=rel, strategy=strategy))


def _record_changed(report: MergeReport, rel: str, strategy: str) -> None:
    report.summary["files_changed"] += 1
    report.add_action(MergeAction(path=rel, strategy=strategy))


def _copy_cfu_only_file(cfu_dir: Path, out_dir: Path, rel: str, report: MergeReport, copied_prefixes: set[str]) -> None:
    src = cfu_dir / rel
    dst = out_dir / rel
    kind = classify_path(rel)
    if rel in {"ConfigDumpInfo.xml", "Configuration.xml", "ОтчетПоКонфигурации.txt", "СобственныеОбъекты.txt"}:
        report.summary["files_skipped"] += 1
        return
    if kind in {"metadata_xml", "form_object_xml"}:
        if _is_xml_native(src):
            copy_file(src, dst)
            copied_prefixes.add(_prefix_for_xml_rel(rel))
            _record_added(report, rel, "copy_native_xml")
            return
        report.add_warning("ADOPTED_XML_WITHOUT_BASE_SKIPPED", rel, "Заимствованный XML без базового соответствия не скопирован")
        report.summary["files_skipped"] += 1
        return
    if kind == "form_visual_xml":
        clean_native_form_xml(src, dst)
        copied_prefixes.add(rel.rsplit("/Ext/Form.xml", 1)[0])
        _record_added(report, rel, "copy_native_form_visual_cleaned")
        return
    if kind == "bsl_module":
        text = read_text(src)
        write_text(dst, clean_extension_module(text), encoding="utf-8-sig", newline="crlf")
        _record_added(report, rel, "copy_orphan_extension_module_cleaned")
        return
    if any(rel == prefix or rel.startswith(prefix + "/") for prefix in copied_prefixes):
        copy_file(src, dst)
        _record_added(report, rel, "copy_native_resource")
        return
    owner = _owner_xml_rel(rel)
    if owner and _is_xml_native(cfu_dir / owner):
        copy_file(src, dst)
        _record_added(report, rel, "copy_native_owner_resource")
        return
    report.summary["files_skipped"] += 1


def merge(cfg: MergeConfig) -> MergeReport:
    cfg.cf_dir = cfg.cf_dir.resolve()
    cfg.cfu_dir = cfg.cfu_dir.resolve()
    cfg.out_dir = cfg.out_dir.resolve()

    if cfg.dry_run:
        original_out = cfg.out_dir
        with tempfile.TemporaryDirectory(prefix="cfmerge-dry-run-") as tmp:
            temp_cfg = replace(
                cfg,
                out_dir=Path(tmp) / "merged_cf",
                dry_run=False,
                force=True,
                backup=False,
                validate_1c=False,
                conflict_strategy="manual-review",
                fail_on_conflict=False,
            )
            report = merge(temp_cfg)
        report.input["out"] = str(original_out)
        report.input["dry_run"] = True
        report.diagnostics["dry_run"] = "full plan executed in temporary output; requested out directory was not changed"
        if cfg.report_path:
            write_json_report(report, cfg.report_path)
        if cfg.human_report_path:
            write_human_report(report, cfg.human_report_path)
        return report

    base_manifest = scan_tree(cfg.cf_dir)
    ext_manifest = scan_tree(cfg.cfu_dir)
    base_registry = build_object_registry(cfg.cf_dir)
    ext_registry = build_object_registry(cfg.cfu_dir)
    report = MergeReport()
    report.input = {
        "cf": str(cfg.cf_dir),
        "cfu": str(cfg.cfu_dir),
        "out": str(cfg.out_dir),
        "dry_run": cfg.dry_run,
    }
    report.summary["files_scanned_cf"] = len(base_manifest)
    report.summary["files_scanned_cfu"] = len(ext_manifest)
    report.diagnostics["one_extension_layer"] = True
    report.diagnostics["call_type_model"] = {"Before": "before", "After": "after", "Override": "override"}

    prepare_output_dir(cfg.out_dir, cfg.force, cfg.backup)
    copy_tree_contents(cfg.cf_dir, cfg.out_dir)
    report.summary["files_copied"] = len(base_manifest)

    copied_prefixes: set[str] = set()

    merge_configuration(
        cfg.cf_dir / "Configuration.xml",
        cfg.cfu_dir / "Configuration.xml",
        cfg.out_dir / "Configuration.xml",
        report,
        base_registry=base_registry,
        ext_registry=ext_registry,
    )

    # Metadata XML and form object XML. Visual form XML is handled later because it can create BSL hooks.
    for rel, ext_rec in sorted(ext_manifest.items()):
        if ext_rec.kind in {"root_configuration", "config_dump_info", "configuration_report", "form_visual_xml", "bsl_module"}:
            continue
        if ext_rec.kind not in {"metadata_xml", "form_object_xml"}:
            continue
        base_path = cfg.cf_dir / rel
        out_path = cfg.out_dir / rel
        if base_path.exists():
            strategy = merge_metadata_object(base_path, ext_rec.abs_path, out_path, rel, report)
            if strategy != "keep_base_adopted_metadata":
                _record_changed(report, rel, strategy)
            continue
        _copy_cfu_only_file(cfg.cfu_dir, cfg.out_dir, rel, report, copied_prefixes)

    event_hooks_by_module: dict[str, list[EventHook]] = {}
    for rel, ext_rec in sorted(ext_manifest.items()):
        if ext_rec.kind != "form_visual_xml":
            continue
        base_path = cfg.cf_dir / rel
        out_path = cfg.out_dir / rel
        module_rel = _module_for_form_visual(rel)
        module_text = read_text(cfg.cfu_dir / module_rel) if (cfg.cfu_dir / module_rel).exists() else None
        if base_path.exists():
            hooks = merge_form_visual(base_path, ext_rec.abs_path, out_path, rel, report, module_text=module_text)
            if hooks:
                event_hooks_by_module.setdefault(module_rel, []).extend(hooks)
            continue
        clean_native_form_xml(ext_rec.abs_path, out_path)
        copied_prefixes.add(rel.rsplit("/Ext/Form.xml", 1)[0])
        _record_added(report, rel, "copy_native_form_visual_cleaned")

    for rel, ext_rec in sorted(ext_manifest.items()):
        if ext_rec.kind != "bsl_module":
            continue
        base_path = cfg.cf_dir / rel
        out_path = cfg.out_dir / rel
        ext_text = read_text(ext_rec.abs_path)
        hooks = event_hooks_by_module.get(rel, [])
        if base_path.exists() or out_path.exists():
            base_text = read_text(out_path if out_path.exists() else base_path)
            try:
                result = merge_bsl(base_text, ext_text, rel, hooks)
            except MergeConflict as exc:
                report.add_conflict(exc.code, rel, exc.details, method=exc.method, context=exc.context)
                continue
            except Exception as exc:
                report.add_conflict("BSL_MERGE_FAILED", rel, str(exc))
                continue
            write_text(out_path, result.text, encoding="utf-8-sig", newline="crlf")
            for warning in result.warnings:
                report.add_warning("BSL_MERGE_WARNING", rel, warning)
            if result.actions:
                _record_changed(report, rel, ",".join(result.actions[:10]))
                report.objects["modified"].append({
                    "type": "BslModule",
                    "name": rel,
                    "path": rel,
                    "strategy": "bsl_semantic_merge",
                    "actions": result.actions,
                })
            continue
        cleaned = clean_extension_module(ext_text)
        write_text(out_path, cleaned, encoding="utf-8-sig", newline="crlf")
        _record_added(report, rel, "copy_extension_module_cleaned")

    for rel, ext_rec in sorted(ext_manifest.items()):
        if (cfg.out_dir / rel).exists():
            continue
        if ext_rec.kind in {"root_configuration", "config_dump_info", "configuration_report", "metadata_xml", "form_object_xml", "form_visual_xml", "bsl_module"}:
            continue
        _copy_cfu_only_file(cfg.cfu_dir, cfg.out_dir, rel, report, copied_prefixes)

    merge_configuration_report(
        cfg.cf_dir / "ОтчетПоКонфигурации.txt",
        cfg.cfu_dir / "ОтчетПоКонфигурации.txt",
        cfg.out_dir / "ОтчетПоКонфигурации.txt",
        report,
    )
    regenerate_config_dump_info(
        cfg.out_dir,
        cfg.cf_dir / "ConfigDumpInfo.xml",
        cfg.cfu_dir / "ConfigDumpInfo.xml",
        report,
    )
    validate_config_dump_info(cfg.out_dir, cfg.cf_dir / "ConfigDumpInfo.xml", cfg.cfu_dir / "ConfigDumpInfo.xml", ext_registry, report)

    if cfg.validate_xml:
        validate_xml_tree(cfg.out_dir, report)
    if cfg.validate_bsl:
        validate_bsl_tree(cfg.out_dir, report)
    if cfg.validate_1c:
        run_1c_validation(cfg, report)

    if report.conflicts:
        report.status = "failed"
    elif report.warnings and report.status == "completed":
        report.status = "completed_with_warnings"

    if cfg.report_path:
        write_json_report(report, cfg.report_path)
    if cfg.human_report_path:
        write_human_report(report, cfg.human_report_path)
    return report
