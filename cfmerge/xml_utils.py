from __future__ import annotations

import copy
import hashlib
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .io_utils import write_text


NS_MD = "http://v8.1c.ru/8.3/MDClasses"
NS_LF = "http://v8.1c.ru/8.3/xcf/logform"
NS_DUMP = "http://v8.1c.ru/8.3/xcf/dumpinfo"

NAMESPACES = {
    "": NS_MD,
    "app": "http://v8.1c.ru/8.2/managed-application/core",
    "cfg": "http://v8.1c.ru/8.1/data/enterprise/current-config",
    "cmi": "http://v8.1c.ru/8.2/managed-application/cmi",
    "dcscor": "http://v8.1c.ru/8.1/data-composition-system/core",
    "dcssch": "http://v8.1c.ru/8.1/data-composition-system/schema",
    "dcsset": "http://v8.1c.ru/8.1/data-composition-system/settings",
    "ent": "http://v8.1c.ru/8.1/data/enterprise",
    "lf": "http://v8.1c.ru/8.2/managed-application/logform",
    "style": "http://v8.1c.ru/8.1/data/ui/style",
    "sys": "http://v8.1c.ru/8.1/data/ui/fonts/system",
    "v8": "http://v8.1c.ru/8.1/data/core",
    "v8ui": "http://v8.1c.ru/8.1/data/ui",
    "web": "http://v8.1c.ru/8.1/data/ui/colors/web",
    "win": "http://v8.1c.ru/8.1/data/ui/colors/windows",
    "xen": "http://v8.1c.ru/8.3/xcf/enums",
    "xpr": "http://v8.1c.ru/8.3/xcf/predef",
    "xr": "http://v8.1c.ru/8.3/xcf/readable",
    "xs": "http://www.w3.org/2001/XMLSchema",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}

for _prefix, _uri in NAMESPACES.items():
    try:
        ET.register_namespace(_prefix, _uri)
    except ValueError:
        pass
ET.register_namespace("", NS_LF)


def q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if tag.startswith("{") else tag


def namespace(tag: str) -> str:
    if tag.startswith("{"):
        return tag[1:].split("}", 1)[0]
    return ""


def parse_xml(path: Path) -> ET.ElementTree:
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    return ET.parse(path, parser=parser)


def clone_element(element: ET.Element) -> ET.Element:
    return copy.deepcopy(element)


def child(element: ET.Element, local: str) -> ET.Element | None:
    for item in list(element):
        if local_name(item.tag) == local:
            return item
    return None


def children(element: ET.Element, local: str | None = None) -> list[ET.Element]:
    return [item for item in list(element) if isinstance(item.tag, str) and (local is None or local_name(item.tag) == local)]


def child_text(element: ET.Element, path: list[str]) -> str | None:
    cur: ET.Element | None = element
    for part in path:
        if cur is None:
            return None
        cur = child(cur, part)
    if cur is None:
        return None
    return cur.text or ""


def is_adopted(element: ET.Element) -> bool:
    return child_text(element, ["Properties", "ObjectBelonging"]) == "Adopted"


def object_name(element: ET.Element) -> str | None:
    return child_text(element, ["Properties", "Name"])


def element_key(element: ET.Element) -> tuple[str, str]:
    name = element.attrib.get("name")
    if not name and local_name(element.tag) == "AdditionalColumns":
        name = element.attrib.get("table")
    if not name:
        name = child_text(element, ["Properties", "Name"])
    if not name:
        text = (element.text or "").strip()
        name = text
    if not name:
        name = element.attrib.get("id", "")
    return local_name(element.tag), name


def element_signature(element: ET.Element) -> str:
    data = ET.tostring(element, encoding="utf-8")
    data = re.sub(rb"\s+", b" ", data)
    return hashlib.sha1(data).hexdigest()


def indent_xml(element: ET.Element, level: int = 0) -> None:
    indent = "\n" + level * "\t"
    child_indent = "\n" + (level + 1) * "\t"
    elems = [e for e in list(element) if isinstance(e.tag, str)]
    if elems:
        if not element.text or not element.text.strip():
            element.text = child_indent
        for idx, item in enumerate(elems):
            indent_xml(item, level + 1)
            if not item.tail or not item.tail.strip():
                item.tail = child_indent if idx < len(elems) - 1 else indent
    else:
        if not element.text:
            element.text = element.text
    if level and (not element.tail or not element.tail.strip()):
        element.tail = indent


def collect_namespace_declarations(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
    except Exception:
        return {}
    result: dict[str, str] = {}
    for match in re.finditer(r'xmlns:([A-Za-z_][A-Za-z0-9_.-]*)="([^"]+)"', text):
        result.setdefault(match.group(1), match.group(2))
    return result


def _inject_namespace_declarations(text: str, default_namespace: str | None, extra_namespaces: dict[str, str] | None = None) -> str:
    start_end = text.find(">")
    if start_end < 0:
        return text
    start = text[:start_end]
    additions: list[str] = []
    declared = set(re.findall(r"\sxmlns:([A-Za-z_][A-Za-z0-9_.-]*)=", start))
    if default_namespace and " xmlns=" not in start:
        additions.append(f'xmlns="{default_namespace}"')
    for prefix, uri in NAMESPACES.items():
        if not prefix:
            continue
        marker = f"xmlns:{prefix}="
        if marker not in start and prefix not in declared:
            additions.append(f'xmlns:{prefix}="{uri}"')
            declared.add(prefix)
    for prefix, uri in (extra_namespaces or {}).items():
        marker = f"xmlns:{prefix}="
        if marker not in start and prefix not in declared:
            additions.append(f'xmlns:{prefix}="{uri}"')
            declared.add(prefix)
    if not additions:
        return text
    return text[:start_end] + " " + " ".join(additions) + text[start_end:]


def xml_to_text(tree: ET.ElementTree, default_namespace: str | None = None, extra_namespaces: dict[str, str] | None = None) -> str:
    root = tree.getroot()
    indent_xml(root, 0)
    if default_namespace:
        ET.register_namespace("", default_namespace)
    text = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    text = _inject_namespace_declarations(text, default_namespace, extra_namespaces)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + text + "\n"


def write_xml(path: Path, tree: ET.ElementTree, default_namespace: str | None = None, extra_namespaces: dict[str, str] | None = None) -> None:
    write_text(path, xml_to_text(tree, default_namespace, extra_namespaces), encoding="utf-8-sig", newline="crlf")


def remove_children_by_local(element: ET.Element, local: str) -> None:
    for item in list(element):
        if isinstance(item.tag, str) and local_name(item.tag) == local:
            element.remove(item)
