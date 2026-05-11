from __future__ import annotations

import xml.etree.ElementTree as ET

from .form_id_allocator import FormIdAllocator
from .form_identity import FormIdentity, child_item_identity, normalize_xml_fragment
from .form_report import FormMergeStats
from .models import MergeReport
from .xml_utils import child, children, clone_element, local_name


XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"


def _property_children(element: ET.Element, skip: set[str]) -> dict[str, ET.Element]:
    result: dict[str, ET.Element] = {}
    for item in children(element):
        name = local_name(item.tag)
        if name in skip:
            continue
        result[name] = item
    return result


def _replace_child(parent: ET.Element, local: str, replacement: ET.Element | None) -> None:
    existing = child(parent, local)
    index = None
    if existing is not None:
        index = list(parent).index(existing)
        parent.remove(existing)
    if replacement is None:
        return
    if index is None:
        parent.append(replacement)
    else:
        parent.insert(index, replacement)


def _is_complex_property(element: ET.Element | None) -> bool:
    if element is None:
        return False
    return any(isinstance(item.tag, str) for item in list(element))


def _is_explicit_removal(element: ET.Element | None) -> bool:
    if element is None:
        return False
    if element.attrib.get(XSI_NIL, "").lower() == "true":
        return True
    if element.attrib.get("cfmerge-remove", "").lower() == "true":
        return True
    return False


def _remove_property(
    owner: ET.Element,
    prop_name: str,
    prop: ET.Element | None,
    *,
    allocator: FormIdAllocator,
    id_domain: str,
) -> None:
    if prop is not None and _is_complex_property(prop):
        allocator.release_subtree(prop, id_domain)
    _replace_child(owner, prop_name, None)


def _reorder_named_children(container: ET.Element, desired_keys: list[FormIdentity]) -> None:
    current_items = children(container)
    current_by_key = {child_item_identity(item): item for item in current_items}
    new_items = [current_by_key[key] for key in desired_keys if key in current_by_key]
    for item in current_items:
        if item not in new_items:
            new_items.append(item)
    for item in list(container):
        container.remove(item)
    for item in new_items:
        container.append(item)


def _merge_context_menu_property(
    current_prop: ET.Element,
    ancestor_prop: ET.Element | None,
    extension_prop: ET.Element,
    *,
    owner_key: FormIdentity | None,
    rel_path: str,
    report: MergeReport,
    stats: FormMergeStats,
    allocator: FormIdAllocator,
    id_domain: str,
) -> None:
    merge_properties(
        current_prop,
        ancestor_prop,
        extension_prop,
        owner_key=owner_key,
        rel_path=rel_path,
        report=report,
        stats=stats,
        allocator=allocator,
        id_domain=id_domain,
        skip={"ChildItems"},
    )
    extension_children = child(extension_prop, "ChildItems")
    if extension_children is None:
        return
    current_children = child(current_prop, "ChildItems")
    if current_children is None:
        current_prop.append(clone_element(extension_children))
        allocator.allocate_subtree(current_prop, id_domain)
        stats.properties_changed += 1
        return
    ancestor_children = child(ancestor_prop, "ChildItems") if ancestor_prop is not None else None
    current_items = {child_item_identity(item): item for item in children(current_children)}
    ancestor_items = {child_item_identity(item): item for item in children(ancestor_children)} if ancestor_children is not None else {}
    extension_items = {child_item_identity(item): item for item in children(extension_children)}
    for key, extension_item in extension_items.items():
        ancestor_item = ancestor_items.get(key)
        current_item = current_items.get(key)
        if ancestor_item is None and current_item is None:
            clone = clone_element(extension_item)
            allocator.allocate_subtree(clone, id_domain)
            current_children.append(clone)
            current_items[key] = clone
            stats.properties_changed += 1
            continue
        if current_item is None:
            report.add_conflict(
                "FORM_PROPERTY_CONFLICT",
                rel_path,
                f"{owner_key.render() if owner_key else 'form'}/ContextMenu/{key.render()}",
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
            skip={"ChildItems", "Events", "Action"},
        )
    _reorder_named_children(current_children, list(extension_items))


def merge_properties(
    current: ET.Element,
    ancestor: ET.Element | None,
    extension: ET.Element,
    *,
    owner_key: FormIdentity | None,
    rel_path: str,
    report: MergeReport,
    stats: FormMergeStats,
    allocator: FormIdAllocator,
    id_domain: str,
    skip: set[str],
) -> None:
    ancestor_props = _property_children(ancestor, skip) if ancestor is not None else {}
    current_props = _property_children(current, skip)
    extension_props = _property_children(extension, skip)
    property_names = sorted(set(ancestor_props) | set(current_props) | set(extension_props))
    owner_label = owner_key.render() if owner_key is not None else "form"

    for prop_name in property_names:
        ancestor_prop = ancestor_props.get(prop_name)
        current_prop = current_props.get(prop_name)
        extension_prop = extension_props.get(prop_name)

        if extension_prop is None and ancestor_prop is None:
            continue

        ancestor_sig = normalize_xml_fragment(ancestor_prop)
        current_sig = normalize_xml_fragment(current_prop)
        extension_sig = normalize_xml_fragment(extension_prop)

        extension_removed = False
        if ancestor_prop is not None:
            extension_removed = _is_explicit_removal(extension_prop)
            if extension_prop is None and not _is_complex_property(ancestor_prop):
                extension_removed = True
        if extension_removed:
            if current_prop is None:
                continue
            if _is_explicit_removal(current_prop):
                _remove_property(current, prop_name, current_prop, allocator=allocator, id_domain=id_domain)
                stats.properties_changed += 1
                continue
            if current_sig == ancestor_sig:
                _remove_property(current, prop_name, current_prop, allocator=allocator, id_domain=id_domain)
                stats.properties_changed += 1
                report.objects["modified"].append({
                    "type": "FormProperty",
                    "name": prop_name,
                    "path": rel_path,
                    "strategy": "form_property_remove_extension",
                    "owner": owner_label,
                })
                continue
            report.add_conflict(
                "FORM_PROPERTY_CONFLICT",
                rel_path,
                f"{owner_label}/{prop_name}",
                object_type="FormProperty",
                object_name=prop_name,
                context={
                    "owner": owner_label,
                    "property": prop_name,
                    "ancestor": ancestor_sig[:4000],
                    "current": current_sig[:4000],
                    "extension": "<removed>",
                },
            )
            continue

        if current_sig == extension_sig:
            continue
        if extension_sig == ancestor_sig:
            continue
        if current_sig == ancestor_sig:
            if _is_explicit_removal(extension_prop):
                _remove_property(current, prop_name, current_prop, allocator=allocator, id_domain=id_domain)
                stats.properties_changed += 1
                continue
            if prop_name == "ContextMenu" and current_prop is not None:
                _merge_context_menu_property(
                    current_prop,
                    ancestor_prop,
                    extension_prop,
                    owner_key=owner_key,
                    rel_path=rel_path,
                    report=report,
                    stats=stats,
                    allocator=allocator,
                    id_domain=id_domain,
                )
                continue
            cloned = clone_element(extension_prop)
            if current_prop is not None and _is_complex_property(current_prop):
                allocator.release_subtree(current_prop, id_domain)
            if _is_complex_property(cloned):
                allocator.allocate_subtree(cloned, id_domain)
            _replace_child(current, prop_name, cloned)
            stats.properties_changed += 1
            report.objects["modified"].append({
                "type": "FormProperty",
                "name": prop_name,
                "path": rel_path,
                "strategy": "form_property_apply_extension",
                "owner": owner_label,
            })
            continue

        if extension_prop is None:
            if _is_explicit_removal(ancestor_prop):
                _remove_property(current, prop_name, current_prop, allocator=allocator, id_domain=id_domain)
                stats.properties_changed += 1
            continue

        if current_prop is None and ancestor_prop is None:
            cloned = clone_element(extension_prop)
            if _is_complex_property(cloned):
                allocator.allocate_subtree(cloned, id_domain)
            _replace_child(current, prop_name, cloned)
            stats.properties_changed += 1
            continue

        if prop_name == "ContextMenu" and current_prop is not None and extension_prop is not None:
            _merge_context_menu_property(
                current_prop,
                ancestor_prop,
                extension_prop,
                owner_key=owner_key,
                rel_path=rel_path,
                report=report,
                stats=stats,
                allocator=allocator,
                id_domain=id_domain,
            )
            continue

        if current_sig == extension_sig:
            continue

        report.add_conflict(
            "FORM_PROPERTY_CONFLICT",
            rel_path,
            f"{owner_label}/{prop_name}",
            object_type="FormProperty",
            object_name=prop_name,
            context={
                "owner": owner_label,
                "property": prop_name,
                "ancestor": ancestor_sig[:4000],
                "current": current_sig[:4000],
                "extension": extension_sig[:4000],
            },
        )
