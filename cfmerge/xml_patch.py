from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .io_utils import detect_encoding_and_newline, read_text, write_text
from .xml_utils import child, children, local_name


PathKey = tuple[tuple[str, str], ...]


@dataclass(slots=True)
class XmlSpan:
    path: PathKey
    local: str
    key: str
    start: int
    open_end: int
    end: int | None = None
    close_start: int | None = None
    self_closing: bool = False
    children: list["XmlSpan"] = field(default_factory=list)


TAG_RE = re.compile(r"<[^>]+>")
ATTR_RE = re.compile(r"\s([A-Za-z_][A-Za-z0-9_.:-]*)=\"([^\"]*)\"")


def _tag_name(tag: str) -> str:
    tag = tag.strip()
    if tag.startswith("</"):
        tag = tag[2:]
    elif tag.startswith("<"):
        tag = tag[1:]
    tag = tag.strip()
    name = tag.split(None, 1)[0].rstrip("/>")
    return name.split(":", 1)[-1]


def _attrs(tag: str) -> dict[str, str]:
    return {m.group(1).split(":", 1)[-1]: m.group(2) for m in ATTR_RE.finditer(tag)}


def _node_key(local: str, attrs: dict[str, str]) -> tuple[str, str]:
    if local == "Event":
        return local, attrs.get("name", "")
    if local == "Action":
        return local, "Action"
    if local == "AdditionalColumns":
        return local, attrs.get("table", "")
    return local, attrs.get("name", "")


def parse_spans(text: str) -> list[XmlSpan]:
    roots: list[XmlSpan] = []
    stack: list[XmlSpan] = []
    for match in TAG_RE.finditer(text):
        tag = match.group(0)
        if tag.startswith("<?") or tag.startswith("<!--") or tag.startswith("<!"):
            continue
        if tag.startswith("</"):
            local = _tag_name(tag)
            while stack:
                node = stack.pop()
                node.close_start = match.start()
                node.end = match.end()
                if node.local == local:
                    break
            continue
        local = _tag_name(tag)
        attrs = _attrs(tag)
        key = _node_key(local, attrs)
        path = (stack[-1].path if stack else tuple()) + (key,)
        node = XmlSpan(
            path=path,
            local=local,
            key=key[1],
            start=match.start(),
            open_end=match.end(),
            self_closing=tag.rstrip().endswith("/>"),
        )
        if stack:
            stack[-1].children.append(node)
        else:
            roots.append(node)
        if node.self_closing:
            node.end = match.end()
            node.close_start = match.start()
        else:
            stack.append(node)
    return roots


def flatten_spans(nodes: list[XmlSpan]) -> list[XmlSpan]:
    result: list[XmlSpan] = []
    for node in nodes:
        result.append(node)
        result.extend(flatten_spans(node.children))
    return result


def span_map(text: str) -> dict[PathKey, XmlSpan]:
    result: dict[PathKey, XmlSpan] = {}
    for node in flatten_spans(parse_spans(text)):
        result.setdefault(node.path, node)
    return result


def _et_key(element: ET.Element) -> tuple[str, str]:
    local = local_name(element.tag)
    if local == "Event":
        return local, element.attrib.get("name", "")
    if local == "Action":
        return local, "Action"
    if local == "AdditionalColumns":
        return local, element.attrib.get("table", "")
    return local, element.attrib.get("name", "")


def parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    return {child_elem: parent for parent in root.iter() for child_elem in list(parent)}


def et_path(element: ET.Element, parents: dict[ET.Element, ET.Element]) -> PathKey:
    parts: list[tuple[str, str]] = []
    cur: ET.Element | None = element
    while cur is not None:
        parts.append(_et_key(cur))
        cur = parents.get(cur)
    return tuple(reversed(parts))


def strip_call_type(text: str) -> str:
    return re.sub(r'\s+callType="[^"]+"', "", text)


def remove_base_form(text: str) -> str:
    m = re.search(r"(?ms)^\t<BaseForm\b.*?^\t</BaseForm>\r?\n?", text)
    if not m:
        return text
    return text[:m.start()] + text[m.end():]


def replace_span_text(text: str, span: XmlSpan, replacement: str) -> str:
    end = span.end if span.end is not None else span.open_end
    return text[:span.start] + replacement + text[end:]


def replace_element_inner_text(text: str, span: XmlSpan, new_inner: str) -> str:
    if span.self_closing or span.close_start is None:
        replacement = text[span.start:span.open_end].rstrip("/>") + f">{new_inner}</{span.local}>"
        return replace_span_text(text, span, replacement)
    return text[:span.open_end] + new_inner + text[span.close_start:]


def insert_before_close(text: str, container: XmlSpan, snippet: str) -> str:
    if container.close_start is None:
        return text
    prefix = "\r\n" if "\r\n" in text else "\n"
    line_start = text.rfind("\n", 0, container.close_start)
    line_start = 0 if line_start < 0 else line_start + 1
    container_indent = re.match(r"[ \t]*", text[line_start:container.close_start]).group(0)
    child_indent = container_indent + "\t"
    insert = snippet
    if insert and not insert.startswith((" ", "\t", "\r", "\n")):
        insert = child_indent + insert
    if not insert.startswith(("\r", "\n")):
        insert = prefix + insert
    if not insert.endswith(("\r", "\n")):
        insert += prefix
    if container.self_closing:
        original = text[container.start:container.open_end]
        tag_name_match = re.match(r"<\s*([^\s/>]+)", original)
        if not tag_name_match:
            return text
        tag_name = tag_name_match.group(1)
        open_tag = re.sub(r"\s*/>\s*$", ">", original)
        replacement = open_tag + insert + container_indent + f"</{tag_name}>"
        return text[:container.start] + replacement + text[container.open_end:]
    return text[:container.close_start] + insert + text[container.close_start:]


def insert_root_events_block(text: str, event_snippet: str) -> str:
    nl = "\r\n" if "\r\n" in text else "\n"
    if event_snippet and not event_snippet.startswith((" ", "\t", "\r", "\n")):
        event_snippet = "\t\t" + event_snippet
    block = f"\t<Events>{nl}{event_snippet.rstrip()}{nl}\t</Events>{nl}"
    m = re.search(r"(?m)^\t<(ChildItems|Attributes|Commands)\b", text)
    if m:
        return text[:m.start()] + block + text[m.start():]
    root_close = re.search(r"(?m)^</Form>", text)
    if root_close:
        return text[:root_close.start()] + block + text[root_close.start():]
    return text + nl + block


def root_container_path(name: str) -> PathKey:
    return (("Form", ""), (name, ""))


def serialize_et_element_from_source(source_text: str, element_path: PathKey) -> str | None:
    spans = span_map(source_text)
    span = spans.get(element_path)
    if not span or span.end is None:
        return None
    return source_text[span.start:span.end]


def container_immediate_child_snippets(source_text: str, container_path: PathKey) -> dict[tuple[str, str], str]:
    spans = span_map(source_text)
    container = spans.get(container_path)
    if not container:
        return {}
    result: dict[tuple[str, str], str] = {}
    for child_span in container.children:
        if child_span.end is None:
            continue
        result[(child_span.local, child_span.key)] = source_text[child_span.start:child_span.end]
    return result


def write_patched_like_source(path: Path, source_path: Path, text: str) -> None:
    encoding, newline = detect_encoding_and_newline(source_path)
    write_text(path, text, encoding=encoding, newline=newline)
