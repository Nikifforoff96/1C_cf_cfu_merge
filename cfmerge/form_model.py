from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .xml_utils import (
    NS_LF,
    child,
    collect_namespace_declarations,
    parse_xml,
    remove_children_by_local,
    write_xml,
)


@dataclass(slots=True)
class FormModel:
    tree: ET.ElementTree
    path: Path
    root: ET.Element
    base_form: ET.Element | None
    namespaces: dict[str, str]


def load_form_model(path: Path) -> FormModel:
    tree = parse_xml(path)
    root = tree.getroot()
    return FormModel(
        tree=tree,
        path=path,
        root=root,
        base_form=child(root, "BaseForm"),
        namespaces=collect_namespace_declarations(path),
    )


def strip_extension_artifacts(root: ET.Element) -> None:
    remove_children_by_local(root, "BaseForm")
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        element.attrib.pop("callType", None)


def write_form_model(path: Path, tree: ET.ElementTree, namespaces: dict[str, str] | None = None) -> None:
    write_xml(path, tree, default_namespace=NS_LF, extra_namespaces=namespaces or {})
