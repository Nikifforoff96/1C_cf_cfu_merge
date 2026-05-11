from __future__ import annotations

from cfmerge.bsl_merge import merge_bsl
from cfmerge.bsl_parser import parse_module


def test_parser_keeps_directives_across_blank_line_before_method() -> None:
    text = (
        '&НаКлиенте\r\n'
        '&После("Зарегистрировать") \r\n'
        '\r\n'
        'Процедура НСК_Зарегистрировать(Команда)\r\n'
        '\tСообщить("after");\r\n'
        'КонецПроцедуры\r\n'
    )

    method = parse_module(text).methods[0]

    assert method.local_name == "НСК_Зарегистрировать"
    assert method.extension_annotation == "after"
    assert method.target_name == "Зарегистрировать"
    assert method.compile_directives == ["&НаКлиенте"]


def test_parser_supports_async_procedure_and_function() -> None:
    text = (
        '&НаКлиенте\r\n'
        'Асинх Процедура НСК_УстановитьРецензирование(СтрокаФайла)\r\n'
        '\tОжидать Обещание;\r\n'
        'КонецПроцедуры\r\n'
        '\r\n'
        'Асинх Функция НСК_СпроситьПроРольФайла()\r\n'
        '\tВозврат Истина;\r\n'
        'КонецФункции\r\n'
    )

    proc, func = parse_module(text).methods

    assert proc.async_method is True
    assert proc.kind == "procedure"
    assert proc.compile_directives == ["&НаКлиенте"]
    assert func.async_method is True
    assert func.kind == "function"


def test_before_after_are_inlined_without_helper() -> None:
    base = (
        '&НаКлиенте\r\n'
        'Процедура ПриСозданииНаСервере(Отказ, СтандартнаяОбработка)\r\n'
        '\tБаза();\r\n'
        'КонецПроцедуры\r\n'
    )
    ext = (
        '&НаКлиенте\r\n'
        '&Перед("ПриСозданииНаСервере")\r\n'
        'Процедура НСК_До(Отказ, СтандартнаяОбработка)\r\n'
        '\tДо();\r\n'
        'КонецПроцедуры\r\n'
        '\r\n'
        '&НаКлиенте\r\n'
        '&После("ПриСозданииНаСервере")\r\n'
        'Процедура НСК_После(Отказ, СтандартнаяОбработка)\r\n'
        '\tПосле();\r\n'
        'КонецПроцедуры\r\n'
    )

    result = merge_bsl(base, ext, "Form/Module.bsl").text

    assert "__cfmerge__orig__" not in result
    assert "Процедура НСК_До" not in result
    assert "Процедура НСК_После" not in result
    assert result.index("\tДо();") < result.index("\tБаза();") < result.index("\tПосле();")
    assert "КонецПроцедуры\r\n\r\n" in result


def test_change_and_validate_markers_are_case_insensitive() -> None:
    base = (
        'Функция НайтиКонтрагента(ОбъектXDTO) Экспорт\r\n'
        '\tВозврат Неопределено;\r\n'
        'КонецФункции\r\n'
    )
    ext = (
        '&ИзменениеИКонтроль("НайтиКонтрагента")\r\n'
        'Функция НСК_НайтиКонтрагента(ОбъектXDTO)\r\n'
        '\t#Вставка\r\n'
        '\tВозврат Контрагент;\r\n'
        '\t#Конецвставки\r\n'
        '\t#Удаление\r\n'
        '\tВозврат Неопределено;\r\n'
        '\t#КонецУдаления\r\n'
        'КонецФункции\r\n'
    )

    result = merge_bsl(base, ext, "Module.bsl").text

    assert "#Конецвставки" not in result
    assert "Возврат Контрагент;" in result
    assert "Возврат Неопределено;" not in result


def test_after_inline_terminates_base_last_statement() -> None:
    base = (
        'Процедура ВестиУчетТоваровИУслугПриИзменении(Элемент)\r\n'
        '\tУстановитьДоступностьОпций(ЭтотОбъект)\r\n'
        'КонецПроцедуры\r\n'
    )
    ext = (
        '&После("ВестиУчетТоваровИУслугПриИзменении")\r\n'
        'Процедура НСК_После(Элемент)\r\n'
        '\tПосле();\r\n'
        'КонецПроцедуры\r\n'
    )

    result = merge_bsl(base, ext, "Form/Module.bsl").text

    assert "УстановитьДоступностьОпций(ЭтотОбъект);\r\n\tПосле();" in result


def test_after_inline_with_early_return_does_not_warn() -> None:
    base = (
        '\u041f\u0440\u043e\u0446\u0435\u0434\u0443\u0440\u0430 \u0426\u0435\u043b\u044c()\r\n'
        '\t\u0412\u043e\u0437\u0432\u0440\u0430\u0442;\r\n'
        '\u041a\u043e\u043d\u0435\u0446\u041f\u0440\u043e\u0446\u0435\u0434\u0443\u0440\u044b\r\n'
    )
    ext = (
        '&\u041f\u043e\u0441\u043b\u0435("\u0426\u0435\u043b\u044c")\r\n'
        '\u041f\u0440\u043e\u0446\u0435\u0434\u0443\u0440\u0430 \u041d\u0421\u041a_\u041f\u043e\u0441\u043b\u0435()\r\n'
        '\t\u041f\u043e\u0441\u043b\u0435();\r\n'
        '\u041a\u043e\u043d\u0435\u0446\u041f\u0440\u043e\u0446\u0435\u0434\u0443\u0440\u044b\r\n'
    )

    result = merge_bsl(base, ext, "Module.bsl")

    assert result.warnings == []
    assert "\u041f\u043e\u0441\u043b\u0435();" in result.text


def test_after_with_blank_line_annotation_is_not_appended_as_plain_method() -> None:
    base = (
        '&НаКлиенте\r\n'
        'Процедура Зарегистрировать(Команда)\r\n'
        '\tЗарегистрироватьДокумент();\r\n'
        'КонецПроцедуры\r\n'
    )
    ext = (
        '&НаКлиенте\r\n'
        '&После("Зарегистрировать") \r\n'
        '\r\n'
        'Процедура НСК_Зарегистрировать(Команда)\r\n'
        '\tМассивФайлов = Новый Массив;\r\n'
        'КонецПроцедуры\r\n'
    )

    result = merge_bsl(base, ext, "Form/Module.bsl").text

    assert "Процедура НСК_Зарегистрировать" not in result
    assert "МассивФайлов = Новый Массив;" in result
    assert result.index("ЗарегистрироватьДокумент();") < result.index("МассивФайлов = Новый Массив;")


def test_async_extension_only_method_is_copied_as_plain_method() -> None:
    ext = (
        '&НаКлиенте\r\n'
        'Асинх Процедура НСК_УстановитьРецензирование(СтрокаФайла)\r\n'
        '\tОжидать Обещание;\r\n'
        'КонецПроцедуры\r\n'
    )

    result = merge_bsl("", ext, "Form/Module.bsl").text

    assert "&НаКлиенте" in result
    assert "Асинх Процедура НСК_УстановитьРецензирование" in result


def test_instead_continue_call_still_uses_helper() -> None:
    base = (
        'Функция Значение(Параметр)\r\n'
        '\tВозврат Параметр;\r\n'
        'КонецФункции\r\n'
    )
    ext = (
        '&Вместо("Значение")\r\n'
        'Функция НСК_Значение(Параметр)\r\n'
        '\tВозврат ПродолжитьВызов(Параметр);\r\n'
        'КонецФункции\r\n'
    )

    result = merge_bsl(base, ext, "Module.bsl").text

    assert "__cfmerge__orig__Значение" in result
    assert "ПродолжитьВызов" not in result
