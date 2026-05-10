from __future__ import annotations

from pathlib import Path

from cfmerge.models import MergeReport
from cfmerge.validators import validate_xml_tree


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig", newline="")


def test_form_qname_prefix_references_must_be_declared(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _write(out / "Catalogs" / "Synthetic" / "Forms" / "ItemForm" / "Ext" / "Form.xml", """<?xml version="1.0" encoding="UTF-8"?>
<Form xmlns="http://v8.1c.ru/8.3/xcf/logform"
      xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
      xmlns:d4p1="urn:synthetic-flowchart">
  <ChildItems>
    <InputField name="FlowchartField" xsi:type="d4p1:FlowchartContextType"/>
  </ChildItems>
</Form>
""")

    report = MergeReport()
    validate_xml_tree(out, report)

    assert report.validation["xml_prefix_references"] == "passed"
    assert not report.conflicts


def test_form_plain_result_artifacts_are_detected(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _write(out / "Catalogs" / "Synthetic" / "Forms" / "ItemForm" / "Ext" / "Form.xml", """<?xml version="1.0" encoding="UTF-8"?>
<Form xmlns="http://v8.1c.ru/8.3/xcf/logform">
  <BaseForm/>
  <Events>
    <Event name="OnOpen" callType="Before">SyntheticHandler</Event>
  </Events>
</Form>
""")

    report = MergeReport()
    validate_xml_tree(out, report)
    codes = {item.code for item in report.conflicts}

    assert "FORM_CALLTYPE_LEFT" in codes
    assert "FORM_BASEFORM_LEFT" in codes
