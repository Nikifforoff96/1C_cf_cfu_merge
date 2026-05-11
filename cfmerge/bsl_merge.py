from __future__ import annotations

import re
from difflib import unified_diff
from dataclasses import dataclass, field

from .bsl_parser import BslMethod, BslModule, method_by_name, parse_module
from .conflicts import MergeConflict


DELETE_START = {item.casefold() for item in {"#Удаление", "#Удалить"}}
DELETE_END = {item.casefold() for item in {"#КонецУдаления", "#КонецУдалить"}}
INSERT_START = {item.casefold() for item in {"#Вставка", "#Вставить"}}
INSERT_END = {item.casefold() for item in {"#КонецВставки", "#КонецВставить"}}


@dataclass(slots=True)
class EventHook:
    target_handler: str
    extension_handler: str
    mode: str
    path: str
    event_name: str


@dataclass(slots=True)
class BslMergeResult:
    text: str
    actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def normalize_body(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def canonical_body(text: str) -> str:
    text = normalize_body(text)
    return re.sub(r"\s+", "", text).lower()


def _marker(line: str) -> str:
    return line.strip().split()[0].casefold() if line.strip().startswith("#") else ""


def change_and_validate_bodies(body: str) -> tuple[str, str]:
    expected: list[str] = []
    result: list[str] = []
    state: str | None = None
    for line in body.splitlines(keepends=True):
        marker = _marker(line)
        if marker in DELETE_START:
            state = "delete"
            continue
        if marker in DELETE_END:
            state = None
            continue
        if marker in INSERT_START:
            state = "insert"
            continue
        if marker in INSERT_END:
            state = None
            continue
        if state == "delete":
            expected.append(line)
            continue
        if state == "insert":
            result.append(line)
            continue
        expected.append(line)
        result.append(line)
    return "".join(expected), "".join(result)


def strip_extension_markers(body: str) -> str:
    _, result = change_and_validate_bodies(body)
    return result


def signature_compatible(base: BslMethod, ext: BslMethod) -> bool:
    return base.kind == ext.kind and len(base.params) == len(ext.params)


def call_args(method: BslMethod) -> str:
    return ", ".join(param.name for param in method.params)


def method_header(method: BslMethod, name: str | None = None, export: bool | None = None, kind: str | None = None) -> str:
    name = name or method.local_name
    kind = kind or method.kind
    keyword = "Процедура" if kind == "procedure" else "Функция"
    if method.async_method:
        keyword = f"Асинх {keyword}"
    export_flag = method.export if export is None else export
    params = ", ".join(p.raw.strip() for p in method.params)
    tail = " Экспорт" if export_flag else ""
    return f"{keyword} {name}({params}){tail}\r\n"


def method_footer(kind: str) -> str:
    return "КонецПроцедуры\r\n" if kind == "procedure" else "КонецФункции\r\n"


def compile_directive_text(method: BslMethod) -> str:
    if not method.compile_directives:
        return ""
    return "\r\n".join(method.compile_directives) + "\r\n"


def make_method_text(method: BslMethod, body: str, name: str | None = None, export: bool | None = None) -> str:
    return compile_directive_text(method) + method_header(method, name=name, export=export) + body + method_footer(method.kind)


def ensure_blank_line_after_method_end(text: str) -> str:
    text = re.sub(
        r"(?im)^([ \t]*Конец(?:Процедуры|Функции)[ \t]*)(\r?\n)(?=[^\r\n])",
        r"\1\2\2",
        text,
    )
    if re.search(r"(?im)^[ \t]*Конец(?:Процедуры|Функции)[ \t]*(?:\r?\n)?\Z", text) and not text.endswith(("\r\n\r\n", "\n\n")):
        newline = "\r\n" if "\r\n" in text else "\n"
        text += newline if text.endswith(("\r\n", "\n")) else newline + newline
    return text


def ensure_body_terminated_before_append(body: str) -> str:
    lines = body.splitlines(keepends=True)
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        content = line.rstrip("\r\n")
        newline = line[len(content):]
        stripped = content.strip()
        if not stripped or stripped.startswith("//"):
            continue
        if stripped.startswith("#") or stripped.endswith(";"):
            return body
        lines[index] = content + ";" + newline
        return "".join(lines)
    return body


def replace_span(text: str, method: BslMethod, replacement: str) -> str:
    return text[:method.start_offset] + replacement + text[method.end_offset:]


def replace_body(text: str, method: BslMethod, body: str) -> str:
    return text[:method.body_start] + body + text[method.body_end:]


def unique_helper_name(module: BslModule, base_name: str) -> str:
    existing = {m.local_name.lower() for m in module.methods}
    candidate = f"__cfmerge__orig__{base_name}"
    if candidate.lower() not in existing:
        return candidate
    idx = 2
    while f"{candidate}_{idx}".lower() in existing:
        idx += 1
    return f"{candidate}_{idx}"


def replace_identifier_outside_literals(text: str, identifier: str, replacement: str) -> str:
    out: list[str] = []
    i = 0
    in_string = False
    ident_l = identifier.lower()
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if in_string:
            out.append(ch)
            if ch == '"' and nxt == '"':
                out.append(nxt)
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == "/" and nxt == "/":
            nl = text.find("\n", i)
            if nl < 0:
                out.append(text[i:])
                break
            out.append(text[i:nl + 1])
            i = nl + 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if text[i:i + len(identifier)].lower() == ident_l:
            before = text[i - 1] if i else ""
            after = text[i + len(identifier)] if i + len(identifier) < len(text) else ""
            if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                out.append(replacement)
                i += len(identifier)
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def contains_continue_call(body: str) -> bool:
    rewritten = replace_identifier_outside_literals(body, "ПродолжитьВызов", "__CFMERGE_CONTINUE__")
    return "__CFMERGE_CONTINUE__" in rewritten


def rewrite_continue_call(body: str, helper_name: str) -> str:
    return replace_identifier_outside_literals(body, "ПродолжитьВызов", helper_name)


def renamed_method_text(method: BslMethod, helper_name: str) -> str:
    body = method.body_text
    return compile_directive_text(method) + method_header(method, name=helper_name, export=False) + body + method_footer(method.kind)


def apply_change_and_validate(text: str, path: str, ext_method: BslMethod) -> tuple[str, str | None]:
    module = parse_module(text)
    target_name = ext_method.target_name or ext_method.local_name
    target = method_by_name(module, target_name)
    if not target:
        raise MergeConflict("TARGET_METHOD_NOT_FOUND", path, f"Не найден метод {target_name}", target_name)
    if not signature_compatible(target, ext_method):
        raise MergeConflict("METHOD_SIGNATURE_MISMATCH", path, f"Несовместимая сигнатура {target_name}", target_name)
    expected, result = change_and_validate_bodies(ext_method.body_text)
    if canonical_body(expected) != canonical_body(target.body_text):
        expected_norm = normalize_body(expected)
        actual_norm = normalize_body(target.body_text)
        diff = "\n".join(unified_diff(
            expected_norm.splitlines(),
            actual_norm.splitlines(),
            fromfile="expected_original",
            tofile="actual_base",
            lineterm="",
        ))
        raise MergeConflict(
            "CHANGE_AND_VALIDATE_BASE_MISMATCH",
            path,
            f"Ожидаемый исходный текст &ИзменениеИКонтроль не совпал с текущим телом {target_name}; изменение не применено",
            target_name,
            context={
                "target_method": target_name,
                "extension_method": ext_method.local_name,
                "diff": diff[:12000],
            },
        )
    new_text = replace_body(text, target, result)
    return new_text, None


def apply_instead(text: str, path: str, ext_method: BslMethod) -> tuple[str, list[str]]:
    module = parse_module(text)
    target_name = ext_method.target_name or ext_method.local_name
    target = method_by_name(module, target_name)
    if not target:
        raise MergeConflict("TARGET_METHOD_NOT_FOUND", path, f"Не найден метод {target_name}", target_name)
    if not signature_compatible(target, ext_method):
        raise MergeConflict("METHOD_SIGNATURE_MISMATCH", path, f"Несовместимая сигнатура {target_name}", target_name)
    body = strip_extension_markers(ext_method.body_text)
    actions: list[str] = []
    if contains_continue_call(body):
        helper_name = unique_helper_name(module, target.local_name)
        helper = renamed_method_text(target, helper_name)
        rewritten_body = rewrite_continue_call(body, helper_name)
        wrapper = make_method_text(target, rewritten_body)
        text = replace_span(text, target, wrapper + "\r\n" + helper)
        actions.append(f"instead_continue:{target_name}->{helper_name}")
    else:
        text = replace_body(text, target, body)
        actions.append(f"instead:{target_name}")
    return text, actions


def _aggregate_hooks(ext_methods: list[BslMethod], event_hooks: list[EventHook]) -> dict[str, dict[str, list[BslMethod | EventHook]]]:
    hooks: dict[str, dict[str, list[BslMethod | EventHook]]] = {}
    for method in ext_methods:
        if method.extension_annotation not in {"before", "after"}:
            continue
        if not method.target_name:
            continue
        hooks.setdefault(method.target_name, {"before": [], "after": []})[method.extension_annotation].append(method)
    for hook in event_hooks:
        hooks.setdefault(hook.target_handler, {"before": [], "after": []})[hook.mode].append(hook)
    return hooks


def _hook_body(hook: BslMethod | EventHook) -> str:
    if isinstance(hook, BslMethod):
        return strip_extension_markers(hook.body_text)
    return f"\t{hook.extension_handler}({{args}});\r\n"


def body_has_early_return(body: str) -> bool:
    return re.search(r"(?im)^[ \t]*Возврат\b", body) is not None


def _apply_inline_hooks(text: str, path: str, ext_methods: list[BslMethod], event_hooks: list[EventHook]) -> tuple[str, list[str], list[str]]:
    actions: list[str] = []
    warnings: list[str] = []
    hooks = _aggregate_hooks(ext_methods, event_hooks)
    for target_name, groups in hooks.items():
        module = parse_module(text)
        target = method_by_name(module, target_name)
        if not target:
            raise MergeConflict("TARGET_METHOD_NOT_FOUND", path, f"Не найден метод для wrapper {target_name}", target_name)
        if target.kind != "procedure":
            raise MergeConflict("WRAPPER_TARGET_NOT_PROCEDURE", path, f"Before/After поддержаны для процедур: {target_name}", target_name)
        for method in groups["before"] + groups["after"]:
            if isinstance(method, BslMethod) and not signature_compatible(target, method):
                raise MergeConflict("METHOD_SIGNATURE_MISMATCH", path, f"Несовместимая сигнатура inline hook {target_name}", target_name)
        args = call_args(target)
        before_parts: list[str] = []
        for hook in groups["before"]:
            body = _hook_body(hook).replace("{args}", args)
            before_parts.append(ensure_body_terminated_before_append(body))
        after_parts: list[str] = []
        for hook in groups["after"]:
            body = _hook_body(hook).replace("{args}", args)
            after_parts.append(ensure_body_terminated_before_append(body))
        target_body = ensure_body_terminated_before_append(target.body_text) if after_parts else target.body_text
        text = replace_body(text, target, "".join(before_parts) + target_body + "".join(after_parts))
        if before_parts:
            actions.append(f"before_inline:{target_name}")
        if after_parts:
            actions.append(f"after_inline:{target_name}")
    return text, actions, warnings


def _append_plain_methods(text: str, ext_methods: list[BslMethod]) -> tuple[str, list[str]]:
    module = parse_module(text)
    existing = {m.local_name.lower() for m in module.methods}
    append: list[str] = []
    actions: list[str] = []
    for method in ext_methods:
        if method.extension_annotation:
            continue
        if method.local_name.lower() in existing:
            continue
        append.append(make_method_text(method, strip_extension_markers(method.body_text), name=method.local_name, export=method.export))
        existing.add(method.local_name.lower())
        actions.append(f"append_method:{method.local_name}")
    if append:
        sep = "\r\n" if text.endswith(("\r\n", "\n")) else "\r\n\r\n"
        text = text.rstrip() + sep + "\r\n\r\n".join(a.strip("\r\n") for a in append) + "\r\n"
    return text, actions


def clean_extension_module(text: str) -> str:
    module = parse_module(text)
    if not module.methods:
        return text
    result = text
    for method in reversed(module.methods):
        if method.extension_annotation:
            cleaned = make_method_text(method, strip_extension_markers(method.body_text), name=method.local_name, export=method.export)
            result = replace_span(result, method, cleaned)
    return result


def merge_bsl(base_text: str, ext_text: str, path: str, event_hooks: list[EventHook] | None = None) -> BslMergeResult:
    event_hooks = event_hooks or []
    ext_module = parse_module(ext_text)
    text = base_text
    actions: list[str] = []
    warnings: list[str] = []

    for method in ext_module.methods:
        if method.extension_annotation == "change_and_validate":
            text, warning = apply_change_and_validate(text, path, method)
            actions.append(f"change_and_validate:{method.target_name}")
            if warning:
                warnings.append(warning)
        elif method.extension_annotation == "instead":
            text, method_actions = apply_instead(text, path, method)
            actions.extend(method_actions)

    before_after = [m for m in ext_module.methods if m.extension_annotation in {"before", "after"}]
    text, hook_actions, hook_warnings = _apply_inline_hooks(text, path, before_after, event_hooks)
    actions.extend(hook_actions)
    warnings.extend(hook_warnings)

    text, append_actions = _append_plain_methods(text, ext_module.methods)
    actions.extend(append_actions)
    text = ensure_blank_line_after_method_end(text)
    return BslMergeResult(text=text, actions=actions, warnings=warnings)
