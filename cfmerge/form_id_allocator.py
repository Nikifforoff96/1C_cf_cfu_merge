from __future__ import annotations

import xml.etree.ElementTree as ET

from .xml_utils import child, local_name


def _iter_domain_elements(root: ET.Element, domain: str) -> list[ET.Element]:
    if domain == "child_item":
        container = child(root, "ChildItems")
        if container is None:
            return []
        return [item for item in container.iter() if isinstance(item.tag, str) and "id" in item.attrib]
    if domain == "attribute":
        container = child(root, "Attributes")
        if container is None:
            return []
        result: list[ET.Element] = []
        for item in list(container):
            if not isinstance(item.tag, str):
                continue
            if local_name(item.tag) == "ConditionalAppearance":
                continue
            for nested in item.iter():
                if isinstance(nested.tag, str) and "id" in nested.attrib:
                    result.append(nested)
        return result
    if domain == "command":
        container = child(root, "Commands")
        if container is None:
            return []
        result: list[ET.Element] = []
        for item in list(container):
            if not isinstance(item.tag, str):
                continue
            for nested in item.iter():
                if isinstance(nested.tag, str) and "id" in nested.attrib:
                    result.append(nested)
        return result
    if domain == "parameter":
        container = child(root, "Parameters")
        if container is None:
            return []
        return [item for item in container.iter() if isinstance(item.tag, str) and "id" in item.attrib]
    return []


class FormIdAllocator:
    def __init__(self, root: ET.Element):
        self.used: dict[str, set[int]] = {}
        for domain in ("child_item", "attribute", "command", "parameter"):
            values: set[int] = set()
            for element in _iter_domain_elements(root, domain):
                try:
                    values.add(int(element.attrib["id"]))
                except (KeyError, ValueError):
                    continue
            self.used[domain] = values

    def release_subtree(self, subtree: ET.Element | None, domain: str) -> None:
        if subtree is None:
            return
        values = self.used.setdefault(domain, set())
        for element in subtree.iter():
            if not isinstance(element.tag, str):
                continue
            raw = element.attrib.get("id")
            if not raw:
                continue
            try:
                values.discard(int(raw))
            except ValueError:
                continue

    def reserve_subtree(self, subtree: ET.Element | None, domain: str) -> None:
        if subtree is None:
            return
        values = self.used.setdefault(domain, set())
        for element in subtree.iter():
            if not isinstance(element.tag, str):
                continue
            raw = element.attrib.get("id")
            if not raw:
                continue
            try:
                values.add(int(raw))
            except ValueError:
                continue

    def allocate_subtree(self, subtree: ET.Element, domain: str) -> dict[int, int]:
        values = self.used.setdefault(domain, set())
        remap: dict[int, int] = {}
        for element in subtree.iter():
            if not isinstance(element.tag, str):
                continue
            raw = element.attrib.get("id")
            if not raw:
                continue
            try:
                desired = int(raw)
            except ValueError:
                continue
            if desired not in values:
                values.add(desired)
                continue
            new_value = max(values, default=0) + 1
            values.add(new_value)
            remap[desired] = new_value
            element.attrib["id"] = str(new_value)
        return remap
