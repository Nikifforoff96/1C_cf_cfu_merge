from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path

from .classifier import DIR_TO_TYPE
from .metadata_merge import metadata_full_name
from .models import MergeReport
from .object_registry import ObjectRegistry
from .xml_utils import NS_DUMP, child, children, local_name, parse_xml, write_xml


def _read_existing_entries(*paths: Path) -> dict[str, tuple[str, str | None]]:
    result: dict[str, tuple[str, str | None]] = {}
    for path in paths:
        if not path.exists():
            continue
        try:
            tree = parse_xml(path)
        except Exception:
            continue
        root = tree.getroot()
        versions = child(root, "ConfigVersions")
        if versions is None:
            continue
        for meta in versions.iter():
            if not isinstance(meta.tag, str) or local_name(meta.tag) != "Metadata":
                continue
            name = meta.attrib.get("name")
            ident = meta.attrib.get("id")
            version = meta.attrib.get("configVersion")
            if name and ident:
                result.setdefault(name, (ident, version))
    return result


def _version_for(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(data).hexdigest()


def _metadata_element(name: str, ident: str, version: str | None = None) -> ET.Element:
    attrib = {"name": name, "id": ident}
    if version:
        attrib["configVersion"] = version
    return ET.Element(f"{{{NS_DUMP}}}Metadata", attrib)


def _module_suffix(path: Path) -> str | None:
    name = path.name
    if name == "Module.bsl":
        return "Module"
    if name == "ObjectModule.bsl":
        return "ObjectModule"
    if name == "ManagerModule.bsl":
        return "ManagerModule"
    if name == "RecordSetModule.bsl":
        return "RecordSetModule"
    if name == "ManagedApplicationModule.bsl":
        return "ManagedApplicationModule"
    return None


def _external_xml_suffix(path: Path) -> str | None:
    if path.name == "Template.xml":
        return "Template"
    return None


def regenerate_config_dump_info(out_dir: Path, base_info: Path | None, ext_info: Path | None, report: MergeReport) -> None:
    base_known = _read_existing_entries(base_info) if base_info is not None else {}
    ext_known = _read_existing_entries(ext_info) if ext_info is not None else {}
    root = ET.Element(f"{{{NS_DUMP}}}ConfigDumpInfo", {
        "format": "Hierarchical",
        "version": "2.20",
    })
    versions = ET.SubElement(root, f"{{{NS_DUMP}}}ConfigVersions")

    entry_by_name: dict[str, ET.Element] = {
        full_name: _metadata_element(full_name, ident, old_version)
        for full_name, (ident, old_version) in base_known.items()
    }
    object_uuid: dict[tuple[str, str], str] = {}
    object_full: dict[tuple[str, str], str] = {}

    def _identity_for(full_name: str, default_ident: str) -> str:
        if full_name in base_known:
            return base_known[full_name][0]
        if full_name in ext_known:
            return ext_known[full_name][0]
        return default_ident

    def _upsert(full_name: str, default_ident: str, version: str | None) -> None:
        entry_by_name[full_name] = _metadata_element(full_name, _identity_for(full_name, default_ident), version)

    cfg_xml = out_dir / "Configuration.xml"
    root_typ, root_name, root_uuid = metadata_full_name(cfg_xml)
    if root_typ == "Configuration" and root_name and root_uuid:
        full = f"Configuration.{root_name}"
        _upsert(full, root_uuid, _version_for(cfg_xml))

    for dir_path in sorted([p for p in out_dir.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
        obj_type = DIR_TO_TYPE.get(dir_path.name)
        if not obj_type:
            continue
        for xml_path in sorted(dir_path.glob("*.xml"), key=lambda p: p.name.lower()):
            obj_typ, obj_name, obj_uuid = metadata_full_name(xml_path)
            if not obj_name or not obj_uuid:
                continue
            full = f"{obj_type}.{obj_name}"
            object_uuid[(dir_path.name, obj_name)] = obj_uuid
            object_full[(dir_path.name, obj_name)] = full
            _upsert(full, obj_uuid, _version_for(xml_path))

            obj_dir = dir_path / xml_path.stem
            forms_dir = obj_dir / "Forms"
            if forms_dir.exists():
                for form_xml in sorted(forms_dir.glob("*.xml"), key=lambda p: p.name.lower()):
                    ftyp, fname, fuuid = metadata_full_name(form_xml)
                    if not fname or not fuuid:
                        fname = form_xml.stem
                        fuuid = _identity_for(f"{full}.Form.{fname}", f"{obj_uuid}.{fname}")
                    form_full = f"{full}.Form.{fname}"
                    _upsert(form_full, fuuid, _version_for(form_xml))
                    visual = forms_dir / form_xml.stem / "Ext" / "Form.xml"
                    if visual.exists():
                        visual_full = f"{form_full}.Form"
                        _upsert(visual_full, f"{_identity_for(form_full, fuuid)}.0", _version_for(visual))

            ext_dir = obj_dir / "Ext"
            if ext_dir.exists():
                for bsl in sorted(ext_dir.glob("*.bsl"), key=lambda p: p.name.lower()):
                    suffix = _module_suffix(bsl)
                    if not suffix:
                        continue
                    module_full = f"{full}.{suffix}"
                    _upsert(module_full, f"{obj_uuid}.0", _version_for(bsl))

            for nested_xml in sorted(obj_dir.rglob("*.xml"), key=lambda p: str(p.relative_to(obj_dir)).lower()):
                rel_parts = nested_xml.relative_to(obj_dir).parts
                if not rel_parts or rel_parts[0] in {"Forms", "Ext"} or "Ext" in rel_parts:
                    continue
                child_typ, child_name, child_uuid = metadata_full_name(nested_xml)
                if not child_typ or not child_name or not child_uuid:
                    continue
                child_full = f"{full}.{child_typ}.{child_name}"
                _upsert(child_full, child_uuid, _version_for(nested_xml))
                child_ext_dir = nested_xml.parent / nested_xml.stem / "Ext"
                if child_ext_dir.exists():
                    for ext_xml in sorted(child_ext_dir.glob("*.xml"), key=lambda p: p.name.lower()):
                        suffix = _external_xml_suffix(ext_xml)
                        if suffix:
                            _upsert(f"{child_full}.{suffix}", f"{child_uuid}.0", _version_for(ext_xml))

    root_ext = out_dir / "Ext"
    if root_ext.exists():
        for bsl in sorted(root_ext.glob("*.bsl"), key=lambda p: p.name.lower()):
            suffix = _module_suffix(bsl)
            if not suffix or not root_name or not root_uuid:
                continue
            full = f"Configuration.{root_name}.{suffix}"
            _upsert(full, f"{root_uuid}.0", _version_for(bsl))

    entries = sorted(entry_by_name.values(), key=lambda e: e.attrib["name"].lower())
    for entry in entries:
        versions.append(entry)

    write_xml(out_dir / "ConfigDumpInfo.xml", ET.ElementTree(root), NS_DUMP)
    report.add_warning("CONFIG_DUMP_INFO_REGENERATED", "ConfigDumpInfo.xml", "Файл версий сформирован заново по дереву результата")


def validate_config_dump_info(out_dir: Path, base_info: Path | None, ext_info: Path | None, ext_registry: ObjectRegistry, report: MergeReport) -> None:
    path = out_dir / "ConfigDumpInfo.xml"
    try:
        parse_xml(path)
    except Exception as exc:
        report.validation["config_dump_info"] = "failed"
        report.add_conflict("CONFIG_DUMP_INFO_XML_INVALID", "ConfigDumpInfo.xml", str(exc))
        return

    result_entries = _read_existing_entries(path)
    base_entries = _read_existing_entries(base_info) if base_info is not None else {}
    report.validation["config_dump_info"] = "passed"

    root_typ, root_name, _ = metadata_full_name(out_dir / "Configuration.xml")
    if root_typ == "Configuration" and root_name:
        expected_root_module = f"Configuration.{root_name}.ManagedApplicationModule"
        for full_name in result_entries:
            if full_name.startswith("Configuration.") and full_name.endswith(".ManagedApplicationModule") and full_name != expected_root_module:
                report.add_conflict(
                    "CONFIG_DUMP_INFO_BAD_ROOT_MODULE_NAME",
                    "ConfigDumpInfo.xml",
                    f"Некорректная запись root module: {full_name}; ожидается {expected_root_module}",
                )

    for full_name, ext_ref in ext_registry.by_full_name.items():
        if ext_ref.is_adopted and full_name in base_entries and full_name in result_entries:
            base_id = base_entries[full_name][0]
            result_id = result_entries[full_name][0]
            if result_id != base_id:
                report.add_conflict(
                    "CONFIG_DUMP_INFO_ADOPTED_ID_OVERRIDES_BASE",
                    "ConfigDumpInfo.xml",
                    f"{full_name}: result id {result_id} != base id {base_id}",
                )
        if not ext_ref.is_adopted and ext_ref.parent_path is None and full_name not in base_entries:
            if full_name not in result_entries:
                report.add_conflict(
                    "CONFIG_DUMP_INFO_NATIVE_OBJECT_MISSING",
                    "ConfigDumpInfo.xml",
                    f"Новый native object из cfu отсутствует в ConfigDumpInfo.xml: {full_name}",
                )
