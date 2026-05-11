from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .form_identity import FormIdentity, child_item_identity
from .xml_utils import child, children


ParentKey = FormIdentity | None


@dataclass(slots=True)
class IndexedChildNode:
    key: FormIdentity
    element: ET.Element
    parent_key: ParentKey
    index: int


@dataclass(slots=True)
class AddNode:
    key: FormIdentity
    parent_key: ParentKey
    index: int
    source: ET.Element


@dataclass(slots=True)
class MoveNode:
    key: FormIdentity
    parent_key: ParentKey
    index: int


@dataclass(slots=True)
class ReorderChildren:
    parent_key: ParentKey
    order: list[FormIdentity]


@dataclass(slots=True)
class ChildTreeIndex:
    nodes: dict[FormIdentity, IndexedChildNode] = field(default_factory=dict)
    children_by_parent: dict[ParentKey, list[FormIdentity]] = field(default_factory=dict)


@dataclass(slots=True)
class ChildTreeDelta:
    add_nodes: list[AddNode] = field(default_factory=list)
    move_nodes: list[MoveNode] = field(default_factory=list)
    reorders: list[ReorderChildren] = field(default_factory=list)
    added_keys: set[FormIdentity] = field(default_factory=set)


def index_child_items(root: ET.Element) -> ChildTreeIndex:
    result = ChildTreeIndex()

    def walk(owner: ET.Element, parent_key: ParentKey) -> None:
        container = child(owner, "ChildItems")
        if container is None:
            return
        keys: list[FormIdentity] = []
        for index, item in enumerate(children(container)):
            key = child_item_identity(item)
            result.nodes[key] = IndexedChildNode(key=key, element=item, parent_key=parent_key, index=index)
            keys.append(key)
            walk(item, key)
        result.children_by_parent[parent_key] = keys

    walk(root, None)
    return result


def diff_child_tree(ancestor_root: ET.Element | None, extension_root: ET.Element) -> ChildTreeDelta:
    ancestor_index = index_child_items(ancestor_root) if ancestor_root is not None else ChildTreeIndex()
    extension_index = index_child_items(extension_root)
    delta = ChildTreeDelta()
    delta.added_keys = set(extension_index.nodes) - set(ancestor_index.nodes)

    for key, ext_node in extension_index.nodes.items():
        ancestor_node = ancestor_index.nodes.get(key)
        if ancestor_node is None:
            delta.add_nodes.append(AddNode(key=key, parent_key=ext_node.parent_key, index=ext_node.index, source=ext_node.element))
            continue
        if ext_node.parent_key != ancestor_node.parent_key:
            delta.move_nodes.append(MoveNode(key=key, parent_key=ext_node.parent_key, index=ext_node.index))

    for parent_key, order in extension_index.children_by_parent.items():
        delta.reorders.append(ReorderChildren(parent_key=parent_key, order=list(order)))
    return delta
