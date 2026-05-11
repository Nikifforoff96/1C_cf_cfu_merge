from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from cfmerge.metadata_merge import merge_metadata_object
from cfmerge.models import MergeReport
from cfmerge.xml_utils import child, children, local_name, parse_xml


MD = "http://v8.1c.ru/8.3/MDClasses"
XR = "http://v8.1c.ru/8.3/xcf/readable"
XSI = "http://www.w3.org/2001/XMLSchema-instance"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig", newline="")


def _document(name: str, records: list[str], *, adopted: bool = False) -> str:
    adopted_xml = "\t\t\t<ObjectBelonging>Adopted</ObjectBelonging>\n\t\t\t<ExtendedConfigurationObject>base-id</ExtendedConfigurationObject>\n" if adopted else ""
    items = "".join(
        f'\t\t\t\t<Item xsi:type="xr:MDObjectRef">{record}</Item>\n'
        for record in records
    )
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="{MD}" xmlns:xr="{XR}" xmlns:xsi="{XSI}">
\t<Document uuid="doc-id">
\t\t<Properties>
{adopted_xml}\t\t\t<Name>{name}</Name>
\t\t\t<RegisterRecords>
{items}\t\t\t</RegisterRecords>
\t\t</Properties>
\t</Document>
</MetaDataObject>
'''


def _records(path: Path) -> list[str]:
    tree = parse_xml(path)
    obj = next(item for item in list(tree.getroot()) if isinstance(item.tag, str))
    register_records = child(child(obj, "Properties"), "RegisterRecords")
    if register_records is None:
        return []
    return [
        (item.text or "").strip()
        for item in children(register_records, "Item")
    ]


def _merge(tmp_path: Path, base_records: list[str], ext_records: list[str]) -> tuple[Path, MergeReport]:
    base = tmp_path / "base.xml"
    ext = tmp_path / "ext.xml"
    out = tmp_path / "out.xml"
    _write(base, _document("Doc", base_records))
    _write(ext, _document("Doc", ext_records, adopted=True))
    report = MergeReport()

    merge_metadata_object(base, ext, out, "Documents/Doc.xml", report)

    return out, report


def test_register_records_add_new_record(tmp_path: Path) -> None:
    out, report = _merge(
        tmp_path,
        ["AccumulationRegister.Base"],
        ["AccumulationRegister.Base", "AccumulationRegister.Extension"],
    )

    assert _records(out) == ["AccumulationRegister.Base", "AccumulationRegister.Extension"]
    assert any(item["action"] == "register_record_added" for item in report.metadata_merge)
    assert any(item["action"] == "register_records_merged" for item in report.metadata_merge)


def test_empty_extension_register_records_do_not_delete_base(tmp_path: Path) -> None:
    out, report = _merge(tmp_path, ["AccumulationRegister.Base"], [])

    assert _records(out) == ["AccumulationRegister.Base"]
    assert not any(item["property_path"] == "RegisterRecords" and item["action"] == "conflict" for item in report.metadata_merge)


def test_existing_register_record_is_not_duplicated(tmp_path: Path) -> None:
    out, report = _merge(tmp_path, ["AccumulationRegister.Base"], ["AccumulationRegister.Base"])

    assert _records(out) == ["AccumulationRegister.Base"]
    assert not any(item["action"] == "register_record_added" for item in report.metadata_merge)


def test_duplicate_register_record_keys_are_reported(tmp_path: Path) -> None:
    out, report = _merge(
        tmp_path,
        ["AccumulationRegister.Base", "AccumulationRegister.Base"],
        ["AccumulationRegister.Extension", "AccumulationRegister.Extension"],
    )

    assert _records(out) == ["AccumulationRegister.Base", "AccumulationRegister.Base"]
    reasons = {item["reason"] for item in report.metadata_merge if item["action"] == "conflict"}
    assert "duplicate_base_register_record:AccumulationRegister.Base" in reasons
    assert "duplicate_extension_register_record:AccumulationRegister.Extension" in reasons


def test_empty_adopted_register_records_are_not_unsafe_conflicts(tmp_path: Path) -> None:
    out, report = _merge(tmp_path, ["AccumulationRegister.Base"], [])

    assert _records(out) == ["AccumulationRegister.Base"]
    assert not any(item["reason"] == "unsafe_linkage_property_not_merged" for item in report.metadata_merge)
    assert not any(w.code == "METADATA_PROPERTY_REQUIRES_SPECIAL_MERGE" for w in report.warnings)
