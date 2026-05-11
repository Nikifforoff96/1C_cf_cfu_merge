from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from cfmerge.merge_engine import merge
from cfmerge.models import MergeConfig
from cfmerge.scanner import scan_tree
from cfmerge.xml_utils import child, child_text, children, clone_element, local_name, parse_xml
from cfmerge.metadata_merge import metadata_root_object


MD = "http://v8.1c.ru/8.3/MDClasses"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig", newline="")


def _config(name: str, children: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="{MD}">
\t<Configuration uuid="cfg-{name}">
\t\t<Properties>
\t\t\t<Name>{name}</Name>
\t\t</Properties>
\t\t<ChildObjects>
{children}\t\t</ChildObjects>
\t</Configuration>
</MetaDataObject>
'''


def _common_module(name: str, uuid: str, adopted: bool = False) -> str:
    adopted_xml = "\t\t\t<ObjectBelonging>Adopted</ObjectBelonging>\n" if adopted else ""
    extended_xml = "\t\t\t<ExtendedConfigurationObject>base-uuid</ExtendedConfigurationObject>\n" if adopted else ""
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="{MD}">
\t<CommonModule uuid="{uuid}">
\t\t<Properties>
\t\t\t<Name>{name}</Name>
{adopted_xml}{extended_xml}\t\t</Properties>
\t</CommonModule>
</MetaDataObject>
'''


def _defined_type(name: str, uuid: str, type_xml: str, adopted: bool = False) -> str:
    adopted_xml = "\t\t\t<ObjectBelonging>Adopted</ObjectBelonging>\n" if adopted else ""
    extended_xml = "\t\t\t<ExtendedConfigurationObject>base-uuid</ExtendedConfigurationObject>\n" if adopted else ""
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="{MD}" xmlns:v8="http://v8.1c.ru/8.1/data/core">
\t<DefinedType uuid="{uuid}">
\t\t<Properties>
{adopted_xml}\t\t\t<Name>{name}</Name>
{extended_xml}\t\t\t{type_xml}
\t\t</Properties>
\t</DefinedType>
</MetaDataObject>
'''


def _norm(element: ET.Element | None) -> bytes | None:
    if element is None:
        return None
    clone = clone_element(element)

    def rec(node: ET.Element) -> None:
        if node.text is not None and not node.text.strip():
            node.text = None
        elif node.text is not None:
            node.text = node.text.strip()
        node.tail = None
        for item in list(node):
            if isinstance(item.tag, str):
                rec(item)

    rec(clone)
    return ET.tostring(clone, encoding="utf-8")


def _object(path: Path) -> ET.Element:
    return metadata_root_object(parse_xml(path))


def _find_child(element: ET.Element, typ: str, name: str) -> ET.Element | None:
    child_objects = child(element, "ChildObjects")
    if child_objects is None:
        return None
    for item in children(child_objects):
        item_name = child_text(item, ["Properties", "Name"]) or (item.text or "").strip()
        if local_name(item.tag) == typ and item_name == name:
            return item
    return None


def _rights_root(path: Path) -> ET.Element:
    return parse_xml(path).getroot()


def _rights_object(root: ET.Element, name: str) -> ET.Element | None:
    for item in children(root, "object"):
        if child_text(item, ["name"]) == name:
            return item
    return None


def _right_value(root: ET.Element, object_name: str, right_name: str) -> str | None:
    obj = _rights_object(root, object_name)
    if obj is None:
        return None
    for item in children(obj, "right"):
        if child_text(item, ["name"]) == right_name:
            return child_text(item, ["value"])
    return None


def _target_element(root: ET.Element, object_path: str) -> ET.Element | None:
    current = root
    for part in object_path.split("/")[1:]:
        typ, name = part.split(".", 1)
        current = _find_child(current, typ, name)
        if current is None:
            return None
    return current


def _property(root_dir: Path, action: dict[str, str]) -> bytes | None:
    root = _object(root_dir / action["source_path"])
    target = _target_element(root, action["object_path"]) if "/" in action["object_path"] else root
    props = child(target, "Properties") if target is not None else None
    prop = child(props, action["property_path"]) if props is not None else None
    return _norm(prop)


@pytest.fixture()
def small_merge(tmp_path: Path) -> tuple[Path, Path, Path, dict]:
    project = Path(__file__).resolve().parents[1]
    cf = project / "examples" / "small" / "cf"
    cfu = project / "examples" / "small" / "cfu"
    out = tmp_path / "merge_cf"
    report_path = tmp_path / "merge-report.json"
    merge(MergeConfig(cf_dir=cf, cfu_dir=cfu, out_dir=out, report_path=report_path, force=True))
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    return cf, cfu, out, report


def test_empty_extension_register_records_do_not_remove_base(small_merge: tuple[Path, Path, Path, dict]) -> None:
    cf, cfu, out, report = small_merge
    action = {
        "source_path": "Documents/Документ1.xml",
        "object_path": "Document.Документ1",
        "property_path": "RegisterRecords",
    }

    assert _property(out, action) == _property(cf, action)
    assert _property(out, action) != _property(cfu, action)
    assert not any(
        item["action"] == "conflict"
        and item["property_path"] == "RegisterRecords"
        and item["reason"] == "unsafe_linkage_property_not_merged"
        for item in report["metadata_merge"]
    )


def test_existing_child_object_property_is_applied(small_merge: tuple[Path, Path, Path, dict]) -> None:
    cf, _, out, report = small_merge
    action = next(
        item for item in report["metadata_merge"]
        if item["action"] == "property_replaced" and item["property_path"] == "Type" and "/Attribute." in item["object_path"]
    )
    result = _property(out, action)

    assert result != _property(cf, action)
    assert b"ExtendedProperty" not in result
    assert b"xs:string" in result
    assert b"xs:dateTime" in result
    assert b"StringQualifiers" in result
    assert b"DateQualifiers" in result
    assert "readable_extended_property_unwrapped" in action["reason"]


def test_empty_adopted_type_does_not_clear_base_type(tmp_path: Path) -> None:
    cf = tmp_path / "cf"
    cfu = tmp_path / "cfu"
    out = tmp_path / "out"
    report_path = tmp_path / "report.json"
    children_xml = "\t\t\t<DefinedType>УчастникЗадач</DefinedType>\n"
    base_type = "<Type><v8:Type>xs:string</v8:Type></Type>"

    _write(cf / "Configuration.xml", _config("Base", children_xml))
    _write(cfu / "Configuration.xml", _config("Ext", children_xml))
    _write(cf / "DefinedTypes" / "УчастникЗадач.xml", _defined_type("УчастникЗадач", "base-id", base_type))
    _write(cfu / "DefinedTypes" / "УчастникЗадач.xml", _defined_type("УчастникЗадач", "ext-id", "<Type/>", adopted=True))

    merge(MergeConfig(cf_dir=cf, cfu_dir=cfu, out_dir=out, report_path=report_path, force=True))
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    result_text = (out / "DefinedTypes" / "УчастникЗадач.xml").read_text(encoding="utf-8-sig")

    assert "<v8:Type>xs:string</v8:Type>" in result_text
    assert any(
        item["action"] == "property_preserved"
        and item["property_path"] == "Type"
        and item["reason"] == "empty_extension_type_ignored"
        for item in report["metadata_merge"]
    )
    assert not any(
        item["action"] == "property_replaced"
        and item["property_path"] == "Type"
        and item["object_path"] == "DefinedType.УчастникЗадач"
        for item in report["metadata_merge"]
    )


def test_plain_adopted_type_does_not_replace_base_type(tmp_path: Path) -> None:
    cf = tmp_path / "cf"
    cfu = tmp_path / "cfu"
    out = tmp_path / "out"
    report_path = tmp_path / "report.json"
    children_xml = "\t\t\t<DefinedType>УчастникЗадач</DefinedType>\n"
    base_type = "<Type><v8:Type>cfg:CatalogRef.Сотрудники</v8:Type></Type>"
    extension_projection_type = "<Type><v8:TypeSet>cfg:AnyIBRef</v8:TypeSet></Type>"

    _write(cf / "Configuration.xml", _config("Base", children_xml))
    _write(cfu / "Configuration.xml", _config("Ext", children_xml))
    _write(cf / "DefinedTypes" / "УчастникЗадач.xml", _defined_type("УчастникЗадач", "base-id", base_type))
    _write(cfu / "DefinedTypes" / "УчастникЗадач.xml", _defined_type("УчастникЗадач", "ext-id", extension_projection_type, adopted=True))

    merge(MergeConfig(cf_dir=cf, cfu_dir=cfu, out_dir=out, report_path=report_path, force=True))
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    result_text = (out / "DefinedTypes" / "УчастникЗадач.xml").read_text(encoding="utf-8-sig")

    assert "<v8:Type>cfg:CatalogRef.Сотрудники</v8:Type>" in result_text
    assert "cfg:AnyIBRef" not in result_text
    assert any(
        item["action"] == "property_preserved"
        and item["property_path"] == "Type"
        and item["reason"] == "adopted_plain_type_ignored"
        for item in report["metadata_merge"]
    )


def test_native_child_object_is_added_without_duplicates(small_merge: tuple[Path, Path, Path, dict]) -> None:
    _, _, out, report = small_merge
    action = next(
        item for item in report["metadata_merge"]
        if item["action"] == "child_object_added" and item["source_path"] != "Configuration.xml" and "/EnumValue." in item["object_path"]
    )
    root = _object(out / action["source_path"])
    child_type, child_name = action["object_path"].split("/")[-1].split(".", 1)
    matches = [
        item for item in children(child(root, "ChildObjects"))
        if local_name(item.tag) == child_type and child_text(item, ["Properties", "Name"]) == child_name
    ]

    assert len(matches) == 1


def test_existing_child_object_is_not_replaced_by_extension_wrapper(small_merge: tuple[Path, Path, Path, dict]) -> None:
    _, _, out, report = small_merge
    action = next(
        item for item in report["metadata_merge"]
        if item["action"] == "child_object_merged" and "/Attribute." in item["object_path"]
    )
    target = _target_element(_object(out / action["source_path"]), action["object_path"])
    text = ET.tostring(target, encoding="unicode")

    assert "<ObjectBelonging>Adopted</ObjectBelonging>" not in text
    assert "ExtendedConfigurationObject" not in text


def test_extension_technical_properties_do_not_leak(small_merge: tuple[Path, Path, Path, dict]) -> None:
    _, _, out, _ = small_merge
    forbidden = [
        "ObjectBelonging>Adopted",
        "ExtendedConfigurationObject",
        "ConfigurationExtensionPurpose",
        "KeepMappingToExtendedConfigurationObjectsByIDs",
        "ExtendedProperty",
    ]

    for path in out.rglob("*.xml"):
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        assert not any(marker in text for marker in forbidden), path


def test_rights_xml_is_not_classified_as_metadata_object(small_merge: tuple[Path, Path, Path, dict]) -> None:
    project = Path(__file__).resolve().parents[1]
    manifest = scan_tree(project / "examples" / "small" / "cfu")
    rights = [record for rel, record in manifest.items() if rel.endswith("/Ext/Rights.xml")]

    assert rights
    assert {record.kind for record in rights} == {"rights_xml"}


def test_unknown_xml_does_not_overwrite_existing_base_resource(tmp_path: Path) -> None:
    cf = tmp_path / "cf"
    cfu = tmp_path / "cfu"
    out = tmp_path / "out"
    _write(cf / "Configuration.xml", _config("Base", "\t\t\t<CommonModule>BaseModule</CommonModule>\n"))
    _write(cf / "CommonModules" / "BaseModule.xml", _common_module("BaseModule", "base-id"))
    _write(cf / "CommonModules" / "BaseModule" / "Ext" / "Extra.xml", "<Extra><Value>base</Value></Extra>")
    _write(cfu / "Configuration.xml", _config("Ext", "\t\t\t<CommonModule>BaseModule</CommonModule>\n"))
    _write(cfu / "CommonModules" / "BaseModule.xml", _common_module("BaseModule", "ext-id", adopted=True))
    _write(cfu / "CommonModules" / "BaseModule" / "Ext" / "Extra.xml", "<Extra><Value>extension</Value></Extra>")

    report = merge(MergeConfig(cf_dir=cf, cfu_dir=cfu, out_dir=out, force=True))

    assert "<Value>base</Value>" in (out / "CommonModules" / "BaseModule" / "Ext" / "Extra.xml").read_text(encoding="utf-8-sig")
    assert any(item["action"] == "unsupported_resource_xml" for item in report.metadata_merge)


def test_adopted_role_rights_xml_is_merged_semantically(small_merge: tuple[Path, Path, Path, dict]) -> None:
    cf, _, out, report = small_merge
    rel = "Roles/ЧастичныеПрава/Ext/Rights.xml"
    root = _rights_root(out / rel)
    text = (out / rel).read_text(encoding="utf-8-sig")

    assert (out / rel).read_bytes() != (cf / rel).read_bytes()
    assert not any(
        item["action"] == "unsupported_resource_xml" and item["source_path"] == rel
        for item in report["metadata_merge"]
    )
    assert _right_value(root, "InformationRegister.РегистрСведений1", "Read") == "true"
    assert _right_value(root, "Catalog.ТестСправочник", "Delete") == "true"
    assert _right_value(root, "Catalog.омСправочникРасширения", "Read") == "true"
    assert _right_value(root, "Catalog.ВторойСправочник", "Read") == "true"
    assert child_text(root, ["setForAttributesByDefault"]) == "false"
    assert "Configuration.РасширениеТекст" not in text
    assert any(item["action"] == "rights_object_added" for item in report["metadata_merge"])
    assert any(item["action"] == "role_rights_flag_replaced" for item in report["metadata_merge"])


def test_report_contains_metadata_property_changes(small_merge: tuple[Path, Path, Path, dict]) -> None:
    _, _, _, report = small_merge

    assert any(item["action"] == "property_replaced" for item in report["metadata_merge"])
    assert any(item["action"] == "child_object_merged" for item in report["metadata_merge"])


def test_config_dump_info_does_not_include_extension_root_configuration(small_merge: tuple[Path, Path, Path, dict]) -> None:
    _, _, out, _ = small_merge
    text = (out / "ConfigDumpInfo.xml").read_text(encoding="utf-8-sig")

    assert "Configuration.ОсновнаяКонфигурацияТест" in text
    assert "Configuration.РасширениеТекст" not in text


def test_native_role_rights_are_rebased_to_base_configuration(small_merge: tuple[Path, Path, Path, dict]) -> None:
    _, _, out, report = small_merge
    text = (out / "Roles" / "омРольРасширения" / "Ext" / "Rights.xml").read_text(encoding="utf-8-sig")

    assert "Configuration.ОсновнаяКонфигурацияТест" in text
    assert "Configuration.РасширениеТекст" not in text
    assert any(
        item["action"] == "rights_xml_rebased" and item["reason"] == "rights_xml_configuration_reference_rebased"
        for item in report["metadata_merge"]
    )


def test_second_merge_does_not_duplicate_child_objects(tmp_path: Path, small_merge: tuple[Path, Path, Path, dict]) -> None:
    _, cfu, first_out, report = small_merge
    second_out = tmp_path / "second"
    merge(MergeConfig(cf_dir=first_out, cfu_dir=cfu, out_dir=second_out, force=True))
    action = next(
        item for item in report["metadata_merge"]
        if item["action"] == "child_object_added" and item["source_path"] != "Configuration.xml" and "/EnumValue." in item["object_path"]
    )
    root = _object(second_out / action["source_path"])
    child_type, child_name = action["object_path"].split("/")[-1].split(".", 1)
    matches = [
        item for item in children(child(root, "ChildObjects"))
        if local_name(item.tag) == child_type and child_text(item, ["Properties", "Name"]) == child_name
    ]

    assert len(matches) == 1

    rights_root = _rights_root(second_out / "Roles" / "ЧастичныеПрава" / "Ext" / "Rights.xml")
    object_names = [child_text(item, ["name"]) for item in children(rights_root, "object")]
    assert len(object_names) == len(set(object_names))
    for rights_object in children(rights_root, "object"):
        right_names = [child_text(item, ["name"]) for item in children(rights_object, "right")]
        assert len(right_names) == len(set(right_names))


def test_rights_changes_are_written_to_json_and_human_report(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[1]
    out = tmp_path / "merge_cf"
    report_path = tmp_path / "merge-report.json"
    human_path = tmp_path / "merge-report.txt"

    merge(MergeConfig(
        cf_dir=project / "examples" / "small" / "cf",
        cfu_dir=project / "examples" / "small" / "cfu",
        out_dir=out,
        report_path=report_path,
        human_report_path=human_path,
        force=True,
    ))
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    actions = {item["action"] for item in report["metadata_merge"]}
    human_text = human_path.read_text(encoding="utf-8-sig")

    assert "rights_object_added" in actions
    assert "role_rights_flag_replaced" in actions
    assert "rights_object_added" in human_text
    assert "role_rights_flag_replaced" in human_text
