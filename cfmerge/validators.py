from __future__ import annotations

import subprocess
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from pathlib import Path

from .classifier import TYPE_TO_DIR
from .external_command_interface import validate_command_interface_resource
from .form_validator import validate_form_result
from .io_utils import normalize_rel
from .models import FileRecord, MergeConfig, MergeReport
from .object_registry import ObjectRegistry, build_object_registry
from .xml_utils import child, children, local_name, parse_xml


def _configuration_child_refs(cfg_path: Path) -> set[tuple[str, str]]:
    tree = parse_xml(cfg_path)
    cfg_obj = next((item for item in list(tree.getroot()) if isinstance(item.tag, str)), None)
    child_objects = child(cfg_obj, "ChildObjects") if cfg_obj is not None else None
    refs: set[tuple[str, str]] = set()
    if child_objects is None:
        return refs
    for item in children(child_objects):
        refs.add((local_name(item.tag), (item.text or "").strip()))
    return refs


def _parse_xml_bytes(data: bytes) -> ET.ElementTree:
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    return ET.ElementTree(ET.fromstring(data, parser=parser))


def _is_unchanged_base_copy(path: Path, out_dir: Path, base_manifest: dict[str, FileRecord] | None) -> bool:
    if base_manifest is None:
        return False
    try:
        rel = normalize_rel(path.relative_to(out_dir))
        base_record = base_manifest.get(rel)
        current = path.stat()
    except (OSError, ValueError):
        return False
    return base_record is not None and base_record.size == current.st_size and base_record.mtime_ns == current.st_mtime_ns


def validate_xml_tree(
    out_dir: Path,
    report: MergeReport,
    base_dir: Path | None = None,
    registry: ObjectRegistry | None = None,
    base_manifest: dict[str, FileRecord] | None = None,
    xml_paths: Iterable[Path] | None = None,
    progress_callback: Callable[[int, int, Path], None] | None = None,
) -> None:
    errors: list[str] = []
    prefix_errors: list[str] = []
    calltype_errors: list[str] = []
    baseform_errors: list[str] = []
    adopted_leaks: list[str] = []
    registry = registry or build_object_registry(out_dir)
    paths = (
        sorted(xml_paths, key=lambda p: normalize_rel(p.relative_to(out_dir)).lower())
        if xml_paths is not None
        else sorted(out_dir.rglob("*.xml"), key=lambda p: normalize_rel(p.relative_to(out_dir)).lower())
    )
    skipped_unchanged_base = 0
    total_paths = len(paths)
    for index, path in enumerate(paths, 1):
        try:
            if _is_unchanged_base_copy(path, out_dir, base_manifest):
                skipped_unchanged_base += 1
                continue
            try:
                data = path.read_bytes()
            except OSError as exc:
                errors.append(f"{path}: {exc}")
                continue
            text = data.decode("utf-8-sig", errors="ignore")
            try:
                _parse_xml_bytes(data)
            except Exception as exc:
                errors.append(f"{path}: {exc}")
                continue
            declared = set(re.findall(r"xmlns:([A-Za-z_][A-Za-z0-9_.-]*)=", text))
            values = []
            for match in re.finditer(r'\s([A-Za-z_][A-Za-z0-9_.:-]*)="([^"]*)"', text):
                attr_name = match.group(1)
                if attr_name in {"xsi:type", "type"} or attr_name.endswith(":type"):
                    values.append(match.group(2))
            values.extend(re.findall(r"<(?:[^:<>]+:)?Type(?:\s[^>]*)?>([^<>]+:[^<>]+)</(?:[^:<>]+:)?Type>", text))
            refs = set()
            for value in values:
                for ref in re.finditer(r"(?<![A-Za-z0-9_.-])([A-Za-z_][A-Za-z0-9_.-]*):[A-Za-z_А-Яа-яЁё]", value):
                    prefix = ref.group(1)
                    if prefix not in {"http", "https", "file", "mailto"}:
                        refs.add(prefix)
            missing = sorted(refs - declared)
            if missing:
                prefix_errors.append(f"{path}: не объявлены QName-префиксы {', '.join(missing)}")
            if path.name == "Form.xml" and 'callType="' in text:
                calltype_errors.append(str(path))
            if path.name == "Form.xml" and re.search(r"<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?BaseForm\b", text):
                baseform_errors.append(str(path))
            if path.name == "Form.xml":
                base_form_path = None
                if base_dir is not None and base_dir.exists():
                    try:
                        base_form_path = base_dir / path.relative_to(out_dir)
                    except ValueError:
                        base_form_path = None
                validate_form_result(path, report, base_form_path=base_form_path)
            if path.name == "CommandInterface.xml":
                validate_command_interface_resource(path, out_dir, registry, report)
            if path.name not in {"ConfigDumpInfo.xml"}:
                if (
                    re.search(r"<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?ObjectBelonging>\s*Adopted\s*</", text)
                    or re.search(r"<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?ExtendedConfigurationObject\b", text)
                    or re.search(r"<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?ConfigurationExtensionPurpose\b", text)
                    or re.search(r"<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?NamePrefix>\s*[^<\s]+", text)
                    or re.search(r'\bxsi:type="(?:[A-Za-z_][A-Za-z0-9_.-]*:)?ExtendedProperty"', text)
                ):
                    adopted_leaks.append(str(path))
        finally:
            if progress_callback is not None:
                progress_callback(index, total_paths, path)
    if errors:
        report.validation["xml_parse"] = "failed"
        for err in errors[:30]:
            report.add_conflict("XML_PARSE_FAILED", str(out_dir), err)
    else:
        report.validation["xml_parse"] = "passed"
    if prefix_errors:
        report.validation["xml_prefix_references"] = "failed"
        for err in prefix_errors[:30]:
            report.add_conflict("XML_PREFIX_REFERENCE_UNDECLARED", str(out_dir), err)
    else:
        report.validation["xml_prefix_references"] = "passed"
    if calltype_errors:
        report.validation["form_calltype_absent"] = "failed"
        for path in calltype_errors[:30]:
            report.add_conflict("FORM_CALLTYPE_LEFT", path, "В plain Form.xml остался callType")
    else:
        report.validation["form_calltype_absent"] = "passed"
    if baseform_errors:
        report.validation["form_baseform_absent"] = "failed"
        for path in baseform_errors[:30]:
            report.add_conflict("FORM_BASEFORM_LEFT", path, "В plain Form.xml остался BaseForm")
    else:
        report.validation["form_baseform_absent"] = "passed"
    if adopted_leaks:
        report.validation["adopted_wrappers_absent"] = "failed"
        for path in adopted_leaks[:30]:
            report.add_conflict("ADOPTED_WRAPPER_LEAKED", path, "В plain-result попали extension/adopted свойства")
    else:
        report.validation["adopted_wrappers_absent"] = "passed"

    missing_refs: list[str] = []
    cfg_path = out_dir / "Configuration.xml"
    if cfg_path.exists():
        try:
            for typ, name in _configuration_child_refs(cfg_path):
                if not name or typ not in TYPE_TO_DIR:
                    continue
                if registry.find(typ, name) is None:
                    missing_refs.append(f"{typ}.{name}")
        except Exception as exc:
            missing_refs.append(f"Configuration.xml parse/reference check failed: {exc}")
    if missing_refs:
        report.validation["configuration_child_references"] = "failed"
        for ref in missing_refs[:50]:
            report.add_conflict("CONFIGURATION_CHILD_OBJECT_FILE_MISSING", "Configuration.xml", ref)
    else:
        report.validation["configuration_child_references"] = "passed"

    base_missing_refs: list[str] = []
    base_missing_files: list[str] = []
    if base_dir is not None and base_dir.exists():
        base_cfg_path = base_dir / "Configuration.xml"
        if base_cfg_path.exists() and cfg_path.exists():
            try:
                for typ, name in sorted(_configuration_child_refs(base_cfg_path) - _configuration_child_refs(cfg_path)):
                    base_missing_refs.append(f"{typ}.{name}")
            except Exception as exc:
                base_missing_refs.append(f"Configuration.xml base-reference compare failed: {exc}")
        if base_manifest is not None:
            base_rels = sorted(base_manifest)
        else:
            base_rels = [
                normalize_rel(path.relative_to(base_dir))
                for path in sorted(base_dir.rglob("*"))
                if path.is_file()
            ]
        for rel in base_rels:
            if not (out_dir / rel).exists():
                base_missing_files.append(rel)

    if base_missing_refs:
        report.validation["base_configuration_children_preserved"] = "failed"
        for ref in base_missing_refs[:50]:
            report.add_conflict("CONFIGURATION_BASE_CHILD_REFERENCE_LOST", "Configuration.xml", ref)
    else:
        report.validation["base_configuration_children_preserved"] = "passed"

    if base_missing_files:
        report.validation["base_files_preserved"] = "failed"
        for rel in base_missing_files[:50]:
            report.add_conflict("BASE_FILE_MISSING_IN_MERGE_RESULT", rel, rel)
    else:
        report.validation["base_files_preserved"] = "passed"
    if skipped_unchanged_base:
        report.diagnostics["validation_xml_skipped_unchanged_base"] = skipped_unchanged_base


def validate_bsl_tree(
    out_dir: Path,
    report: MergeReport,
    bsl_paths: Iterable[Path] | None = None,
    base_manifest: dict[str, FileRecord] | None = None,
    progress_callback: Callable[[int, int, Path], None] | None = None,
) -> None:
    bad_markers: list[str] = []
    marker_re = re.compile(r"(?im)^[ \t]*(?:&(?:ИзменениеИКонтроль|Вместо|Перед|После)\b|#(?:Вставка|КонецВставки|Удаление|КонецУдаления|Вставить|КонецВставить|Удалить|КонецУдалить)\b)")
    paths = (
        sorted(bsl_paths, key=lambda p: normalize_rel(p.relative_to(out_dir)).lower())
        if bsl_paths is not None
        else sorted(out_dir.rglob("*.bsl"), key=lambda p: normalize_rel(p.relative_to(out_dir)).lower())
    )
    skipped_unchanged_base = 0
    total_paths = len(paths)
    for index, path in enumerate(paths, 1):
        try:
            if _is_unchanged_base_copy(path, out_dir, base_manifest):
                skipped_unchanged_base += 1
                continue
            text = path.read_text(encoding="utf-8-sig", errors="ignore")
            if marker_re.search(text):
                bad_markers.append(str(path))
        finally:
            if progress_callback is not None:
                progress_callback(index, total_paths, path)
    if bad_markers:
        report.validation["bsl_plain_markers"] = "failed"
        for path in bad_markers[:30]:
            report.add_conflict("BSL_EXTENSION_MARKER_LEFT", path, "В plain-result осталась расширенческая аннотация или блок")
    else:
        report.validation["bsl_plain_markers"] = "passed"
    if skipped_unchanged_base:
        report.diagnostics["validation_bsl_skipped_unchanged_base"] = skipped_unchanged_base


def _run(command: list[str], timeout: int = 3600) -> tuple[int, str]:
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return completed.returncode, completed.stdout


def run_1c_validation(cfg: MergeConfig, report: MergeReport) -> None:
    project_root = Path(__file__).resolve().parent.parent
    v8_path = cfg.v8_path or Path(r"C:\Program Files\1cv8\8.3.27.1644\bin")
    infobase = cfg.infobase_path or project_root / "test-infobase"
    cf_validate = project_root / "tools" / "1c-cf-manage" / "scripts" / "cf-validate.ps1"
    db_load = project_root / "tools" / "1c-db-ops" / "scripts" / "db-load-xml.ps1"
    db_update = project_root / "tools" / "1c-db-ops" / "scripts" / "db-update.ps1"
    db_dump = project_root / "tools" / "1c-db-ops" / "scripts" / "db-dump-cf.ps1"
    out_cf = cfg.out_dir.with_suffix(".cf")

    steps = [
        ("cf_validate_ps1", ["powershell.exe", "-NoProfile", "-File", str(cf_validate), "-ConfigPath", str(cfg.out_dir)]),
        ("db_load_xml_ps1", ["powershell.exe", "-NoProfile", "-File", str(db_load), "-V8Path", str(v8_path), "-InfoBasePath", str(infobase), "-ConfigDir", str(cfg.out_dir), "-Mode", "Full"]),
        ("db_update_ps1", ["powershell.exe", "-NoProfile", "-File", str(db_update), "-V8Path", str(v8_path), "-InfoBasePath", str(infobase)]),
        ("db_dump_cf_ps1", ["powershell.exe", "-NoProfile", "-File", str(db_dump), "-V8Path", str(v8_path), "-InfoBasePath", str(infobase), "-OutputFile", str(out_cf)]),
    ]
    outputs: dict[str, str] = {}
    for name, command in steps:
        code, output = _run(command)
        outputs[name] = output[-8000:]
        if code == 0:
            report.validation[name] = "passed"
        else:
            report.validation[name] = "failed"
            report.diagnostics[f"{name}_output"] = output[-20000:]
            report.add_conflict("VALIDATION_1C_FAILED", name, f"Команда завершилась с кодом {code}")
            break
    report.diagnostics["1c_outputs"] = outputs
