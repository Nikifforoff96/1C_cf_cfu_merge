from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import tempfile
from time import perf_counter

from .bsl_merge import EventHook, clean_extension_module, merge_bsl
from .classifier import classify_file
from .config_dump_info import regenerate_config_dump_info, validate_config_dump_info
from .conflicts import MergeConflict
from .external_command_interface import copy_command_interface_resource
from .form_merge import clean_native_form_xml, merge_form_visual
from .io_utils import copy_file, copy_tree_contents, prepare_output_dir, read_text, write_text
from .metadata_merge import merge_configuration, merge_metadata_object, metadata_full_name, xml_is_adopted
from .models import MergeAction, MergeConfig, MergeReport, ProgressEvent
from .object_registry import build_object_registry_from_records, build_result_object_registry
from .progress import ProgressTracker, merge_progress_phases
from .report_merge import merge_configuration_report
from .reporters import write_human_report, write_json_report
from .role_rights_merge import copy_role_rights, merge_role_rights
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


def _emit_progress(
    cfg: MergeConfig,
    level: str,
    stage: str,
    message: str,
    path: Path | str | None = None,
) -> None:
    if cfg.progress_callback is None:
        return
    cfg.progress_callback(
        ProgressEvent(
            time=datetime.now().strftime("%H:%M:%S"),
            level=level,
            stage=stage,
            message=message,
            path=str(path) if path is not None else None,
            event_type="log",
        )
    )


@contextmanager
def _timed(report: MergeReport, name: str):
    started = perf_counter()
    try:
        yield
    finally:
        _record_timing(report, name, started)


def _record_timing(report: MergeReport, name: str, started: float) -> None:
    timings = report.diagnostics.setdefault("timings", {})
    timings[name] = round(perf_counter() - started, 3)


def _result_paths(out_dir: Path, manifests: list[dict[str, object]], suffix: str) -> list[Path]:
    rels: set[str] = set()
    for manifest in manifests:
        rels.update(rel for rel in manifest if rel.lower().endswith(suffix))
    return [out_dir / rel for rel in rels if (out_dir / rel).exists()]


def _record_added(report: MergeReport, rel: str, strategy: str) -> None:
    report.summary["files_added"] += 1
    report.add_action(MergeAction(path=rel, strategy=strategy))


def _record_changed(report: MergeReport, rel: str, strategy: str) -> None:
    report.summary["files_changed"] += 1
    report.add_action(MergeAction(path=rel, strategy=strategy))


def _record_resource_skipped(report: MergeReport, rel: str, action: str, reason: str) -> None:
    report.summary["files_skipped"] += 1
    report.add_metadata_action(
        object_path=rel,
        object_type="ResourceXml",
        property_path=None,
        action=action,
        old_value=None,
        new_value=None,
        reason=reason,
        source_path=rel,
    )


def _copy_resource_file(
    src: Path,
    dst: Path,
    rel: str,
    kind: str,
    report: MergeReport,
    base_config_name: str | None,
    ext_config_name: str | None,
    result_registry=None,
) -> str:
    if kind == "rights_xml":
        return copy_role_rights(src, dst, rel, report, base_config_name, ext_config_name).strategy
    if kind == "command_interface_xml":
        return copy_command_interface_resource(src, dst, rel, report, registry=result_registry)
    copy_file(src, dst)
    return "copy_native_resource"


def _copy_cfu_only_file(
    cfu_dir: Path,
    out_dir: Path,
    rel: str,
    report: MergeReport,
    copied_prefixes: set[str],
    base_config_name: str | None = None,
    ext_config_name: str | None = None,
    result_registry=None,
) -> None:
    src = cfu_dir / rel
    dst = out_dir / rel
    kind = classify_file(src, rel)
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
    if kind in {"rights_xml", "unknown_xml", "command_interface_xml"}:
        if any(rel == prefix or rel.startswith(prefix + "/") for prefix in copied_prefixes):
            strategy = _copy_resource_file(src, dst, rel, kind, report, base_config_name, ext_config_name, result_registry)
            _record_added(report, rel, strategy)
            return
        owner = _owner_xml_rel(rel)
        if owner and _is_xml_native(cfu_dir / owner):
            strategy = _copy_resource_file(src, dst, rel, kind, report, base_config_name, ext_config_name, result_registry)
            _record_added(report, rel, "copy_native_owner_resource_rebased" if strategy.endswith("_rebased") else "copy_native_owner_resource")
            return
        report.add_warning("UNSUPPORTED_RESOURCE_XML_SKIPPED", rel, "Resource XML was not copied without a native extension owner")
        _record_resource_skipped(report, rel, "unsupported_resource_xml", "no_native_extension_owner")
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
    _record_resource_skipped(report, rel, "auxiliary_xml_skipped", "unsupported_or_adopted_owner")


def merge(cfg: MergeConfig) -> MergeReport:
    cfg.cf_dir = cfg.cf_dir.resolve()
    cfg.cfu_dir = cfg.cfu_dir.resolve()
    cfg.out_dir = cfg.out_dir.resolve()
    _emit_progress(cfg, "Инфо", "Проверка входов", "Пути объединения подготовлены", f"{cfg.cf_dir} -> {cfg.out_dir}")

    if cfg.dry_run:
        original_out = cfg.out_dir
        _emit_progress(cfg, "Инфо", "Пробный запуск", "Результат будет сформирован во временном каталоге", original_out)
        with tempfile.TemporaryDirectory(prefix="cfmerge-dry-run-") as tmp:
            temp_out = Path(tmp) / "merged_cf"
            temp_out_text = str(temp_out)
            original_out_text = str(original_out)

            def dry_run_progress(event: ProgressEvent) -> None:
                if cfg.progress_callback is None:
                    return
                path = event.path
                if path is not None:
                    path = path.replace(temp_out_text, original_out_text)
                cfg.progress_callback(replace(event, path=path))

            temp_cfg = replace(
                cfg,
                out_dir=temp_out,
                dry_run=False,
                force=True,
                backup=False,
                validate_1c=False,
                conflict_strategy="manual-review",
                fail_on_conflict=False,
                progress_callback=dry_run_progress if cfg.progress_callback is not None else None,
            )
            report = merge(temp_cfg)
        report.input["out"] = str(original_out)
        report.input["dry_run"] = True
        report.diagnostics["dry_run"] = "full plan executed in temporary output; requested out directory was not changed"
        if cfg.report_path:
            _emit_progress(cfg, "Инфо", "Отчеты", "Запись JSON-отчета", cfg.report_path)
            write_json_report(report, cfg.report_path)
        if cfg.human_report_path:
            _emit_progress(cfg, "Инфо", "Отчеты", "Запись текстового отчета", cfg.human_report_path)
            write_human_report(report, cfg.human_report_path)
        level = "Ошибка" if report.status == "failed" else "Предупреждение" if report.status == "completed_with_warnings" else "Успех"
        _emit_progress(cfg, level, "Завершение", f"Пробный запуск завершен: {report.status}", original_out)
        return report

    report = MergeReport()
    report.input = {
        "cf": str(cfg.cf_dir),
        "cfu": str(cfg.cfu_dir),
        "out": str(cfg.out_dir),
        "dry_run": cfg.dry_run,
    }
    report.diagnostics["one_extension_layer"] = True
    report.diagnostics["call_type_model"] = {"Before": "before", "After": "after", "Override": "override"}
    progress = ProgressTracker(
        cfg.progress_callback,
        merge_progress_phases(validate_xml=cfg.validate_xml, validate_bsl=cfg.validate_bsl, validate_1c=cfg.validate_1c),
    )

    _emit_progress(cfg, "Инфо", "Сканирование", "Сканирование исходных каталогов")
    progress.start("scan", message="Сканирование исходных каталогов", current=0, unit="файлов")
    with _timed(report, "scan"):
        scan_processed = 0

        def scan_progress(_current: int, _rel: str) -> None:
            nonlocal scan_processed
            scan_processed += 1
            progress.update("scan", scan_processed, unit="файлов", message=f"Сканирование: {scan_processed} файлов")

        base_manifest = scan_tree(cfg.cf_dir, progress_callback=scan_progress)
        ext_manifest = scan_tree(cfg.cfu_dir, progress_callback=scan_progress)
    progress.done(
        "scan",
        message=f"Найдено файлов: cf - {len(base_manifest)}, cfu - {len(ext_manifest)}",
        current=len(base_manifest) + len(ext_manifest),
        total=len(base_manifest) + len(ext_manifest),
        unit="файлов",
    )
    _emit_progress(
        cfg,
        "Инфо",
        "Сканирование",
        f"Найдено файлов: cf - {len(base_manifest)}, cfu - {len(ext_manifest)}",
    )
    _emit_progress(cfg, "Инфо", "Сканирование", "Построение реестров объектов")
    progress.start("build_registries", message="Построение реестров объектов", current=0, total=1, unit="этапов")
    with _timed(report, "build_registries"):
        base_registry = build_object_registry_from_records(cfg.cf_dir, base_manifest)
        ext_registry = build_object_registry_from_records(cfg.cfu_dir, ext_manifest)
        base_config_name = metadata_full_name(cfg.cf_dir / "Configuration.xml")[1]
        ext_config_name = metadata_full_name(cfg.cfu_dir / "Configuration.xml")[1]
    progress.done("build_registries", message="Реестры объектов построены", total=1, unit="этапов")
    report.summary["files_scanned_cf"] = len(base_manifest)
    report.summary["files_scanned_cfu"] = len(ext_manifest)

    _emit_progress(cfg, "Инфо", "Подготовка результата", "Подготовка каталога результата", cfg.out_dir)
    copy_started = perf_counter()
    prepare_output_dir(cfg.out_dir, cfg.force, cfg.backup)
    _emit_progress(cfg, "Инфо", "Подготовка результата", "Копирование основной конфигурации", cfg.cf_dir)
    progress.start(
        "copy_base_configuration",
        message="Копирование основной конфигурации",
        current=0,
        total=len(base_manifest),
        unit="файлов",
        path=str(cfg.cf_dir),
    )
    copy_tree_contents(
        cfg.cf_dir,
        cfg.out_dir,
        progress_callback=lambda current, rel: progress.update(
            "copy_base_configuration",
            current,
            total=len(base_manifest),
            unit="файлов",
            message=f"Копирование основной конфигурации: {current} из {len(base_manifest)}",
            path=rel,
        ),
    )
    _record_timing(report, "copy_base_configuration", copy_started)
    progress.done(
        "copy_base_configuration",
        message=f"Скопировано файлов: {len(base_manifest)}",
        total=len(base_manifest),
        unit="файлов",
        path=str(cfg.out_dir),
    )
    report.summary["files_copied"] = len(base_manifest)
    _emit_progress(cfg, "Инфо", "Подготовка результата", f"Скопировано файлов: {len(base_manifest)}", cfg.out_dir)

    copied_prefixes: set[str] = set()

    _emit_progress(cfg, "Инфо", "Слияние конфигурации", "Слияние корневого Configuration.xml")
    progress.start("merge_configuration", message="Слияние корневого Configuration.xml", current=0, total=1, unit="этапов")
    configuration_started = perf_counter()
    merge_configuration(
        cfg.cf_dir / "Configuration.xml",
        cfg.cfu_dir / "Configuration.xml",
        cfg.out_dir / "Configuration.xml",
        report,
        base_registry=base_registry,
        ext_registry=ext_registry,
    )
    _record_timing(report, "merge_configuration", configuration_started)
    progress.done("merge_configuration", message="Configuration.xml обработан", total=1, unit="этапов")

    # Metadata XML and form object XML. Visual form XML is handled later because it can create BSL hooks.
    metadata_items = [
        (rel, ext_rec)
        for rel, ext_rec in sorted(ext_manifest.items())
        if ext_rec.kind in {"metadata_xml", "form_object_xml"}
    ]
    metadata_total = len(metadata_items)
    _emit_progress(cfg, "Инфо", "Метаданные", f"Слияние объектов метаданных: {metadata_total}")
    progress.start("merge_metadata", message=f"Слияние объектов метаданных: {metadata_total}", current=0, total=metadata_total, unit="объектов")
    metadata_started = perf_counter()
    metadata_processed = 0
    for metadata_processed, (rel, ext_rec) in enumerate(metadata_items, 1):
        base_path = cfg.cf_dir / rel
        out_path = cfg.out_dir / rel
        if base_path.exists():
            strategy = merge_metadata_object(base_path, ext_rec.abs_path, out_path, rel, report)
            if strategy != "keep_base_adopted_metadata":
                _record_changed(report, rel, strategy)
            progress.update("merge_metadata", metadata_processed, total=metadata_total, unit="объектов", path=rel)
            continue
        _copy_cfu_only_file(cfg.cfu_dir, cfg.out_dir, rel, report, copied_prefixes, base_config_name, ext_config_name)
        progress.update("merge_metadata", metadata_processed, total=metadata_total, unit="объектов", path=rel)
    _record_timing(report, "merge_metadata", metadata_started)
    progress.done("merge_metadata", message=f"Обработано объектов метаданных: {metadata_processed}", current=metadata_processed, total=metadata_total, unit="объектов")
    _emit_progress(cfg, "Инфо", "Метаданные", f"Обработано объектов метаданных: {metadata_processed}")

    event_hooks_by_module: dict[str, list[EventHook]] = {}
    form_items = [(rel, ext_rec) for rel, ext_rec in sorted(ext_manifest.items()) if ext_rec.kind == "form_visual_xml"]
    form_total = len(form_items)
    _emit_progress(cfg, "Инфо", "Формы", f"Слияние управляемых форм: {form_total}")
    progress.start("merge_forms", message=f"Слияние управляемых форм: {form_total}", current=0, total=form_total, unit="форм")
    forms_started = perf_counter()
    form_processed = 0
    for form_processed, (rel, ext_rec) in enumerate(form_items, 1):
        base_path = cfg.cf_dir / rel
        out_path = cfg.out_dir / rel
        module_rel = _module_for_form_visual(rel)
        module_text = read_text(cfg.cfu_dir / module_rel) if (cfg.cfu_dir / module_rel).exists() else None
        if base_path.exists():
            hooks = merge_form_visual(base_path, ext_rec.abs_path, out_path, rel, report, module_text=module_text)
            if hooks:
                event_hooks_by_module.setdefault(module_rel, []).extend(hooks)
            progress.update("merge_forms", form_processed, total=form_total, unit="форм", path=rel)
            continue
        clean_native_form_xml(ext_rec.abs_path, out_path)
        copied_prefixes.add(rel.rsplit("/Ext/Form.xml", 1)[0])
        _record_added(report, rel, "copy_native_form_visual_cleaned")
        progress.update("merge_forms", form_processed, total=form_total, unit="форм", path=rel)
    _record_timing(report, "merge_forms", forms_started)
    progress.done("merge_forms", message=f"Обработано форм: {form_processed}", current=form_processed, total=form_total, unit="форм")
    _emit_progress(cfg, "Инфо", "Формы", f"Обработано форм: {form_processed}")

    bsl_items = [(rel, ext_rec) for rel, ext_rec in sorted(ext_manifest.items()) if ext_rec.kind == "bsl_module"]
    bsl_total = len(bsl_items)
    _emit_progress(cfg, "Инфо", "BSL", f"Слияние модулей BSL: {bsl_total}")
    progress.start("merge_bsl", message=f"Слияние модулей BSL: {bsl_total}", current=0, total=bsl_total, unit="модулей")
    bsl_started = perf_counter()
    bsl_processed = 0
    for bsl_processed, (rel, ext_rec) in enumerate(bsl_items, 1):
        base_path = cfg.cf_dir / rel
        out_path = cfg.out_dir / rel
        ext_text = read_text(ext_rec.abs_path)
        hooks = event_hooks_by_module.get(rel, [])
        if base_path.exists() or out_path.exists():
            base_text = read_text(out_path if out_path.exists() else base_path)
            try:
                result = merge_bsl(base_text, ext_text, rel, hooks)
            except MergeConflict as exc:
                if exc.code == "TARGET_METHOD_NOT_FOUND":
                    report.add_warning(exc.code, rel, exc.details, method=exc.method, context=exc.context)
                    progress.update("merge_bsl", bsl_processed, total=bsl_total, unit="модулей", path=rel)
                    continue
                report.add_conflict(exc.code, rel, exc.details, method=exc.method, context=exc.context)
                progress.update("merge_bsl", bsl_processed, total=bsl_total, unit="модулей", path=rel)
                continue
            except Exception as exc:
                report.add_conflict("BSL_MERGE_FAILED", rel, str(exc))
                progress.update("merge_bsl", bsl_processed, total=bsl_total, unit="модулей", path=rel)
                continue
            write_text(out_path, result.text, encoding="utf-8-sig", newline="crlf")
            for warning in result.warnings:
                report.add_warning("BSL_MERGE_WARNING", rel, warning)
            for warning in result.warning_records:
                report.add_warning(warning.code, rel, warning.details, method=warning.method, context=warning.context)
            if result.actions:
                _record_changed(report, rel, ",".join(result.actions[:10]))
                report.objects["modified"].append({
                    "type": "BslModule",
                    "name": rel,
                    "path": rel,
                    "strategy": "bsl_semantic_merge",
                    "actions": result.actions,
                })
            progress.update("merge_bsl", bsl_processed, total=bsl_total, unit="модулей", path=rel)
            continue
        cleaned = clean_extension_module(ext_text)
        write_text(out_path, cleaned, encoding="utf-8-sig", newline="crlf")
        _record_added(report, rel, "copy_extension_module_cleaned")
        progress.update("merge_bsl", bsl_processed, total=bsl_total, unit="модулей", path=rel)
    _record_timing(report, "merge_bsl", bsl_started)
    progress.done("merge_bsl", message=f"Обработано модулей BSL: {bsl_processed}", current=bsl_processed, total=bsl_total, unit="модулей")
    _emit_progress(cfg, "Инфо", "BSL", f"Обработано модулей BSL: {bsl_processed}")

    progress.start("build_result_registry", message="Построение реестра результата", current=0, total=1, unit="этапов")
    with _timed(report, "build_result_registry"):
        result_registry = build_result_object_registry(base_registry, ext_registry, cfg.out_dir)
    progress.done("build_result_registry", message="Реестр результата построен", total=1, unit="этапов")

    resource_processed = 0
    resource_skip_kinds = {"root_configuration", "config_dump_info", "configuration_report", "metadata_xml", "form_object_xml", "form_visual_xml", "bsl_module"}
    resource_items = [
        (rel, ext_rec)
        for rel, ext_rec in sorted(ext_manifest.items())
        if ext_rec.kind not in resource_skip_kinds
    ]
    resource_total = len(resource_items)
    _emit_progress(cfg, "Инфо", "Ресурсы", "Обработка ресурсов расширения")
    progress.start("merge_resources", message=f"Обработка ресурсов расширения: {resource_total}", current=0, total=resource_total, unit="файлов")
    resources_started = perf_counter()
    for resource_seen, (rel, ext_rec) in enumerate(resource_items, 1):
        if (cfg.out_dir / rel).exists():
            if ext_rec.kind == "rights_xml" and (cfg.cf_dir / rel).exists() and ext_rec.sha256 != base_manifest.get(rel, ext_rec).sha256:
                resource_processed += 1
                result = merge_role_rights(
                    cfg.cf_dir / rel,
                    ext_rec.abs_path,
                    cfg.out_dir / rel,
                    rel,
                    report,
                    base_config_name,
                    ext_config_name,
                )
                if result.changed:
                    _record_changed(report, rel, result.strategy)
            elif ext_rec.kind == "unknown_xml" and (cfg.cf_dir / rel).exists() and ext_rec.sha256 != base_manifest.get(rel, ext_rec).sha256:
                resource_processed += 1
                report.add_warning(
                    "UNSUPPORTED_RESOURCE_XML_SKIPPED",
                    rel,
                    "Existing base resource XML was not overwritten by extension XML",
                )
                _record_resource_skipped(report, rel, "unsupported_resource_xml", "existing_base_resource_not_overwritten")
            progress.update("merge_resources", resource_seen, total=resource_total, unit="файлов", path=rel)
            continue
        resource_processed += 1
        _copy_cfu_only_file(cfg.cfu_dir, cfg.out_dir, rel, report, copied_prefixes, base_config_name, ext_config_name, result_registry)
        progress.update("merge_resources", resource_seen, total=resource_total, unit="файлов", path=rel)
    _record_timing(report, "merge_resources", resources_started)
    progress.done("merge_resources", message=f"Обработано ресурсов: {resource_processed}", current=resource_total, total=resource_total, unit="файлов")
    _emit_progress(cfg, "Инфо", "Ресурсы", f"Обработано ресурсов: {resource_processed}")

    _emit_progress(cfg, "Инфо", "Отчет конфигурации", "Слияние ОтчетПоКонфигурации.txt")
    progress.start("merge_configuration_report", message="Слияние ОтчетПоКонфигурации.txt", current=0, total=1, unit="этапов")
    configuration_report_started = perf_counter()
    merge_configuration_report(
        cfg.cf_dir / "ОтчетПоКонфигурации.txt",
        cfg.cfu_dir / "ОтчетПоКонфигурации.txt",
        cfg.out_dir / "ОтчетПоКонфигурации.txt",
        report,
    )
    _record_timing(report, "merge_configuration_report", configuration_report_started)
    progress.done("merge_configuration_report", message="ОтчетПоКонфигурации.txt обработан", total=1, unit="этапов")
    progress.start("config_dump_info", message="Обработка ConfigDumpInfo.xml", current=0, total=2, unit="шагов")
    _emit_progress(cfg, "Инфо", "ConfigDumpInfo", "Регенерация ConfigDumpInfo.xml")
    config_dump_regenerate_started = perf_counter()
    regenerate_config_dump_info(
        cfg.out_dir,
        cfg.cf_dir / "ConfigDumpInfo.xml",
        cfg.cfu_dir / "ConfigDumpInfo.xml",
        report,
        base_manifest=base_manifest,
    )
    _record_timing(report, "regenerate_config_dump_info", config_dump_regenerate_started)
    progress.update("config_dump_info", 1, total=2, unit="шагов", message="ConfigDumpInfo.xml регенерирован", force=True)
    _emit_progress(cfg, "Инфо", "ConfigDumpInfo", "Проверка ConfigDumpInfo.xml")
    config_dump_validate_started = perf_counter()
    validate_config_dump_info(cfg.out_dir, cfg.cf_dir / "ConfigDumpInfo.xml", cfg.cfu_dir / "ConfigDumpInfo.xml", ext_registry, report)
    _record_timing(report, "validate_config_dump_info", config_dump_validate_started)
    progress.done("config_dump_info", message="ConfigDumpInfo.xml проверен", total=2, unit="шагов")

    if cfg.validate_xml:
        _emit_progress(cfg, "Инфо", "Валидация", "Проверка XML результата", cfg.out_dir)
        xml_paths = _result_paths(cfg.out_dir, [base_manifest, ext_manifest], ".xml")
        progress.start("validate_xml", message=f"Проверка XML результата: {len(xml_paths)}", current=0, total=len(xml_paths), unit="файлов", path=str(cfg.out_dir))
        validate_xml_started = perf_counter()
        validate_xml_tree(
            cfg.out_dir,
            report,
            base_dir=cfg.cf_dir,
            registry=result_registry,
            base_manifest=base_manifest,
            xml_paths=xml_paths,
            progress_callback=lambda current, total, path: progress.update(
                "validate_xml",
                current,
                total=total,
                unit="файлов",
                path=str(path),
            ),
        )
        _record_timing(report, "validate_xml", validate_xml_started)
        progress.done("validate_xml", message="Проверка XML результата завершена", total=len(xml_paths), unit="файлов")
    if cfg.validate_bsl:
        _emit_progress(cfg, "Инфо", "Валидация", "Проверка BSL результата", cfg.out_dir)
        bsl_paths = _result_paths(cfg.out_dir, [base_manifest, ext_manifest], ".bsl")
        progress.start("validate_bsl", message=f"Проверка BSL результата: {len(bsl_paths)}", current=0, total=len(bsl_paths), unit="файлов", path=str(cfg.out_dir))
        validate_bsl_started = perf_counter()
        validate_bsl_tree(
            cfg.out_dir,
            report,
            bsl_paths=bsl_paths,
            base_manifest=base_manifest,
            progress_callback=lambda current, total, path: progress.update(
                "validate_bsl",
                current,
                total=total,
                unit="файлов",
                path=str(path),
            ),
        )
        _record_timing(report, "validate_bsl", validate_bsl_started)
        progress.done("validate_bsl", message="Проверка BSL результата завершена", total=len(bsl_paths), unit="файлов")
    if cfg.validate_1c:
        _emit_progress(cfg, "Инфо", "Валидация 1С", "Запуск проверки через локальные инструменты 1С", cfg.out_dir)
        progress.start("validate_1c", message="Запуск проверки через локальные инструменты 1С", current=0, total=1, unit="этапов", path=str(cfg.out_dir))
        validate_1c_started = perf_counter()
        run_1c_validation(cfg, report)
        _record_timing(report, "validate_1c", validate_1c_started)
        progress.done("validate_1c", message="Проверка через локальные инструменты 1С завершена", total=1, unit="этапов", path=str(cfg.out_dir))

    if report.conflicts:
        report.status = "failed"
    elif report.warnings and report.status == "completed":
        report.status = "completed_with_warnings"

    report_targets = [path for path in (cfg.report_path, cfg.human_report_path) if path is not None]
    reports_started = perf_counter()
    progress.start("write_reports", message=f"Запись отчетов: {len(report_targets)}", current=0, total=len(report_targets), unit="файлов")
    reports_written = 0
    if cfg.report_path:
        _emit_progress(cfg, "Инфо", "Отчеты", "Запись JSON-отчета", cfg.report_path)
        write_json_report(report, cfg.report_path)
        reports_written += 1
        progress.update("write_reports", reports_written, total=len(report_targets), unit="файлов", path=str(cfg.report_path), force=True)
    if cfg.human_report_path:
        _emit_progress(cfg, "Инфо", "Отчеты", "Запись текстового отчета", cfg.human_report_path)
        write_human_report(report, cfg.human_report_path)
        reports_written += 1
        progress.update("write_reports", reports_written, total=len(report_targets), unit="файлов", path=str(cfg.human_report_path), force=True)
    _record_timing(report, "write_reports", reports_started)
    progress.done("write_reports", message="Запись отчетов завершена", current=reports_written, total=len(report_targets), unit="файлов")
    level = "Ошибка" if report.status == "failed" else "Предупреждение" if report.status == "completed_with_warnings" else "Успех"
    _emit_progress(cfg, level, "Завершение", f"Объединение завершено: {report.status}", cfg.out_dir)
    return report
