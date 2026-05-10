from __future__ import annotations

from pathlib import Path

from cfmerge.models import MergeReport
from cfmerge.report_merge import merge_configuration_report, parse_report_text


ROOT = Path(__file__).resolve().parents[1]


def _write_report(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-16", newline="")


def _block(
    full_name: str,
    *,
    own: bool,
    indent: int = 2,
    body: list[str] | None = None,
    children: list[str] | None = None,
) -> str:
    prefix = "\t" * indent
    prop = "\t" * (indent + 1)
    body = body or []
    children = children or []
    lines = [
        f"{prefix}- {full_name}",
        f'{prop}Имя: "{full_name.split(".")[-1]}"',
        f'{prop}ПринадлежностьОбъекта: "{"Собственный" if own else "Заимствованный"}"',
        f'{prop}ОбъектРасширяемойКонфигурации: ""',
    ]
    lines.extend(f"{prop}{line}" for line in body)
    text = "\n".join(lines) + "\n"
    return text + "".join(children)


def _report(configuration_name: str, children: list[str], *, own: bool) -> str:
    return (
        f"\t- Конфигурации.{configuration_name}\n"
        f'\t\tИмя: "{configuration_name}"\n'
        f'\t\tПринадлежностьОбъекта: "{"Собственный" if own else "Заимствованный"}"\n'
        f'\t\tОбъектРасширяемойКонфигурации: ""\n'
        + "".join(children)
    )


def test_parse_report_preserves_multiline_property_and_nested_blocks() -> None:
    text = _report("База", [
        _block("ОбщиеМодули.Модуль1", own=True, body=[
            'Синоним: "Модуль 1"',
            'Тип:\n\t\t\t"Строка(10),\n\t\t\t Число(5)"',
        ], children=[
            _block("ОбщиеМодули.Модуль1.Формы.Форма", own=True, indent=3, body=['ТипФормы: "Управляемая"']),
        ]),
    ], own=True)

    doc = parse_report_text(text)

    assert len(doc.blocks) == 1
    root = doc.blocks[0]
    assert root.full_name == "Конфигурации.База"
    assert root.children[0].full_name == "ОбщиеМодули.Модуль1"
    assert root.children[0].children[0].full_name == "ОбщиеМодули.Модуль1.Формы.Форма"
    assert any("Число(5)" in line for line in root.children[0].property_lines)


def test_merge_report_keeps_base_configuration_and_orders_top_level_own_blocks(tmp_path: Path) -> None:
    base = tmp_path / "cf" / "ОтчетПоКонфигурации.txt"
    ext = tmp_path / "cfu" / "ОтчетПоКонфигурации.txt"
    out = tmp_path / "out" / "ОтчетПоКонфигурации.txt"
    _write_report(base, _report("BaseCfg", [
        _block("ОбщиеМодули.А", own=True),
        _block("ОбщиеМодули.Б", own=True),
        _block("Обработки.Базовая", own=True),
    ], own=True))
    _write_report(ext, _report("ExtCfg", [
        _block("ОбщиеМодули.Б", own=False),
        _block("ОбщиеМодули.НовыйМодуль", own=True, body=['Синоним: "Новый модуль"']),
        _block("Обработки.НоваяОбработка", own=True, body=['Синоним: "Новая обработка"']),
    ], own=False))
    report = MergeReport()

    merge_configuration_report(base, ext, out, report)

    merged = out.read_text(encoding="utf-16")
    assert "# cfmerge:" not in merged
    assert "Конфигурации.ExtCfg" not in merged
    doc = parse_report_text(merged)
    names = [child.full_name for child in doc.blocks[0].children]
    assert names == [
        "ОбщиеМодули.А",
        "ОбщиеМодули.Б",
        "ОбщиеМодули.НовыйМодуль",
        "Обработки.Базовая",
        "Обработки.НоваяОбработка",
    ]
    data = out.read_bytes()
    assert data.startswith(b"\xff\xfe")
    assert b"\r\n" not in data


def test_merge_report_inserts_nested_own_block_into_adopted_parent(tmp_path: Path) -> None:
    base = tmp_path / "cf" / "ОтчетПоКонфигурации.txt"
    ext_dir = tmp_path / "cfu"
    ext = ext_dir / "ОтчетПоКонфигурации.txt"
    out = tmp_path / "out" / "ОтчетПоКонфигурации.txt"
    _write_report(base, _report("BaseCfg", [
        _block("Справочники.Настройки", own=True, body=['Синоним: "Настройки"'], children=[
            _block("Справочники.Настройки.Макеты.БазовыйМакет", own=True, indent=3),
        ]),
    ], own=True))
    _write_report(ext, _report("ExtCfg", [
        _block("Справочники.Настройки", own=False, children=[
            _block("Справочники.Настройки.Макеты.НовыйМакет", own=True, indent=3, body=['ТипМакета: "ТабличныйДокумент"']),
        ]),
    ], own=False))
    _write_report(ext_dir / "СобственныеОбъекты.txt", "Справочники.Настройки.Макеты.НовыйМакет\n")
    report = MergeReport()

    merge_configuration_report(base, ext, out, report)

    doc = parse_report_text(out.read_text(encoding="utf-16"))
    catalog = doc.blocks[0].children[0]
    assert [child.full_name for child in catalog.children] == [
        "Справочники.Настройки.Макеты.БазовыйМакет",
        "Справочники.Настройки.Макеты.НовыйМакет",
    ]


def test_merge_report_keeps_nested_own_children_inside_own_top_level_object(tmp_path: Path) -> None:
    base = tmp_path / "cf" / "ОтчетПоКонфигурации.txt"
    ext_dir = tmp_path / "cfu"
    ext = ext_dir / "ОтчетПоКонфигурации.txt"
    out = tmp_path / "out" / "ОтчетПоКонфигурации.txt"
    _write_report(base, _report("BaseCfg", [
        _block("Обработки.Базовая", own=True),
    ], own=True))
    _write_report(ext, _report("ExtCfg", [
        _block("Обработки.Новая", own=True, body=['ОсновнаяФорма: "Обработка.Новая.Форма.Форма"'], children=[
            _block("Обработки.Новая.Реквизиты.Версия", own=True, indent=3),
            _block("Обработки.Новая.Формы.Форма", own=True, indent=3),
        ]),
    ], own=False))
    _write_report(ext_dir / "СобственныеОбъекты.txt", "\n".join([
        "Обработки.Новая",
        "Обработки.Новая.Реквизиты.Версия",
        "Обработки.Новая.Формы.Форма",
    ]) + "\n")
    report = MergeReport()

    merge_configuration_report(base, ext, out, report)

    doc = parse_report_text(out.read_text(encoding="utf-16"))
    own_processor = doc.blocks[0].children[1]
    assert own_processor.full_name == "Обработки.Новая"
    assert [child.full_name for child in own_processor.children] == [
        "Обработки.Новая.Реквизиты.Версия",
        "Обработки.Новая.Формы.Форма",
    ]


def test_merge_report_skips_adopted_extension_blocks(tmp_path: Path) -> None:
    base = tmp_path / "cf" / "ОтчетПоКонфигурации.txt"
    ext = tmp_path / "cfu" / "ОтчетПоКонфигурации.txt"
    out = tmp_path / "out" / "ОтчетПоКонфигурации.txt"
    _write_report(base, _report("BaseCfg", [_block("ОбщиеМодули.Базовый", own=True)], own=True))
    _write_report(ext, _report("ExtCfg", [_block("ОбщиеМодули.Заимствованный", own=False)], own=False))
    report = MergeReport()

    merge_configuration_report(base, ext, out, report)

    text = out.read_text(encoding="utf-16")
    assert "ОбщиеМодули.Заимствованный" not in text


def test_merge_report_missing_parent_is_manual_review(tmp_path: Path) -> None:
    base = tmp_path / "cf" / "ОтчетПоКонфигурации.txt"
    ext_dir = tmp_path / "cfu"
    ext = ext_dir / "ОтчетПоКонфигурации.txt"
    out = tmp_path / "out" / "ОтчетПоКонфигурации.txt"
    _write_report(base, _report("BaseCfg", [_block("Справочники.Базовый", own=True)], own=True))
    _write_report(ext, _report("ExtCfg", [
        _block("Справочники.ОтсутствующийРодитель", own=False, children=[
            _block("Справочники.ОтсутствующийРодитель.Макеты.НовыйМакет", own=True, indent=3),
        ]),
    ], own=False))
    _write_report(ext_dir / "СобственныеОбъекты.txt", "Справочники.ОтсутствующийРодитель.Макеты.НовыйМакет\n")
    report = MergeReport()

    merge_configuration_report(base, ext, out, report)

    assert any(item.code == "CONFIGURATION_REPORT_PARENT_NOT_FOUND" and item.severity == "manual-review" for item in report.conflicts)
    assert "НовыйМакет" not in out.read_text(encoding="utf-16")


def test_merge_report_is_skipped_when_files_are_missing(tmp_path: Path) -> None:
    base = tmp_path / "cf" / "ОтчетПоКонфигурации.txt"
    ext = tmp_path / "cfu" / "ОтчетПоКонфигурации.txt"
    out = tmp_path / "out" / "ОтчетПоКонфигурации.txt"

    report = MergeReport()
    merge_configuration_report(base, ext, out, report)
    assert not out.exists()
    assert report.diagnostics["configuration_report_merge"]["strategy"] == "skipped"
    assert report.warnings == []

    _write_report(base, _report("BaseCfg", [_block("ОбщиеМодули.Базовый", own=True)], own=True))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("base-copy\n", encoding="utf-16", newline="")
    report = MergeReport()
    merge_configuration_report(base, ext, out, report)
    assert out.read_text(encoding="utf-16") == "base-copy\n"
    assert report.diagnostics["configuration_report_merge"] == {
        "strategy": "skipped",
        "base_exists": True,
        "extension_exists": False,
    }

    ext.unlink(missing_ok=True)
    base.unlink()
    _write_report(ext, _report("ExtCfg", [_block("ОбщиеМодули.Новый", own=True)], own=False))
    if out.exists():
        out.unlink()
    report = MergeReport()
    merge_configuration_report(base, ext, out, report)
    assert not out.exists()
    assert report.diagnostics["configuration_report_merge"] == {
        "strategy": "skipped",
        "base_exists": False,
        "extension_exists": True,
    }


def test_examples_small_report_merge_regression(tmp_path: Path) -> None:
    base = ROOT / "examples" / "small" / "cf" / "ОтчетПоКонфигурации.txt"
    ext = ROOT / "examples" / "small" / "cfu" / "ОтчетПоКонфигурации.txt"
    out = tmp_path / "ОтчетПоКонфигурации.txt"
    report = MergeReport()

    merge_configuration_report(base, ext, out, report)

    text = out.read_text(encoding="utf-16")
    assert "# cfmerge:" not in text
    doc = parse_report_text(text)
    names = [child.full_name for child in doc.blocks[0].children]
    assert names.index("ОбщиеМодули.омОбщийМодульРасширения2") == names.index("ОбщиеМодули.ОбщийМодуль1") + 1
    assert names.index("Обработки.омОбработкаРасширение2") == names.index("Обработки.Обработка1") + 1


def test_synthetic_large_shape_report_merge_regression(tmp_path: Path) -> None:
    base = tmp_path / "cf" / "ОтчетПоКонфигурации.txt"
    ext_dir = tmp_path / "cfu"
    ext = ext_dir / "ОтчетПоКонфигурации.txt"
    out = tmp_path / "ОтчетПоКонфигурации.txt"
    report = MergeReport()
    _write_report(base, _report("BaseCfg", [
        _block("ОбщиеМодули.Базовый", own=True),
        _block("Справочники.Настройки", own=True, children=[
            _block("Справочники.Настройки.Макеты.БазовыйМакет", own=True, indent=3),
        ]),
    ], own=True))
    _write_report(ext, _report("ExtCfg", [
        _block("ОбщиеМодули.Новый", own=True),
        _block("HTTPСервисы.НСК_Интеграция", own=True, children=[
            _block("HTTPСервисы.НСК_Интеграция.ШаблоныURL.CreateUpdate", own=True, indent=3, children=[
                _block("HTTPСервисы.НСК_Интеграция.ШаблоныURL.CreateUpdate.Методы.POST", own=True, indent=4),
            ]),
        ]),
        _block("Справочники.Настройки", own=False, children=[
            _block("Справочники.Настройки.Макеты.НовыйМакет", own=True, indent=3),
        ]),
        _block("Обработки.Новая", own=True, children=[
            _block("Обработки.Новая.Реквизиты.ВерсияПравил", own=True, indent=3),
            _block("Обработки.Новая.Формы.Форма", own=True, indent=3),
        ]),
    ], own=False))
    _write_report(ext_dir / "СобственныеОбъекты.txt", "\n".join([
        "ОбщиеМодули.Новый",
        "HTTPСервисы.НСК_Интеграция",
        "HTTPСервисы.НСК_Интеграция.ШаблоныURL.CreateUpdate",
        "HTTPСервисы.НСК_Интеграция.ШаблоныURL.CreateUpdate.Методы.POST",
        "Справочники.Настройки.Макеты.НовыйМакет",
        "Обработки.Новая",
        "Обработки.Новая.Реквизиты.ВерсияПравил",
        "Обработки.Новая.Формы.Форма",
    ]) + "\n")

    merge_configuration_report(base, ext, out, report)

    doc = parse_report_text(out.read_text(encoding="utf-16"))
    root = doc.blocks[0]
    names = [child.full_name for child in root.children]
    assert names[names.index("ОбщиеМодули.Базовый") + 1] == "ОбщиеМодули.Новый"
    assert "HTTPСервисы.НСК_Интеграция" in names
    assert "Справочники.Настройки" in names
    assert "Обработки.Новая" in names

    by_name = {child.full_name: child for child in root.children}
    http_service = by_name["HTTPСервисы.НСК_Интеграция"]
    assert http_service.children[0].full_name == "HTTPСервисы.НСК_Интеграция.ШаблоныURL.CreateUpdate"
    assert http_service.children[0].children[0].full_name == "HTTPСервисы.НСК_Интеграция.ШаблоныURL.CreateUpdate.Методы.POST"

    catalog = by_name["Справочники.Настройки"]
    assert any(child.full_name == "Справочники.Настройки.Макеты.НовыйМакет" for child in catalog.children)

    processor = by_name["Обработки.Новая"]
    child_names = [child.full_name for child in processor.children]
    assert "Обработки.Новая.Реквизиты.ВерсияПравил" in child_names
    assert "Обработки.Новая.Формы.Форма" in child_names
