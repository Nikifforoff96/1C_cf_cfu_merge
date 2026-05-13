from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from cfmerge.bsl_merge import merge_bsl
from cfmerge.form_merge import merge_form_visual
from cfmerge.form_validator import validate_form_result
from cfmerge.models import MergeReport


LF = "http://v8.1c.ru/8.3/xcf/logform"
V8 = "http://v8.1c.ru/8.1/data/core"
XR = "http://v8.1c.ru/8.3/xcf/readable"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
DCSSET = "http://v8.1c.ru/8.1/data-composition-system/settings"
DCSCOR = "http://v8.1c.ru/8.1/data-composition-system/core"
V8UI = "http://v8.1c.ru/8.1/data/ui"
ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig", newline="")


def _form(
    *,
    body: str,
    with_attrs: bool = False,
    with_commands: bool = False,
    with_parameters: bool = False,
) -> str:
    attrs = "<Attributes />" if with_attrs else ""
    commands = "<Commands />" if with_commands else ""
    params = "<Parameters />" if with_parameters else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Form xmlns="{LF}"
      xmlns:v8="{V8}"
      xmlns:xr="{XR}"
      xmlns:xsi="{XSI}"
      xmlns:dcsset="{DCSSET}"
      xmlns:dcscor="{DCSCOR}"
      xmlns:v8ui="{V8UI}">
{body}
{attrs}
{commands}
{params}
</Form>
"""


def _merge(
    tmp_path: Path,
    *,
    base_form: str,
    ext_form: str,
    ext_module: str | None = None,
    rel_path: str = "Form.xml",
) -> tuple[ET.Element, MergeReport, list]:
    base = tmp_path / "base.xml"
    ext = tmp_path / "ext.xml"
    out = tmp_path / "out.xml"
    _write(base, base_form)
    _write(ext, ext_form)
    report = MergeReport()
    hooks = merge_form_visual(base, ext, out, rel_path, report, module_text=ext_module)
    return ET.parse(out).getroot(), report, hooks


def _find_named(root: ET.Element, name: str) -> list[ET.Element]:
    return [item for item in root.iter() if isinstance(item.tag, str) and item.attrib.get("name") == name]


def _find_first(root: ET.Element, name: str) -> ET.Element:
    found = _find_named(root, name)
    assert found, name
    return found[0]


def _child_items(owner: ET.Element) -> list[ET.Element]:
    for item in list(owner):
        if isinstance(item.tag, str) and item.tag.endswith("ChildItems"):
            return [child for child in list(item) if isinstance(child.tag, str)]
    return []


def _attribute(root: ET.Element, name: str) -> ET.Element:
    attrs = next((item for item in list(root) if isinstance(item.tag, str) and item.tag.endswith("Attributes")), None)
    assert attrs is not None
    found = next((item for item in list(attrs) if isinstance(item.tag, str) and item.tag.endswith("Attribute") and item.attrib.get("name") == name), None)
    assert found is not None, name
    return found


def _attribute_columns(attribute: ET.Element, table: str) -> list[str]:
    columns = next((item for item in list(attribute) if isinstance(item.tag, str) and item.tag.endswith("Columns")), None)
    assert columns is not None
    group = next((item for item in list(columns) if isinstance(item.tag, str) and item.tag.endswith("AdditionalColumns") and item.attrib.get("table") == table), None)
    assert group is not None, table
    return [item.attrib["name"] for item in list(group) if isinstance(item.tag, str) and item.tag.endswith("Column")]


def _direct_rule_count(root: ET.Element) -> int:
    attrs = next((item for item in list(root) if isinstance(item.tag, str) and item.tag.endswith("Attributes")), None)
    assert attrs is not None
    ca = next((item for item in list(attrs) if isinstance(item.tag, str) and item.tag.endswith("ConditionalAppearance")), None)
    assert ca is not None
    return len([item for item in list(ca) if isinstance(item.tag, str)])


def _ca_rule(field: str, left: str, color: str) -> str:
    return f"""
      <dcsset:item>
        <dcsset:selection>
          <dcsset:item>
            <dcsset:field>{field}</dcsset:field>
          </dcsset:item>
        </dcsset:selection>
        <dcsset:filter>
          <dcsset:item xsi:type="dcsset:FilterItemComparison">
            <dcsset:left>{left}</dcsset:left>
            <dcsset:comparisonType>Equal</dcsset:comparisonType>
          </dcsset:item>
        </dcsset:filter>
        <dcsset:appearance>
          <dcscor:item xsi:type="dcsset:SettingsParameterValue">
            <dcscor:parameter>ЦветТекста</dcscor:parameter>
            <dcscor:value xsi:type="v8ui:Color">{color}</dcscor:value>
          </dcscor:item>
        </dcsset:appearance>
      </dcsset:item>
"""


def _handlers_from_xml(root: ET.Element) -> set[str]:
    handlers: set[str] = set()
    for item in root.iter():
        if not isinstance(item.tag, str):
            continue
        if item.tag.endswith("Event") or item.tag.endswith("Action"):
            value = (item.text or "").strip()
            if value:
                handlers.add(value)
    return handlers


def _methods_from_module(text: str) -> set[str]:
    return {
        line.split("(", 1)[0].split()[-1]
        for line in text.splitlines()
        if line.strip().startswith(("Процедура ", "Функция ", "Асинх Процедура ", "Асинх Функция "))
    }


def test_form_move_existing_child_item(tmp_path: Path) -> None:
    base_form = _form(body="""
  <ChildItems>
    <UsualGroup name="P1" id="1"><ChildItems><InputField name="A" id="3" /></ChildItems></UsualGroup>
    <UsualGroup name="P2" id="2"><ChildItems /></UsualGroup>
  </ChildItems>
""")
    ext_form = _form(body="""
  <ChildItems>
    <UsualGroup name="P1" id="1"><ChildItems /></UsualGroup>
    <UsualGroup name="P2" id="2"><ChildItems><InputField name="A" id="3" /></ChildItems></UsualGroup>
  </ChildItems>
  <BaseForm>
    <ChildItems>
      <UsualGroup name="P1" id="1"><ChildItems><InputField name="A" id="3" /></ChildItems></UsualGroup>
      <UsualGroup name="P2" id="2"><ChildItems /></UsualGroup>
    </ChildItems>
  </BaseForm>
""")

    root, report, _ = _merge(tmp_path, base_form=base_form, ext_form=ext_form)

    assert not report.conflicts
    assert len(_find_named(root, "A")) == 1
    assert _child_items(_find_first(root, "P1")) == []
    assert [item.attrib["name"] for item in _child_items(_find_first(root, "P2"))] == ["A"]


def test_form_move_to_extension_added_parent(tmp_path: Path) -> None:
    base_form = _form(body="""
  <ChildItems>
    <UsualGroup name="P1" id="1"><ChildItems><InputField name="A" id="3" /></ChildItems></UsualGroup>
  </ChildItems>
""")
    ext_form = _form(body="""
  <ChildItems>
    <UsualGroup name="P1" id="1"><ChildItems /></UsualGroup>
    <UsualGroup name="P2" id="2"><ChildItems><InputField name="A" id="3" /></ChildItems></UsualGroup>
  </ChildItems>
  <BaseForm>
    <ChildItems>
      <UsualGroup name="P1" id="1"><ChildItems><InputField name="A" id="3" /></ChildItems></UsualGroup>
    </ChildItems>
  </BaseForm>
""")

    root, report, _ = _merge(tmp_path, base_form=base_form, ext_form=ext_form)

    assert not report.conflicts
    assert len(_find_named(root, "A")) == 1
    assert [item.attrib["name"] for item in _child_items(_find_first(root, "P2"))] == ["A"]


def test_form_property_apply_when_current_equals_ancestor(tmp_path: Path) -> None:
    base_form = _form(body="""
  <ChildItems>
    <InputField name="Field" id="1"><Title>Base</Title></InputField>
  </ChildItems>
""")
    ext_form = _form(body="""
  <ChildItems>
    <InputField name="Field" id="1"><Title>Ext</Title></InputField>
  </ChildItems>
  <BaseForm>
    <ChildItems>
      <InputField name="Field" id="1"><Title>Base</Title></InputField>
    </ChildItems>
  </BaseForm>
""")

    root, report, _ = _merge(tmp_path, base_form=base_form, ext_form=ext_form)

    assert not report.conflicts
    assert _find_first(root, "Field").find(f"{{{LF}}}Title").text == "Ext"


def test_form_property_keep_current_when_extension_unchanged(tmp_path: Path) -> None:
    base_form = _form(body="""
  <ChildItems>
    <InputField name="Field" id="1"><Title>Current</Title></InputField>
  </ChildItems>
""")
    ext_form = _form(body="""
  <ChildItems>
    <InputField name="Field" id="1"><Title>Base</Title></InputField>
  </ChildItems>
  <BaseForm>
    <ChildItems>
      <InputField name="Field" id="1"><Title>Base</Title></InputField>
    </ChildItems>
  </BaseForm>
""")

    root, report, _ = _merge(tmp_path, base_form=base_form, ext_form=ext_form)

    assert not report.conflicts
    assert _find_first(root, "Field").find(f"{{{LF}}}Title").text == "Current"


def test_form_property_conflict_when_both_changed(tmp_path: Path) -> None:
    base_form = _form(body="""
  <ChildItems>
    <InputField name="Field" id="1"><Title>Current</Title></InputField>
  </ChildItems>
""")
    ext_form = _form(body="""
  <ChildItems>
    <InputField name="Field" id="1"><Title>Ext</Title></InputField>
  </ChildItems>
  <BaseForm>
    <ChildItems>
      <InputField name="Field" id="1"><Title>Base</Title></InputField>
    </ChildItems>
  </BaseForm>
""")

    root, report, _ = _merge(tmp_path, base_form=base_form, ext_form=ext_form)

    assert any(item.code == "FORM_PROPERTY_CONFLICT" for item in report.conflicts)
    assert _find_first(root, "Field").find(f"{{{LF}}}Title").text == "Current"


def test_form_added_direct_property(tmp_path: Path) -> None:
    base_form = _form(body="""
  <ChildItems>
    <InputField name="Field" id="1" />
  </ChildItems>
""")
    ext_form = _form(body="""
  <ChildItems>
    <InputField name="Field" id="1"><Visible>false</Visible></InputField>
  </ChildItems>
  <BaseForm>
    <ChildItems>
      <InputField name="Field" id="1" />
    </ChildItems>
  </BaseForm>
""")

    root, report, _ = _merge(tmp_path, base_form=base_form, ext_form=ext_form)

    assert not report.conflicts
    assert _find_first(root, "Field").find(f"{{{LF}}}Visible").text == "false"


def test_form_removed_direct_property(tmp_path: Path) -> None:
    base_form = _form(body="""
  <ChildItems>
    <InputField name="Field" id="1"><Title>Base</Title></InputField>
  </ChildItems>
""")
    ext_form = _form(body="""
  <ChildItems>
    <InputField name="Field" id="1"><Title xsi:nil="true" /></InputField>
  </ChildItems>
  <BaseForm>
    <ChildItems>
      <InputField name="Field" id="1"><Title>Base</Title></InputField>
    </ChildItems>
  </BaseForm>
""")

    root, report, _ = _merge(tmp_path, base_form=base_form, ext_form=ext_form)

    assert not report.conflicts
    assert _find_first(root, "Field").find(f"{{{LF}}}Title") is None


def test_form_removed_direct_property_by_absence(tmp_path: Path) -> None:
    base_form = _form(body="""
  <ChildItems>
    <UsualGroup name="Group" id="1"><Width>40</Width><Group>Vertical</Group></UsualGroup>
  </ChildItems>
""")
    ext_form = _form(body="""
  <ChildItems>
    <UsualGroup name="Group" id="1"><Group>Vertical</Group></UsualGroup>
  </ChildItems>
  <BaseForm>
    <ChildItems>
      <UsualGroup name="Group" id="1"><Width>40</Width><Group>Vertical</Group></UsualGroup>
    </ChildItems>
  </BaseForm>
""")

    root, report, _ = _merge(tmp_path, base_form=base_form, ext_form=ext_form)

    assert not report.conflicts
    group = _find_first(root, "Group")
    assert group.find(f"{{{LF}}}Width") is None
    assert group.find(f"{{{LF}}}Group").text == "Vertical"


def test_form_removed_direct_property_conflict_when_current_changed(tmp_path: Path) -> None:
    base_form = _form(body="""
  <ChildItems>
    <UsualGroup name="Group" id="1"><Width>50</Width></UsualGroup>
  </ChildItems>
""")
    ext_form = _form(body="""
  <ChildItems>
    <UsualGroup name="Group" id="1" />
  </ChildItems>
  <BaseForm>
    <ChildItems>
      <UsualGroup name="Group" id="1"><Width>40</Width></UsualGroup>
    </ChildItems>
  </BaseForm>
""")

    root, report, _ = _merge(tmp_path, base_form=base_form, ext_form=ext_form)

    assert any(item.code == "FORM_PROPERTY_CONFLICT" for item in report.conflicts)
    assert _find_first(root, "Group").find(f"{{{LF}}}Width").text == "50"


def test_form_nested_context_menu_addition(tmp_path: Path) -> None:
    base_form = _form(body="""
  <ChildItems>
    <Table name="Files" id="1">
      <ContextMenu name="FilesMenu" id="10">
        <ChildItems>
          <Button name="Open" id="11"><CommandName>Form.Command.OpenCurrent</CommandName></Button>
        </ChildItems>
      </ContextMenu>
    </Table>
  </ChildItems>
""")
    ext_form = _form(body="""
  <ChildItems>
    <Table name="Files" id="1">
      <ContextMenu name="FilesMenu" id="10">
        <ChildItems>
          <Button name="Open" id="11"><CommandName>0</CommandName></Button>
          <Button name="FilesContextVersions" id="12"><CommandName>Form.Command.Versions</CommandName></Button>
        </ChildItems>
      </ContextMenu>
    </Table>
  </ChildItems>
  <BaseForm>
    <ChildItems>
      <Table name="Files" id="1">
        <ContextMenu name="FilesMenu" id="10">
          <ChildItems>
            <Button name="Open" id="11"><CommandName>0</CommandName></Button>
          </ChildItems>
        </ContextMenu>
      </Table>
    </ChildItems>
  </BaseForm>
""")

    root, report, _ = _merge(tmp_path, base_form=base_form, ext_form=ext_form)

    assert not report.conflicts
    menu = _find_first(root, "FilesMenu")
    items = [item.attrib["name"] for item in _child_items(menu)]
    assert items[:2] == ["Open", "FilesContextVersions"]
    assert _find_first(root, "Open").find(f"{{{LF}}}CommandName").text == "Form.Command.OpenCurrent"


def test_form_additional_columns_merge_preserves_base_and_adds_extension(tmp_path: Path) -> None:
    base_form = _form(
        body="""
  <Attributes>
    <Attribute name="Объект" id="1">
      <Columns>
        <AdditionalColumns table="Объект.Стороны">
          <Column name="СводныйИндикатор" id="1"><Type><v8:Type>xs:string</v8:Type></Type></Column>
          <Column name="ИндексКартинкиСтороны" id="2"><Type><v8:Type>xs:string</v8:Type></Type></Column>
        </AdditionalColumns>
        <AdditionalColumns table="Объект.Контрагенты">
          <Column name="Состояние" id="3"><Type><v8:Type>xs:string</v8:Type></Type></Column>
        </AdditionalColumns>
      </Columns>
    </Attribute>
  </Attributes>
""",
    )
    ext_form = _form(
        body="""
  <Attributes>
    <Attribute name="Объект" id="1">
      <Columns>
        <AdditionalColumns table="Объект.НСК_БюджетныеСроки">
          <Column name="СметаФНР" id="100"><Type><v8:Type>xs:string</v8:Type></Type></Column>
        </AdditionalColumns>
        <AdditionalColumns table="Объект.Контрагенты" />
        <AdditionalColumns table="Объект.Стороны" />
      </Columns>
    </Attribute>
  </Attributes>
  <BaseForm>
    <Attributes>
      <Attribute name="Объект" id="1">
        <Columns>
          <AdditionalColumns table="Объект.Стороны">
            <Column name="СводныйИндикатор" id="1"><Type><v8:Type>xs:string</v8:Type></Type></Column>
            <Column name="ИндексКартинкиСтороны" id="2"><Type><v8:Type>xs:string</v8:Type></Type></Column>
          </AdditionalColumns>
          <AdditionalColumns table="Объект.Контрагенты">
            <Column name="Состояние" id="3"><Type><v8:Type>xs:string</v8:Type></Type></Column>
          </AdditionalColumns>
        </Columns>
      </Attribute>
    </Attributes>
  </BaseForm>
""",
    )

    root, report, _ = _merge(tmp_path, base_form=base_form, ext_form=ext_form)

    assert not report.conflicts
    attribute = _attribute(root, "Объект")
    assert _attribute_columns(attribute, "Объект.Стороны") == ["СводныйИндикатор", "ИндексКартинкиСтороны"]
    assert _attribute_columns(attribute, "Объект.Контрагенты") == ["Состояние"]
    assert _attribute_columns(attribute, "Объект.НСК_БюджетныеСроки") == ["СметаФНР"]


def test_form_conditional_appearance_add(tmp_path: Path) -> None:
    base_form = _form(
        body=f"""
  <Attributes>
    <ConditionalAppearance>
      {_ca_rule("FieldA", "Object.FieldA", "Red")}
    </ConditionalAppearance>
  </Attributes>
"""
    )
    ext_form = _form(
        body=f"""
  <Attributes>
    <ConditionalAppearance>
      {_ca_rule("FieldA", "Object.FieldA", "Red")}
      {_ca_rule("FieldB", "Object.FieldB", "Green")}
    </ConditionalAppearance>
  </Attributes>
  <BaseForm>
    <Attributes>
      <ConditionalAppearance>
        {_ca_rule("FieldA", "Object.FieldA", "Red")}
      </ConditionalAppearance>
    </Attributes>
  </BaseForm>
"""
    )

    root, report, _ = _merge(tmp_path, base_form=base_form, ext_form=ext_form)

    assert not report.conflicts
    assert _direct_rule_count(root) == 2


def test_form_conditional_appearance_change(tmp_path: Path) -> None:
    base_form = _form(
        body=f"""
  <Attributes>
    <ConditionalAppearance>
      {_ca_rule("FieldA", "Object.FieldA", "Red")}
    </ConditionalAppearance>
  </Attributes>
"""
    )
    ext_form = _form(
        body=f"""
  <Attributes>
    <ConditionalAppearance>
      {_ca_rule("FieldA", "Object.FieldA", "Green")}
    </ConditionalAppearance>
  </Attributes>
  <BaseForm>
    <Attributes>
      <ConditionalAppearance>
        {_ca_rule("FieldA", "Object.FieldA", "Red")}
      </ConditionalAppearance>
    </Attributes>
  </BaseForm>
"""
    )

    root, report, _ = _merge(tmp_path, base_form=base_form, ext_form=ext_form)

    assert not report.conflicts
    assert "Green" in ET.tostring(root, encoding="unicode")


def test_form_command_interface_add_item(tmp_path: Path) -> None:
    base_form = _form(body="<CommandInterface><NavigationPanel /></CommandInterface>")
    ext_form = _form(body="""
  <CommandInterface>
    <NavigationPanel>
      <Item>
        <Command>CommonCommand.Processes</Command>
        <Type>Added</Type>
        <CommandGroup>FormNavigationPanelGoTo</CommandGroup>
        <DefaultVisible>false</DefaultVisible>
      </Item>
    </NavigationPanel>
  </CommandInterface>
  <BaseForm>
    <CommandInterface><NavigationPanel /></CommandInterface>
  </BaseForm>
""")

    root, report, _ = _merge(tmp_path, base_form=base_form, ext_form=ext_form)

    assert not report.conflicts
    assert "CommonCommand.Processes" in ET.tostring(root, encoding="unicode")


def test_form_command_interface_merges_independent_property_changes(tmp_path: Path) -> None:
    base_form = _form(body="""
  <CommandInterface>
    <NavigationPanel>
      <Item>
        <Command>CommonCommand.Processes</Command>
        <Type>Auto</Type>
        <CommandGroup>FormNavigationPanelGoTo</CommandGroup>
        <Index>2</Index>
        <DefaultVisible>true</DefaultVisible>
      </Item>
    </NavigationPanel>
  </CommandInterface>
""")
    ext_form = _form(body="""
  <CommandInterface>
    <NavigationPanel>
      <Item>
        <Command>CommonCommand.Processes</Command>
        <Type>Auto</Type>
        <CommandGroup>FormNavigationPanelGoTo</CommandGroup>
        <DefaultVisible>false</DefaultVisible>
      </Item>
    </NavigationPanel>
  </CommandInterface>
  <BaseForm>
    <CommandInterface>
      <NavigationPanel>
        <Item>
          <Command>CommonCommand.Processes</Command>
          <Type>Auto</Type>
          <CommandGroup>FormNavigationPanelGoTo</CommandGroup>
          <DefaultVisible>true</DefaultVisible>
        </Item>
      </NavigationPanel>
    </CommandInterface>
  </BaseForm>
""")

    root, report, _ = _merge(tmp_path, base_form=base_form, ext_form=ext_form)

    assert not report.conflicts
    text = ET.tostring(root, encoding="unicode")
    assert "<Index>2</Index>" in text
    assert "<DefaultVisible>false</DefaultVisible>" in text


def test_form_command_interface_existing_current_collision_is_diagnostic(tmp_path: Path) -> None:
    base_form = _form(body="""
  <CommandInterface>
    <NavigationPanel>
      <Item>
        <Command>CommonCommand.Processes</Command>
        <Type>Auto</Type>
        <CommandGroup>FormNavigationPanelGoTo</CommandGroup>
        <Visible><Common>false</Common></Visible>
      </Item>
    </NavigationPanel>
  </CommandInterface>
""")
    ext_form = _form(body="""
  <CommandInterface>
    <NavigationPanel>
      <Item>
        <Command>CommonCommand.Processes</Command>
        <Type>Added</Type>
        <CommandGroup>FormNavigationPanelGoTo</CommandGroup>
        <Visible>
          <Common>true</Common>
          <Value name="FunctionalOption.UseTasks">true</Value>
        </Visible>
      </Item>
    </NavigationPanel>
  </CommandInterface>
  <BaseForm>
    <CommandInterface><NavigationPanel /></CommandInterface>
  </BaseForm>
""")

    root, report, _ = _merge(tmp_path, base_form=base_form, ext_form=ext_form)

    assert not any(item.code == "FORM_COMMAND_INTERFACE_CONFLICT" for item in report.conflicts)
    text = ET.tostring(root, encoding="unicode")
    assert 'Value name="FunctionalOption.UseTasks">true</' in text
    assert "<Type>Added</Type>" in text
    assert "<Common>true</Common>" in text
    assert any(
        item["action"] == "form_command_interface_property_replaced"
        for item in report.metadata_merge
    )


def test_form_event_after_with_base_handler(tmp_path: Path) -> None:
    base_form = _form(body="""
  <Events><Event name="OnOpen">BaseHandler</Event></Events>
""")
    ext_form = _form(body="""
  <Events><Event name="OnOpen" callType="After">ExtHandler</Event></Events>
  <BaseForm><Events><Event name="OnOpen">BaseHandler</Event></Events></BaseForm>
""")
    ext_module = """
Процедура ExtHandler()
КонецПроцедуры
"""

    root, report, hooks = _merge(tmp_path, base_form=base_form, ext_form=ext_form, ext_module=ext_module)

    assert not report.conflicts
    assert len(hooks) == 1
    assert hooks[0].target_handler == "BaseHandler"
    assert hooks[0].extension_handler == "ExtHandler"
    assert hooks[0].mode == "after"
    event = next(item for item in root.iter() if isinstance(item.tag, str) and item.tag.endswith("Event"))
    assert event.text == "BaseHandler"


def test_form_event_before_and_after_same_event_are_both_inlined(tmp_path: Path) -> None:
    base_form = _form(body="""
  <Events><Event name="OnOpen">BaseHandler</Event></Events>
""")
    ext_form = _form(body="""
  <Events>
    <Event name="OnOpen" callType="Before">BeforeHandler</Event>
    <Event name="OnOpen" callType="After">AfterHandler</Event>
  </Events>
  <BaseForm><Events><Event name="OnOpen">BaseHandler</Event></Events></BaseForm>
""")
    ext_module = """
Процедура BeforeHandler(Отказ)
	Сообщить("before");
КонецПроцедуры

Процедура AfterHandler(Отказ)
	Сообщить("after");
КонецПроцедуры
"""
    base_module = """
Процедура BaseHandler(Отказ)
	Сообщить("base");
КонецПроцедуры
"""

    _, report, hooks = _merge(tmp_path, base_form=base_form, ext_form=ext_form, ext_module=ext_module)
    result = merge_bsl(base_module, ext_module, "Form/Module.bsl", hooks)

    assert not report.conflicts
    assert [(hook.extension_handler, hook.mode) for hook in hooks] == [("BeforeHandler", "before"), ("AfterHandler", "after")]
    assert result.text.index("\tBeforeHandler(Отказ);") < result.text.index('\tСообщить("base");')
    assert result.text.index('\tСообщить("base");') < result.text.index("\tAfterHandler(Отказ);")


def test_form_event_override_missing_handler(tmp_path: Path) -> None:
    base_form = _form(body="""
  <Events><Event name="OnOpen">BaseHandler</Event></Events>
""")
    ext_form = _form(body="""
  <Events><Event name="OnOpen" callType="Override">MissingHandler</Event></Events>
  <BaseForm><Events><Event name="OnOpen">BaseHandler</Event></Events></BaseForm>
""")

    root, report, _ = _merge(tmp_path, base_form=base_form, ext_form=ext_form, ext_module="")

    event = next(item for item in root.iter() if isinstance(item.tag, str) and item.tag.endswith("Event"))
    assert event.text == "BaseHandler"
    assert not report.conflicts


def test_form_validator_ignores_missing_handlers(tmp_path: Path) -> None:
    form_path = tmp_path / "Form.xml"
    _write(
        form_path,
        _form(body="""
  <Events><Event name="OnOpen">MissingHandler</Event></Events>
"""),
    )
    report = MergeReport()

    validate_form_result(form_path, report)

    assert not any(item.code == "FORM_HANDLER_MISSING" for item in report.conflicts)


def test_form_validator_ignores_preexisting_duplicate_child_item_names(tmp_path: Path) -> None:
    base = tmp_path / "base.xml"
    result = tmp_path / "result.xml"
    form_text = _form(body="""
  <ChildItems>
    <Group name="Parent1" id="1"><ChildItems><ContextMenu name="SameMenu" id="2" /></ChildItems></Group>
    <Group name="Parent2" id="3"><ChildItems><ContextMenu name="SameMenu" id="4" /></ChildItems></Group>
  </ChildItems>
""")
    _write(base, form_text)
    _write(result, form_text)
    report = MergeReport()

    validate_form_result(result, report, base_form_path=base)

    assert not any(item.code == "FORM_DUPLICATE_CHILD_ITEM_NAME" for item in report.conflicts)


def test_form_validator_reports_new_duplicate_child_item_names(tmp_path: Path) -> None:
    base = tmp_path / "base.xml"
    result = tmp_path / "result.xml"
    _write(
        base,
        _form(body="""
  <ChildItems>
    <Group name="Parent1" id="1"><ChildItems><ContextMenu name="SameMenu" id="2" /></ChildItems></Group>
  </ChildItems>
"""),
    )
    _write(
        result,
        _form(body="""
  <ChildItems>
    <Group name="Parent1" id="1"><ChildItems><ContextMenu name="SameMenu" id="2" /></ChildItems></Group>
    <Group name="Parent2" id="3"><ChildItems><ContextMenu name="SameMenu" id="4" /></ChildItems></Group>
  </ChildItems>
"""),
    )
    report = MergeReport()

    validate_form_result(result, report, base_form_path=base)

    assert any(item.code == "FORM_DUPLICATE_CHILD_ITEM_NAME" for item in report.conflicts)


def test_form_id_collision_added_node(tmp_path: Path) -> None:
    base_form = _form(body="""
  <ChildItems>
    <InputField name="Current" id="3" />
  </ChildItems>
""")
    ext_form = _form(body="""
  <ChildItems>
    <InputField name="Current" id="3" />
    <InputField name="Added" id="3" />
  </ChildItems>
  <BaseForm>
    <ChildItems>
      <InputField name="Current" id="3" />
    </ChildItems>
  </BaseForm>
""")

    root, report, _ = _merge(tmp_path, base_form=base_form, ext_form=ext_form)

    assert not report.conflicts
    added = _find_first(root, "Added")
    assert added.attrib["id"] != "3"


def test_form_validator_accepts_common_datapath_patterns(tmp_path: Path) -> None:
    form_path = tmp_path / "Form.xml"
    _write(
        form_path,
        _form(
            body="""
  <ChildItems>
    <Table name="Список" id="1">
      <ChildItems>
        <InputField name="ПолеItems" id="2"><DataPath>Items.Список.CurrentData.Ссылка</DataPath></InputField>
      </ChildItems>
    </Table>
  </ChildItems>
  <Attributes>
    <Attribute name="ИнформацияДляКонтрагентов" id="10" />
    <Attribute name="СписокДокументыПредприятия" id="11" />
    <Attribute name="Дескрипторы" id="12" />
    <Attribute name="ПриложенияРезультата" id="13" />
  </Attributes>
  <Commands />
  <Parameters />
  <DataPath>ИнформацияДляКонтрагентов[0].НаименованиеУчетнойЗаписи</DataPath>
  <DataPath>~СписокДокументыПредприятия.Ref</DataPath>
  <DataPath>~Дескрипторы.Отключен</DataPath>
  <DataPath>ПриложенияРезультата[1].ИндексКартинки</DataPath>
  <DataPath>1/0:6727aade-d9bc-4506-86e0-9c74ef590633</DataPath>
  <DataPath>1/-3</DataPath>
  <DataPath>12</DataPath>
""",
        ),
    )

    report = MergeReport()
    validate_form_result(form_path, report)

    assert not [item for item in report.conflicts if item.code == "FORM_DATAPATH_UNRESOLVED"]


def test_form_validator_ignores_preexisting_unresolved_datapath(tmp_path: Path) -> None:
    base = tmp_path / "base.xml"
    result = tmp_path / "result.xml"
    form_text = _form(
        body="""
  <ChildItems>
    <InputField name="Поле" id="1"><DataPath>СтарыйБитыйПуть.Поле</DataPath></InputField>
  </ChildItems>
  <Attributes />
""",
    )
    _write(base, form_text)
    _write(result, form_text)
    report = MergeReport()

    validate_form_result(result, report, base_form_path=base)

    assert not [item for item in report.conflicts if item.code == "FORM_DATAPATH_UNRESOLVED"]


def test_form_validator_keeps_real_unresolved_datapath(tmp_path: Path) -> None:
    form_path = tmp_path / "Form.xml"
    _write(
        form_path,
        _form(
            body="""
  <ChildItems>
    <InputField name="Поле" id="1"><DataPath>НесуществующийАтрибут.Поле</DataPath></InputField>
  </ChildItems>
  <Attributes>
    <Attribute name="СуществующийАтрибут" id="2" />
  </Attributes>
""",
        ),
    )

    report = MergeReport()
    validate_form_result(form_path, report)

    assert any(item.code == "FORM_DATAPATH_UNRESOLVED" and item.details == "НесуществующийАтрибут.Поле" for item in report.conflicts)
