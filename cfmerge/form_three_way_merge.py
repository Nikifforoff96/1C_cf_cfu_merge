from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .bsl_merge import EventHook
from .form_command_interface import merge_command_interface
from .form_conditional_appearance import merge_conditional_appearance
from .form_diff import ChildTreeDelta, ParentKey, diff_child_tree, index_child_items
from .form_events import ModuleMethodIndex, merge_events_for_owner, module_method_index
from .form_id_allocator import FormIdAllocator
from .form_identity import FormIdentity, attribute_identity, child_item_identity, command_identity, parameter_identity
from .form_properties import merge_properties
from .form_report import FormMergeStats
from .models import MergeReport
from .xml_utils import child, children, clone_element, local_name, namespace


@dataclass(slots=True)
class FormMergeResult:
    root: ET.Element
    hooks: list[EventHook] = field(default_factory=list)
    stats: FormMergeStats = field(default_factory=FormMergeStats)


def _pruned_added_subtree(element: ET.Element, added_keys: set[FormIdentity]) -> ET.Element:
    cloned = clone_element(element)

    def prune(ext_source: ET.Element, dst: ET.Element) -> None:
        ext_container = child(ext_source, "ChildItems")
        dst_container = child(dst, "ChildItems")
        if ext_container is None or dst_container is None:
            return
        ext_children = children(ext_container)
        dst_children = children(dst_container)
        for ext_child, dst_child in zip(ext_children, dst_children):
            key = child_item_identity(ext_child)
            if key not in added_keys:
                dst_container.remove(dst_child)
                continue
            prune(ext_child, dst_child)

    prune(element, cloned)
    return cloned


def _child_owner_by_key(root: ET.Element) -> dict[FormIdentity | None, ET.Element]:
    result: dict[FormIdentity | None, ET.Element] = {None: root}

    def walk(owner: ET.Element) -> None:
        container = child(owner, "ChildItems")
        if container is None:
            return
        for item in children(container):
            key = child_item_identity(item)
            result[key] = item
            walk(item)

    walk(root)
    return result


def _reorder_children(current_root: ET.Element, order_by_parent: dict[ParentKey, list[FormIdentity]]) -> None:
    owners = _child_owner_by_key(current_root)
    for parent_key, desired_order in order_by_parent.items():
        owner = owners.get(parent_key)
        if owner is None:
            continue
        container = child(owner, "ChildItems")
        if container is None:
            continue
        current_items = children(container)
        current_by_key = {child_item_identity(item): item for item in current_items}
        current_keys = [child_item_identity(item) for item in current_items]
        pending_current_only = [key for key in current_keys if key not in desired_order]
        new_items: list[ET.Element] = []
        current_only_index = 0
        for key in desired_order:
            if key in current_by_key:
                while current_only_index < len(pending_current_only):
                    probe = pending_current_only[current_only_index]
                    if probe in desired_order:
                        current_only_index += 1
                        continue
                    break
                new_items.append(current_by_key[key])
        for key in pending_current_only:
            item = current_by_key.get(key)
            if item is not None and item not in new_items:
                new_items.append(item)
        if len(new_items) != len(current_items):
            for item in current_items:
                if item not in new_items:
                    new_items.append(item)
        for item in list(container):
            container.remove(item)
        for item in new_items:
            container.append(item)


def _column_collection_item_identity(owner_name: str, element: ET.Element) -> FormIdentity:
    item_type = local_name(element.tag)
    if item_type == "AdditionalColumns":
        return FormIdentity("additional_columns", (owner_name, element.attrib.get("table", "")))
    return FormIdentity("column", (owner_name, element.attrib.get("name", "")))


def _reorder_collection(
    container: ET.Element,
    *,
    key_func,
    current_order: list[FormIdentity],
    extension_order: list[FormIdentity],
) -> None:
    if current_order == extension_order:
        return
    current_by_key = {key_func(item): item for item in children(container)}
    new_items = [current_by_key[key] for key in extension_order if key in current_by_key]
    for key in current_order:
        item = current_by_key.get(key)
        if item is not None and item not in new_items:
            new_items.append(item)
    for item in list(container):
        container.remove(item)
    for item in new_items:
        container.append(item)


def _merge_column_collection(
    current_container: ET.Element,
    ancestor_container: ET.Element | None,
    extension_container: ET.Element,
    *,
    owner_name: str,
    rel_path: str,
    report: MergeReport,
    stats: FormMergeStats,
    allocator: FormIdAllocator,
) -> None:
    key_func = lambda item, owner=owner_name: _column_collection_item_identity(owner, item)
    current_items = {key_func(item): item for item in children(current_container)}
    ancestor_items = {key_func(item): item for item in children(ancestor_container)} if ancestor_container is not None else {}
    extension_items = {key_func(item): item for item in children(extension_container)}
    current_order = [key_func(item) for item in children(current_container)]
    extension_order = [key_func(item) for item in children(extension_container)]

    for key, extension_item in extension_items.items():
        ancestor_item = ancestor_items.get(key)
        current_item = current_items.get(key)
        if ancestor_item is None and current_item is None:
            clone = clone_element(extension_item)
            allocator.allocate_subtree(clone, "attribute")
            current_container.append(clone)
            current_items[key] = clone
            stats.properties_changed += 1
            continue
        if ancestor_item is not None and current_item is None:
            report.add_conflict(
                "FORM_COLLECTION_TARGET_NOT_FOUND",
                rel_path,
                f"Columns/{key.render()}",
                object_type="column",
                object_name=key.render(),
            )
            continue
        if current_item is None:
            continue
        if local_name(extension_item.tag) == "AdditionalColumns":
            _merge_column_collection(
                current_item,
                ancestor_item,
                extension_item,
                owner_name=extension_item.attrib.get("table", owner_name),
                rel_path=rel_path,
                report=report,
                stats=stats,
                allocator=allocator,
            )
            continue
        merge_properties(
            current_item,
            ancestor_item,
            extension_item,
            owner_key=key,
            rel_path=rel_path,
            report=report,
            stats=stats,
            allocator=allocator,
            id_domain="attribute",
            skip=set(),
        )

    _reorder_collection(
        current_container,
        key_func=key_func,
        current_order=current_order,
        extension_order=extension_order,
    )


def _merge_attribute_columns(
    current_attribute: ET.Element,
    ancestor_attribute: ET.Element | None,
    extension_attribute: ET.Element,
    *,
    rel_path: str,
    report: MergeReport,
    stats: FormMergeStats,
    allocator: FormIdAllocator,
) -> None:
    extension_columns = child(extension_attribute, "Columns")
    if extension_columns is None:
        return
    current_columns = child(current_attribute, "Columns")
    if current_columns is None:
        clone = clone_element(extension_columns)
        allocator.allocate_subtree(clone, "attribute")
        current_attribute.append(clone)
        stats.properties_changed += 1
        return
    ancestor_columns = child(ancestor_attribute, "Columns") if ancestor_attribute is not None else None
    _merge_column_collection(
        current_columns,
        ancestor_columns,
        extension_columns,
        owner_name=current_attribute.attrib.get("name", ""),
        rel_path=rel_path,
        report=report,
        stats=stats,
        allocator=allocator,
    )


def _merge_keyed_collection(
    current_owner: ET.Element,
    ancestor_owner: ET.Element | None,
    extension_owner: ET.Element,
    *,
    container_name: str,
    key_func,
    rel_path: str,
    report: MergeReport,
    stats: FormMergeStats,
    allocator: FormIdAllocator,
    id_domain: str,
    methods: ModuleMethodIndex,
    hooks: list[EventHook],
    item_type: str,
) -> None:
    extension_container = child(extension_owner, container_name)
    if extension_container is None:
        return
    current_container = child(current_owner, container_name)
    if current_container is None:
        cloned_container = clone_element(extension_container)
        current_owner.append(cloned_container)
        if container_name == "Commands":
            for command in children(cloned_container, "Command"):
                merge_events_for_owner(
                    command,
                    None,
                    command,
                    owner_key=command_identity(command),
                    rel_path=rel_path,
                    report=report,
                    stats=stats,
                    hooks=hooks,
                    methods=methods,
                )
        return
    ancestor_container = child(ancestor_owner, container_name) if ancestor_owner is not None else None
    current_items = {key_func(item): item for item in children(current_container)}
    ancestor_items = {key_func(item): item for item in children(ancestor_container)} if ancestor_container is not None else {}
    extension_items = {key_func(item): item for item in children(extension_container)}
    current_order = [key_func(item) for item in children(current_container)]
    extension_order = [key_func(item) for item in children(extension_container)]

    for key, extension_item in extension_items.items():
        ancestor_item = ancestor_items.get(key)
        current_item = current_items.get(key)
        if ancestor_item is None and current_item is None:
            clone = clone_element(extension_item)
            allocator.allocate_subtree(clone, id_domain)
            current_container.append(clone)
            current_items[key] = clone
            if item_type == "attribute":
                stats.elements_added += 1
            continue
        if ancestor_item is not None and current_item is None:
            report.add_conflict(
                "FORM_COLLECTION_TARGET_NOT_FOUND",
                rel_path,
                f"{container_name}/{key.render()}",
                object_type=item_type,
                object_name=key.render(),
            )
            continue
        if current_item is None:
            continue
        if container_name == "Commands":
            merge_properties(
                current_item,
                ancestor_item,
                extension_item,
                owner_key=key,
                rel_path=rel_path,
                report=report,
                stats=stats,
                allocator=allocator,
                id_domain=id_domain,
                skip={"Events", "Action"},
            )
            merge_events_for_owner(
                current_item,
                ancestor_item,
                extension_item,
                owner_key=key,
                rel_path=rel_path,
                report=report,
                stats=stats,
                hooks=hooks,
                methods=methods,
            )
            continue
        if container_name == "Attributes":
            merge_properties(
                current_item,
                ancestor_item,
                extension_item,
                owner_key=key,
                rel_path=rel_path,
                report=report,
                stats=stats,
                allocator=allocator,
                id_domain=id_domain,
                skip={"Columns", "ConditionalAppearance"},
            )
            _merge_attribute_columns(
                current_item,
                ancestor_item,
                extension_item,
                rel_path=rel_path,
                report=report,
                stats=stats,
                allocator=allocator,
            )
            continue
        merge_properties(
            current_item,
            ancestor_item,
            extension_item,
            owner_key=key,
            rel_path=rel_path,
            report=report,
            stats=stats,
            allocator=allocator,
            id_domain=id_domain,
            skip=set(),
        )

    _reorder_collection(
        current_container,
        key_func=key_func,
        current_order=current_order,
        extension_order=extension_order,
    )


def _merge_child_items(
    current_root: ET.Element,
    ancestor_root: ET.Element | None,
    extension_root: ET.Element,
    *,
    rel_path: str,
    report: MergeReport,
    stats: FormMergeStats,
    allocator: FormIdAllocator,
    hooks: list[EventHook],
    methods: ModuleMethodIndex,
) -> None:
    delta = diff_child_tree(ancestor_root, extension_root)
    current_index = index_child_items(current_root)
    current_owners = _child_owner_by_key(current_root)
    ancestor_index = index_child_items(ancestor_root) if ancestor_root is not None else None
    ancestor_nodes = ancestor_index.nodes if ancestor_index is not None else {}
    extension_index = index_child_items(extension_root)
    ext_order_by_parent = {item.parent_key: item.order for item in delta.reorders}

    for add in delta.add_nodes:
        if add.parent_key in delta.added_keys:
            continue
        owner = current_owners.get(add.parent_key)
        if owner is None:
            report.add_conflict("FORM_MOVE_TARGET_NOT_FOUND", rel_path, add.key.render())
            continue
        container = child(owner, "ChildItems")
        if container is None:
            owner_ns = namespace(owner.tag)
            container = ET.Element(f"{{{owner_ns}}}ChildItems" if owner_ns else "ChildItems")
            owner.append(container)
        clone = _pruned_added_subtree(add.source, delta.added_keys)
        allocator.allocate_subtree(clone, "child_item")
        container.append(clone)
        stats.elements_added += 1
        current_owners = _child_owner_by_key(current_root)

    for move in delta.move_nodes:
        node = current_owners.get(move.key)
        target_owner = current_owners.get(move.parent_key)
        if node is None:
            report.add_conflict("FORM_NODE_MISSING_IN_CURRENT", rel_path, move.key.render())
            continue
        if target_owner is None:
            report.add_conflict("FORM_MOVE_TARGET_NOT_FOUND", rel_path, move.key.render())
            continue
        current_parent = None
        for owner in current_owners.values():
            container = child(owner, "ChildItems")
            if container is not None and node in list(container):
                current_parent = container
                break
        target_container = child(target_owner, "ChildItems")
        if target_container is None:
            owner_ns = namespace(target_owner.tag)
            target_container = ET.Element(f"{{{owner_ns}}}ChildItems" if owner_ns else "ChildItems")
            target_owner.append(target_container)
        if current_parent is not None and current_parent is not target_container:
            current_parent.remove(node)
            target_container.append(node)
            stats.elements_moved += 1
        current_owners = _child_owner_by_key(current_root)

    current_owners = _child_owner_by_key(current_root)
    for key, ext_node in extension_index.nodes.items():
        current_node = current_owners.get(key)
        if current_node is None:
            continue
        ancestor_node = ancestor_nodes.get(key).element if key in ancestor_nodes else None
        merge_properties(
            current_node,
            ancestor_node,
            ext_node.element,
            owner_key=key,
            rel_path=rel_path,
            report=report,
            stats=stats,
            allocator=allocator,
            id_domain="child_item",
            skip={"ChildItems", "Events", "Action"},
        )
        merge_events_for_owner(
            current_node,
            ancestor_node,
            ext_node.element,
            owner_key=key,
            rel_path=rel_path,
            report=report,
            stats=stats,
            hooks=hooks,
            methods=methods,
        )

    _reorder_children(current_root, ext_order_by_parent)


def merge_form_tree(
    current_root: ET.Element,
    ancestor_root: ET.Element | None,
    extension_root: ET.Element,
    *,
    rel_path: str,
    report: MergeReport,
    extension_module_text: str | None,
) -> FormMergeResult:
    hooks: list[EventHook] = []
    stats = FormMergeStats()
    allocator = FormIdAllocator(current_root)
    methods = module_method_index(extension_module_text)

    merge_properties(
        current_root,
        ancestor_root,
        extension_root,
        owner_key=None,
        rel_path=rel_path,
        report=report,
        stats=stats,
        allocator=allocator,
        id_domain="child_item",
        skip={"BaseForm", "Events", "ChildItems", "Attributes", "Commands", "Parameters", "CommandInterface"},
    )
    merge_events_for_owner(
        current_root,
        ancestor_root,
        extension_root,
        owner_key=None,
        rel_path=rel_path,
        report=report,
        stats=stats,
        hooks=hooks,
        methods=methods,
    )
    _merge_child_items(
        current_root,
        ancestor_root,
        extension_root,
        rel_path=rel_path,
        report=report,
        stats=stats,
        allocator=allocator,
        hooks=hooks,
        methods=methods,
    )
    _merge_keyed_collection(
        current_root,
        ancestor_root,
        extension_root,
        container_name="Attributes",
        key_func=attribute_identity,
        rel_path=rel_path,
        report=report,
        stats=stats,
        allocator=allocator,
        id_domain="attribute",
        methods=methods,
        hooks=hooks,
        item_type="attribute",
    )
    merge_conditional_appearance(
        current_root,
        ancestor_root,
        extension_root,
        rel_path=rel_path,
        report=report,
        stats=stats,
    )
    _merge_keyed_collection(
        current_root,
        ancestor_root,
        extension_root,
        container_name="Commands",
        key_func=command_identity,
        rel_path=rel_path,
        report=report,
        stats=stats,
        allocator=allocator,
        id_domain="command",
        methods=methods,
        hooks=hooks,
        item_type="command",
    )
    _merge_keyed_collection(
        current_root,
        ancestor_root,
        extension_root,
        container_name="Parameters",
        key_func=parameter_identity,
        rel_path=rel_path,
        report=report,
        stats=stats,
        allocator=allocator,
        id_domain="parameter",
        methods=methods,
        hooks=hooks,
        item_type="parameter",
    )
    merge_command_interface(
        current_root,
        ancestor_root,
        extension_root,
        rel_path=rel_path,
        report=report,
        stats=stats,
    )
    return FormMergeResult(root=current_root, hooks=hooks, stats=stats)
