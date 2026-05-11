from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from cfmerge.models import MergeReport
from cfmerge.role_rights_merge import copy_role_rights, merge_role_rights
from cfmerge.xml_utils import child, child_text, children, local_name, parse_xml


ROLES = "http://v8.1c.ru/8.2/roles"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig", newline="")


def _rights(body: str, *, set_for_attributes: str = "true") -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<Rights xmlns="{ROLES}" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="Rights" version="2.20">
\t<setForNewObjects>false</setForNewObjects>
\t<setForAttributesByDefault>{set_for_attributes}</setForAttributesByDefault>
\t<independentRightsOfChildObjects>false</independentRightsOfChildObjects>
{body}</Rights>
'''


def _object(name: str, body: str) -> str:
    return f'''\t<object>
\t\t<name>{name}</name>
{body}\t</object>
'''


def _right(name: str, value: str = "true", extra: str = "") -> str:
    return f'''\t\t<right>
\t\t\t<name>{name}</name>
\t\t\t<value>{value}</value>
{extra}\t\t</right>
'''


def _root(path: Path) -> ET.Element:
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


def _merge(
    tmp_path: Path,
    base_body: str,
    ext_body: str,
    *,
    ext_set_for_attributes: str = "true",
) -> tuple[ET.Element, MergeReport]:
    base = tmp_path / "base.xml"
    ext = tmp_path / "ext.xml"
    out = tmp_path / "out.xml"
    _write(base, _rights(base_body))
    _write(ext, _rights(ext_body, set_for_attributes=ext_set_for_attributes))
    report = MergeReport()

    merge_role_rights(base, ext, out, "Roles/Partial/Ext/Rights.xml", report, "Base", "Ext")

    return _root(out), report


def test_adds_new_object(tmp_path: Path) -> None:
    root, report = _merge(
        tmp_path,
        _object("Catalog.Base", _right("Read")),
        _object("Catalog.Extension", _right("Read")),
    )

    assert _rights_object(root, "Catalog.Base") is not None
    assert _right_value(root, "Catalog.Extension", "Read") == "true"
    assert any(item["action"] == "rights_object_added" for item in report.metadata_merge)


def test_adds_new_right_to_existing_object(tmp_path: Path) -> None:
    root, report = _merge(
        tmp_path,
        _object("Catalog.Item", _right("Read")),
        _object("Catalog.Item", _right("Insert")),
    )

    assert _right_value(root, "Catalog.Item", "Read") == "true"
    assert _right_value(root, "Catalog.Item", "Insert") == "true"
    assert any(item["action"] == "role_right_added" and item["property_path"] == "Insert" for item in report.metadata_merge)


def test_replaces_existing_right(tmp_path: Path) -> None:
    root, report = _merge(
        tmp_path,
        _object("Catalog.Item", _right("Read", "false")),
        _object("Catalog.Item", _right("Read", "true")),
    )

    assert _right_value(root, "Catalog.Item", "Read") == "true"
    assert any(item["action"] == "role_right_replaced" and item["property_path"] == "Read" for item in report.metadata_merge)


def test_replaces_right_with_restriction_by_condition(tmp_path: Path) -> None:
    restriction = '''\t\t\t<restrictionByCondition>
\t\t\t\t<condition>Allowed</condition>
\t\t\t</restrictionByCondition>
'''
    root, report = _merge(
        tmp_path,
        _object("Catalog.Item", _right("Read", "true")),
        _object("Catalog.Item", _right("Read", "false", restriction)),
    )
    read = next(item for item in children(_rights_object(root, "Catalog.Item"), "right") if child_text(item, ["name"]) == "Read")

    assert child_text(read, ["value"]) == "false"
    assert child(read, "restrictionByCondition") is not None
    assert any(item["action"] == "role_right_replaced" and item["property_path"] == "Read" for item in report.metadata_merge)


def test_replaces_top_level_flags(tmp_path: Path) -> None:
    root, report = _merge(tmp_path, "", "", ext_set_for_attributes="false")

    assert child_text(root, ["setForAttributesByDefault"]) == "false"
    assert any(
        item["action"] == "role_rights_flag_replaced"
        and item["property_path"] == "setForAttributesByDefault"
        for item in report.metadata_merge
    )


def test_rebases_extension_root_configuration_on_copy(tmp_path: Path) -> None:
    src = tmp_path / "src.xml"
    out = tmp_path / "out.xml"
    _write(src, _rights(_object("Configuration.Ext", _right("MainWindowModeNormal"))))
    report = MergeReport()

    copy_role_rights(src, out, "Roles/OwnRole/Ext/Rights.xml", report, "Base", "Ext")
    text = out.read_text(encoding="utf-8-sig")

    assert "Configuration.Base" in text
    assert "Configuration.Ext" not in text
    assert any(item["action"] == "rights_xml_rebased" for item in report.metadata_merge)


def test_conflict_on_duplicate_object_or_right_key(tmp_path: Path) -> None:
    root, report = _merge(
        tmp_path,
        _object("Catalog.Item", _right("Read", "true") + _right("Read", "false")),
        _object("Catalog.Duplicate", _right("Read"))
        + _object("Catalog.Duplicate", _right("Insert"))
        + _object("Catalog.Item", _right("Read", "false") + _right("Insert")),
    )

    assert _rights_object(root, "Catalog.Duplicate") is None
    assert _right_value(root, "Catalog.Item", "Read") == "true"
    assert _right_value(root, "Catalog.Item", "Insert") == "true"
    reasons = {item["reason"] for item in report.metadata_merge if item["action"] == "conflict"}
    assert "duplicate_extension_object_key" in reasons
    assert "duplicate_base_right_key" in reasons


def test_unsupported_unknown_top_level_xml_is_reported_and_not_copied(tmp_path: Path) -> None:
    root, report = _merge(tmp_path, "", "\t<unknown><value>extension</value></unknown>\n")

    assert all(local_name(item.tag) != "unknown" for item in children(root))
    assert any(item["action"] == "unsupported_rights_xml_element" for item in report.metadata_merge)
