from __future__ import annotations

import json
from pathlib import Path

import pytest

from cfmerge.bsl_merge import merge_bsl
from cfmerge.config_dump_info import regenerate_config_dump_info
from cfmerge.conflicts import MergeConflict
from cfmerge.form_merge import merge_form_visual
from cfmerge.merge_engine import merge
from cfmerge.metadata_merge import merge_configuration
from cfmerge.models import MergeConfig, MergeReport
from cfmerge.validators import validate_bsl_tree, validate_xml_tree


MD = "http://v8.1c.ru/8.3/MDClasses"
LF = "http://v8.1c.ru/8.3/xcf/logform"
DUMP = "http://v8.1c.ru/8.3/xcf/dumpinfo"


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


def _data_processor(name: str, uuid: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="{MD}">
\t<DataProcessor uuid="{uuid}">
\t\t<Properties>
\t\t\t<Name>{name}</Name>
\t\t</Properties>
\t</DataProcessor>
</MetaDataObject>
'''


def _dump(entries: dict[str, str]) -> str:
    rows = "\n".join(f'\t\t<Metadata name="{name}" id="{ident}" configVersion="old"/>' for name, ident in entries.items())
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<ConfigDumpInfo xmlns="{DUMP}" format="Hierarchical" version="2.20">
\t<ConfigVersions>
{rows}
\t</ConfigVersions>
</ConfigDumpInfo>
'''


def _report(configuration_name: str, child_blocks: list[str], *, own: bool = True) -> str:
    belonging = "Собственный" if own else "Заимствованный"
    children = "".join(child_blocks)
    return (
        f'\t- Конфигурации.{configuration_name}\n'
        f'\t\tИмя: "{configuration_name}"\n'
        f'\t\tПринадлежностьОбъекта: "{belonging}"\n'
        f'\t\tОбъектРасширяемойКонфигурации: ""\n'
        f"{children}"
    )


def _report_block(full_name: str, *, own: bool = True, body: list[str] | None = None) -> str:
    belonging = "Собственный" if own else "Заимствованный"
    body = body or []
    body_lines = "".join(f"\t\t{line}\n" for line in body)
    return (
        f'\t\t- {full_name}\n'
        f'\t\t\tИмя: "{full_name.split(".")[-1]}"\n'
        f'\t\t\tПринадлежностьОбъекта: "{belonging}"\n'
        f'\t\t\tОбъектРасширяемойКонфигурации: ""\n'
        f"{body_lines}"
    )


def test_change_and_validate_mismatch_is_error_and_not_applied() -> None:
    base = (
        "Процедура Цель()\r\n"
        "\tБазаИзменилась();\r\n"
        "КонецПроцедуры\r\n"
    )
    ext = (
        '&ИзменениеИКонтроль("Цель")\r\n'
        "Процедура НСК_Цель()\r\n"
        "\t#Удаление\r\n"
        "\tСтарыйКод();\r\n"
        "\t#КонецУдаления\r\n"
        "\t#Вставка\r\n"
        "\tНовыйКод();\r\n"
        "\t#КонецВставки\r\n"
        "КонецПроцедуры\r\n"
    )

    with pytest.raises(MergeConflict) as exc:
        merge_bsl(base, ext, "CommonModules/Модуль/Ext/Module.bsl")

    assert exc.value.code == "CHANGE_AND_VALIDATE_BASE_MISMATCH"
    assert "НовыйКод" not in base
    assert "actual_base" in exc.value.context["diff"]


def test_configuration_merge_uses_xml_name_when_physical_filename_is_escaped(tmp_path: Path) -> None:
    cf = tmp_path / "cf"
    cfu = tmp_path / "cfu"
    out = tmp_path / "out.xml"
    _write(cf / "Configuration.xml", _config("Base", "\t\t\t<CommonModule>Базовый</CommonModule>\n"))
    _write(cf / "CommonModules" / "Базовый.xml", _common_module("Базовый", "base-id"))
    _write(cfu / "Configuration.xml", _config("Ext", "\t\t\t<CommonModule>омНовый</CommonModule>\n"))
    _write(cfu / "CommonModules" / "#U043e#U043c#U041d#U043e#U0432#U044b#U0439.xml", _common_module("омНовый", "ext-id"))

    report = MergeReport()
    merge_configuration(cf / "Configuration.xml", cfu / "Configuration.xml", out, report)

    text = out.read_text(encoding="utf-8-sig")
    assert "<CommonModule>омНовый</CommonModule>" in text
    assert report.objects["added"][0]["path"] == "CommonModules/#U043e#U043c#U041d#U043e#U0432#U044b#U0439.xml"


def test_config_dump_info_preserves_base_ids_and_adds_native_extension_objects(tmp_path: Path) -> None:
    out = tmp_path / "merged"
    _write(out / "Configuration.xml", _config("Base", "\t\t\t<CommonModule>Общий</CommonModule>\n\t\t\t<CommonModule>омНовый</CommonModule>\n\t\t\t<DataProcessor>омОбработка</DataProcessor>\n"))
    _write(out / "CommonModules" / "Общий.xml", _common_module("Общий", "base-xml-uuid"))
    _write(out / "CommonModules" / "омНовый.xml", _common_module("омНовый", "ext-xml-uuid"))
    _write(out / "DataProcessors" / "омОбработка.xml", _data_processor("омОбработка", "dp-xml-uuid"))
    _write(out / "Ext" / "ManagedApplicationModule.bsl", "Процедура ПриНачалеРаботыСистемы()\nКонецПроцедуры\n")
    base_info = tmp_path / "base-info.xml"
    ext_info = tmp_path / "ext-info.xml"
    _write(base_info, _dump({
        "Configuration.Base": "base-cfg-id",
        "Configuration.Base.ManagedApplicationModule": "base-root-module-id",
        "CommonModule.Общий": "base-common-id",
    }))
    _write(ext_info, _dump({
        "CommonModule.Общий": "adopted-common-id",
        "CommonModule.омНовый": "ext-common-id",
        "DataProcessor.омОбработка": "ext-dp-id",
    }))

    report = MergeReport()
    regenerate_config_dump_info(out, base_info, ext_info, report)
    text = (out / "ConfigDumpInfo.xml").read_text(encoding="utf-8-sig")

    assert 'name="CommonModule.Общий" id="base-common-id"' in text
    assert 'name="CommonModule.омНовый" id="ext-common-id"' in text
    assert 'name="DataProcessor.омОбработка" id="ext-dp-id"' in text
    assert 'name="Configuration.Base.ManagedApplicationModule" id="base-root-module-id"' in text
    assert "Configuration.Русский.ManagedApplicationModule" not in text


def test_dry_run_writes_reports_and_does_not_touch_out(tmp_path: Path) -> None:
    cf = tmp_path / "cf"
    cfu = tmp_path / "cfu"
    out = tmp_path / "merged"
    _write(cf / "Configuration.xml", _config("Base", ""))
    _write(cf / "ConfigDumpInfo.xml", _dump({"Configuration.Base": "base-cfg-id"}))
    _write(cf / "ОтчетПоКонфигурации.txt", _report("Base", [
        _report_block("ОбщиеМодули.Базовый", body=['Синоним: ""']),
    ]))
    _write(cfu / "Configuration.xml", _config("Ext", "\t\t\t<CommonModule>омНовый</CommonModule>\n"))
    _write(cfu / "CommonModules" / "#U043e#U043c.xml", _common_module("омНовый", "ext-id"))
    _write(cfu / "ConfigDumpInfo.xml", _dump({"CommonModule.омНовый": "ext-id"}))
    _write(cfu / "ОтчетПоКонфигурации.txt", _report("Ext", [
        _report_block("ОбщиеМодули.омНовый", body=['Синоним: "Ом новый"']),
    ], own=False))
    report_path = tmp_path / "dry.json"
    human_path = tmp_path / "dry.txt"

    report = merge(MergeConfig(
        cf_dir=cf,
        cfu_dir=cfu,
        out_dir=out,
        report_path=report_path,
        human_report_path=human_path,
        dry_run=True,
    ))

    assert not out.exists()
    assert report_path.exists()
    assert human_path.exists()
    data = json.loads(report_path.read_text(encoding="utf-8-sig"))
    assert data["input"]["dry_run"] is True
    assert data["summary"]["files_added"] >= 1
    assert report.summary["files_added"] >= 1


def test_form_property_delta_is_reported_when_not_materialized(tmp_path: Path) -> None:
    base = tmp_path / "base.xml"
    ext = tmp_path / "ext.xml"
    out = tmp_path / "out.xml"
    _write(base, f'''<?xml version="1.0" encoding="UTF-8"?>
<Form xmlns="{LF}">
\t<ChildItems>
\t\t<InputField name="Поле">
\t\t\t<Title>Base</Title>
\t\t</InputField>
\t</ChildItems>
</Form>
''')
    _write(ext, f'''<?xml version="1.0" encoding="UTF-8"?>
<Form xmlns="{LF}">
\t<BaseForm>
\t\t<ChildItems>
\t\t\t<InputField name="Поле">
\t\t\t\t<Title>Base</Title>
\t\t\t</InputField>
\t\t</ChildItems>
\t</BaseForm>
\t<ChildItems>
\t\t<InputField name="Поле">
\t\t\t<Title>Ext</Title>
\t\t</InputField>
\t</ChildItems>
</Form>
''')

    report = MergeReport()
    merge_form_visual(base, ext, out, "Form.xml", report)

    assert any(w.code == "FORM_PROPERTY_DELTA_NOT_APPLIED" for w in report.warnings)
    assert "<Title>Base</Title>" in out.read_text(encoding="utf-8-sig")


def test_validators_catch_plain_result_artifacts_without_false_string_hits(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _write(out / "Configuration.xml", _config("Base", ""))
    _write(out / "CommonModules" / "Модуль.xml", _common_module("Модуль", "id", adopted=True))
    _write(out / "CommonModules" / "Модуль" / "Ext" / "Module.bsl", 'Текст = "&ПереданныеДела";\n&Перед("Цель")\nПроцедура НСК()\nКонецПроцедуры\n')
    _write(out / "Catalogs" / "Справочник" / "Forms" / "Форма" / "Ext" / "Form.xml", f'<Form xmlns="{LF}"><BaseForm/><Events><Event name="OnOpen" callType="Before">НСК</Event></Events></Form>')
    report = MergeReport()

    validate_xml_tree(out, report)
    validate_bsl_tree(out, report)

    codes = {c.code for c in report.conflicts}
    assert "ADOPTED_WRAPPER_LEAKED" in codes
    assert "FORM_CALLTYPE_LEFT" in codes
    assert "FORM_BASEFORM_LEFT" in codes
    assert "BSL_EXTENSION_MARKER_LEFT" in codes
    assert sum(c.code == "BSL_EXTENSION_MARKER_LEFT" for c in report.conflicts) == 1
