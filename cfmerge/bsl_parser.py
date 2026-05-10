from __future__ import annotations

import re

from .models import BslMethod, BslModule, Parameter


METHOD_RE = re.compile(r"(?im)^[ \t]*(?:(Асинх)\s+)?(Процедура|Функция)\s+([A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*)\s*\(")
END_RE = re.compile(r"(?im)^[ \t]*(КонецПроцедуры|КонецФункции)\b")
DIRECTIVE_RE = re.compile(r"^[ \t]*&([A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*)\s*(?:\((.*)\))?\s*$")
ANNOTATION_NAMES = {
    "ИзменениеИКонтроль": "change_and_validate",
    "Вместо": "instead",
    "Перед": "before",
    "После": "after",
}


def _line_start(text: str, offset: int) -> int:
    pos = text.rfind("\n", 0, offset)
    return 0 if pos < 0 else pos + 1


def _line_end(text: str, offset: int) -> int:
    pos = text.find("\n", offset)
    return len(text) if pos < 0 else pos + 1


def _previous_line_span(text: str, line_start: int) -> tuple[int, int] | None:
    if line_start <= 0:
        return None
    end = line_start - 1
    if end > 0 and text[end - 1] == "\r":
        end -= 1
    start = text.rfind("\n", 0, end)
    start = 0 if start < 0 else start + 1
    return start, line_start


def _collect_directive_start(text: str, method_line_start: int) -> tuple[int, list[str], str | None, str | None]:
    spans: list[tuple[int, int, str]] = []
    cur = method_line_start
    while True:
        prev = _previous_line_span(text, cur)
        if not prev:
            break
        start, end = prev
        line = text[start:end].strip()
        if not line:
            cur = start
            continue
        if not line.startswith("&"):
            break
        spans.append((start, end, line))
        cur = start
    spans.reverse()
    directives: list[str] = []
    annotation: str | None = None
    target: str | None = None
    for _, _, line in spans:
        match = DIRECTIVE_RE.match(line)
        if not match:
            directives.append(line)
            continue
        name = match.group(1)
        args = match.group(2) or ""
        if name in ANNOTATION_NAMES:
            annotation = ANNOTATION_NAMES[name]
            target_match = re.search(r'"([^"]+)"', args)
            target = target_match.group(1) if target_match else None
        else:
            directives.append(line)
    return (spans[0][0] if spans else method_line_start), directives, annotation, target


def _find_matching_paren(text: str, open_offset: int) -> int:
    depth = 0
    in_string = False
    i = open_offset
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if in_string:
            if ch == '"' and nxt == '"':
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == "/" and nxt == "/":
            nl = text.find("\n", i)
            i = len(text) if nl < 0 else nl + 1
            continue
        if ch == '"':
            in_string = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def split_params(params_text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    in_string = False
    i = 0
    while i < len(params_text):
        ch = params_text[i]
        nxt = params_text[i + 1] if i + 1 < len(params_text) else ""
        if in_string:
            if ch == '"' and nxt == '"':
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(params_text[start:i].strip())
            start = i + 1
        i += 1
    tail = params_text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def parse_parameters(params_text: str) -> list[Parameter]:
    result: list[Parameter] = []
    for raw in split_params(params_text):
        cleaned = " ".join(raw.replace("\r", " ").replace("\n", " ").split())
        by_value = cleaned.lower().startswith("знач ")
        name_part = cleaned[5:].strip() if by_value else cleaned
        default = None
        if "=" in name_part:
            name_part, default = name_part.split("=", 1)
            default = default.strip()
        name = name_part.strip().split()[0] if name_part.strip() else ""
        result.append(Parameter(raw=raw, name=name, default=default, by_value=by_value))
    return result


def _header_end(text: str, close_paren: int) -> int:
    nl = text.find("\n", close_paren)
    return len(text) if nl < 0 else nl + 1


def _body_bounds(text: str, method_start: int, end_match: re.Match[str]) -> tuple[int, int, int, str, str]:
    open_paren = text.find("(", method_start)
    close_paren = _find_matching_paren(text, open_paren)
    header_end = _header_end(text, close_paren)
    footer_start = end_match.start()
    footer_end = _line_end(text, end_match.start())
    return header_end, footer_start, footer_end, text[method_start:header_end], text[footer_start:footer_end]


def parse_module(text: str) -> BslModule:
    methods: list[BslMethod] = []
    search_pos = 0
    while True:
        match = METHOD_RE.search(text, search_pos)
        if not match:
            break
        method_line_start = _line_start(text, match.start())
        raw_start, directives, annotation, target = _collect_directive_start(text, method_line_start)
        end_match = END_RE.search(text, match.end())
        if not end_match:
            break
        footer_end = _line_end(text, end_match.start())
        header_end, body_end, _, header_text, footer_text = _body_bounds(text, match.start(), end_match)
        open_paren = text.find("(", match.start())
        close_paren = _find_matching_paren(text, open_paren)
        params = parse_parameters(text[open_paren + 1:close_paren]) if close_paren >= 0 else []
        full_header = text[match.start():header_end]
        export = bool(re.search(r"(?i)\bЭкспорт\b", full_header))
        kind = "procedure" if match.group(2).lower().startswith("проц") else "function"
        methods.append(BslMethod(
            local_name=match.group(3),
            target_name=target,
            kind=kind,
            async_method=bool(match.group(1)),
            params=params,
            export=export,
            compile_directives=directives,
            extension_annotation=annotation,
            raw_text=text[raw_start:footer_end],
            body_text=text[header_end:body_end],
            start_offset=raw_start,
            end_offset=footer_end,
            header_start=match.start(),
            body_start=header_end,
            body_end=body_end,
            footer_start=body_end,
            header_text=header_text,
            footer_text=footer_text,
        ))
        search_pos = footer_end
    return BslModule(text=text, methods=methods)


def method_by_name(module: BslModule, name: str) -> BslMethod | None:
    name_l = name.lower()
    for method in module.methods:
        if method.local_name.lower() == name_l:
            return method
    return None
