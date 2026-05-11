from __future__ import annotations

import xml.etree.ElementTree as ET

from .form_identity import command_interface_item_identity, normalize_xml_fragment
from .form_report import FormMergeStats
from .models import MergeReport
from .xml_utils import child, children, clone_element, local_name


MERGEABLE_PROPERTY_CONTAINERS = {"Visible", "FunctionalOptions"}


def _panel_map(command_interface: ET.Element | None) -> dict[str, ET.Element]:
    if command_interface is None:
        return {}
    return {local_name(item.tag): item for item in children(command_interface)}


def _item_map(panel: ET.Element | None) -> dict[tuple[str, ...], ET.Element]:
    if panel is None:
        return {}
    result: dict[tuple[str, ...], ET.Element] = {}
    panel_name = local_name(panel.tag)
    for item in children(panel, "Item"):
        result[command_interface_item_identity(panel_name, item).parts] = item
    return result


def _property_key(element: ET.Element) -> tuple[str, ...]:
    name = local_name(element.tag)
    if name == "Value":
        return name, element.attrib.get("name", "")
    if name == "Item":
        return name, (element.text or "").strip()
    return (name,)


def _property_map(parent: ET.Element | None) -> dict[tuple[str, ...], ET.Element]:
    if parent is None:
        return {}
    result: dict[tuple[str, ...], ET.Element] = {}
    for item in children(parent):
        result[_property_key(item)] = item
    return result


def _is_mergeable_container(element: ET.Element | None) -> bool:
    return element is not None and local_name(element.tag) in MERGEABLE_PROPERTY_CONTAINERS and bool(children(element))


def _replace_existing(parent: ET.Element, existing: ET.Element, replacement: ET.Element) -> None:
    index = list(parent).index(existing)
    parent.remove(existing)
    parent.insert(index, replacement)


def _conflict(
    report: MergeReport,
    *,
    rel_path: str,
    item_key: tuple[str, ...],
    property_key: tuple[str, ...],
    ancestor: str | None = None,
    current: str | None = None,
    extension: str | None = None,
) -> None:
    command = item_key[1] if len(item_key) > 1 else "/".join(item_key)
    context = {
        "item": "/".join(item_key),
        "property": "/".join(property_key),
    }
    if ancestor is not None:
        context["ancestor"] = ancestor[:4000]
    if current is not None:
        context["current"] = current[:4000]
    if extension is not None:
        context["extension"] = extension[:4000]
    report.add_conflict(
        "FORM_COMMAND_INTERFACE_CONFLICT",
        rel_path,
        f"{'/'.join(item_key)}/{'/'.join(property_key)}",
        object_type="CommandInterface",
        object_name=command,
        context=context,
    )


def _merge_property_children(
    current_parent: ET.Element,
    ancestor_parent: ET.Element | None,
    extension_parent: ET.Element,
    *,
    rel_path: str,
    report: MergeReport,
    stats: FormMergeStats,
    item_key: tuple[str, ...],
) -> None:
    current_props = _property_map(current_parent)
    ancestor_props = _property_map(ancestor_parent)
    extension_props = _property_map(extension_parent)
    property_keys = list(dict.fromkeys([*ancestor_props.keys(), *current_props.keys(), *extension_props.keys()]))

    for property_key in property_keys:
        ancestor_prop = ancestor_props.get(property_key)
        current_prop = current_props.get(property_key)
        extension_prop = extension_props.get(property_key)

        if extension_prop is None:
            if ancestor_prop is None:
                continue
            if current_prop is None:
                continue
            ancestor_sig = normalize_xml_fragment(ancestor_prop)
            current_sig = normalize_xml_fragment(current_prop)
            if current_sig == ancestor_sig:
                current_parent.remove(current_prop)
                stats.command_interface_changed += 1
                continue
            _conflict(
                report,
                rel_path=rel_path,
                item_key=item_key,
                property_key=property_key,
                ancestor=ancestor_sig,
                current=current_sig,
                extension="<removed>",
            )
            continue

        extension_sig = normalize_xml_fragment(extension_prop)
        ancestor_sig = normalize_xml_fragment(ancestor_prop)
        current_sig = normalize_xml_fragment(current_prop)

        if current_sig == extension_sig:
            continue
        if extension_sig == ancestor_sig:
            continue
        if current_prop is None:
            if ancestor_prop is None:
                current_parent.append(clone_element(extension_prop))
                stats.command_interface_changed += 1
                continue
            _conflict(
                report,
                rel_path=rel_path,
                item_key=item_key,
                property_key=property_key,
                ancestor=ancestor_sig,
                current="<removed>",
                extension=extension_sig,
            )
            continue
        if ancestor_prop is not None and current_sig == ancestor_sig:
            _replace_existing(current_parent, current_prop, clone_element(extension_prop))
            stats.command_interface_changed += 1
            continue
        if _is_mergeable_container(current_prop) and _is_mergeable_container(extension_prop):
            _merge_property_children(
                current_prop,
                ancestor_prop,
                extension_prop,
                rel_path=rel_path,
                report=report,
                stats=stats,
                item_key=(*item_key, *property_key),
            )
            continue
        _conflict(
            report,
            rel_path=rel_path,
            item_key=item_key,
            property_key=property_key,
            ancestor=ancestor_sig if ancestor_prop is not None else None,
            current=current_sig,
            extension=extension_sig,
        )


def _merge_item_properties(
    current_item: ET.Element,
    ancestor_item: ET.Element | None,
    extension_item: ET.Element,
    *,
    rel_path: str,
    report: MergeReport,
    stats: FormMergeStats,
    item_key: tuple[str, ...],
) -> None:
    _merge_property_children(
        current_item,
        ancestor_item,
        extension_item,
        rel_path=rel_path,
        report=report,
        stats=stats,
        item_key=item_key,
    )


def merge_command_interface(
    current_root: ET.Element,
    ancestor_root: ET.Element | None,
    extension_root: ET.Element,
    *,
    rel_path: str,
    report: MergeReport,
    stats: FormMergeStats,
) -> None:
    extension_ci = child(extension_root, "CommandInterface")
    if extension_ci is None:
        return
    current_ci = child(current_root, "CommandInterface")
    if current_ci is None:
        current_ci = clone_element(extension_ci)
        current_root.append(current_ci)
        stats.command_interface_added += sum(len(children(panel, "Item")) for panel in children(current_ci))
        return
    ancestor_ci = child(ancestor_root, "CommandInterface") if ancestor_root is not None else None

    current_panels = _panel_map(current_ci)
    ancestor_panels = _panel_map(ancestor_ci)
    extension_panels = _panel_map(extension_ci)

    for panel_name, extension_panel in extension_panels.items():
        current_panel = current_panels.get(panel_name)
        ancestor_panel = ancestor_panels.get(panel_name)
        if current_panel is None:
            current_panel = ET.Element(extension_panel.tag, extension_panel.attrib.copy())
            current_ci.append(current_panel)
            current_panels[panel_name] = current_panel
        current_items = _item_map(current_panel)
        ancestor_items = _item_map(ancestor_panel)
        extension_items = _item_map(extension_panel)

        for key, extension_item in extension_items.items():
            ancestor_item = ancestor_items.get(key)
            current_item = current_items.get(key)
            if ancestor_item is None and current_item is None:
                current_panel.append(clone_element(extension_item))
                stats.command_interface_added += 1
                continue
            if ancestor_item is None and current_item is not None:
                _merge_item_properties(
                    current_item,
                    None,
                    extension_item,
                    rel_path=rel_path,
                    report=report,
                    stats=stats,
                    item_key=key,
                )
                continue
            if current_item is None:
                report.add_conflict(
                    "FORM_COMMAND_INTERFACE_TARGET_NOT_FOUND",
                    rel_path,
                    "/".join(key),
                    object_type="CommandInterface",
                    object_name=key[1],
                )
                continue
            ancestor_sig = normalize_xml_fragment(ancestor_item)
            current_sig = normalize_xml_fragment(current_item)
            extension_sig = normalize_xml_fragment(extension_item)
            if extension_sig == ancestor_sig or current_sig == extension_sig:
                continue
            if current_sig == ancestor_sig:
                index = list(current_panel).index(current_item)
                current_panel.remove(current_item)
                current_panel.insert(index, clone_element(extension_item))
                stats.command_interface_changed += 1
                continue
            _merge_item_properties(
                current_item,
                ancestor_item,
                extension_item,
                rel_path=rel_path,
                report=report,
                stats=stats,
                item_key=key,
            )

        extension_order = list(extension_items)
        current_order = [command_interface_item_identity(panel_name, item).parts for item in children(current_panel, "Item")]
        if current_order != extension_order:
            current_by_key = _item_map(current_panel)
            current_only = [key for key in current_order if key not in extension_items]
            new_order = []
            for key in extension_order:
                item = current_by_key.get(key)
                if item is not None:
                    new_order.append(item)
            for key in current_only:
                item = current_by_key.get(key)
                if item is not None:
                    new_order.append(item)
            for item in list(current_panel):
                current_panel.remove(item)
            for item in new_order:
                current_panel.append(item)
