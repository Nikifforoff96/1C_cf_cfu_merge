from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import re
import xml.etree.ElementTree as ET
from typing import Iterable

from .models import MergeReport
from .xml_utils import child, child_text, children, clone_element, is_adopted, local_name


NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"
XSI_TYPE = f"{{{NS_XSI}}}type"

EXTENSION_ONLY_PROPERTIES = {
    "ObjectBelonging",
    "ExtendedConfigurationObject",
    "ConfigurationExtensionPurpose",
    "NamePrefix",
    "KeepMappingToExtendedConfigurationObjectsByIDs",
}

IDENTITY_PROPERTIES = {
    "Name",
    "UUID",
    "Uuid",
    "InternalInfo",
    "ID",
} | EXTENSION_ONLY_PROPERTIES

UNSAFE_LINKAGE_PROPERTIES: set[str] = set()


@dataclass(frozen=True, slots=True)
class MetadataMergeContext:
    report: MergeReport
    rel_path: str
    object_path: str
    object_type: str

    def nested(self, child_type: str, child_name: str) -> "MetadataMergeContext":
        suffix = f"{child_type}.{child_name}" if child_name else child_type
        return replace(
            self,
            object_path=f"{self.object_path}/{suffix}",
            object_type=child_type,
        )


def _canonical_element(element: ET.Element) -> bytes:
    clone = clone_element(element)

    def normalize(node: ET.Element) -> None:
        if node.text is not None and not node.text.strip():
            node.text = None
        elif node.text is not None:
            node.text = node.text.strip()
        node.tail = None
        for item in list(node):
            if isinstance(item.tag, str):
                normalize(item)

    normalize(clone)
    return ET.tostring(clone, encoding="utf-8", short_empty_elements=True)


def _elements_equal(left: ET.Element, right: ET.Element) -> bool:
    return _canonical_element(left) == _canonical_element(right)


def _is_effectively_empty(element: ET.Element) -> bool:
    return not (element.text or "").strip() and not any(isinstance(item.tag, str) for item in list(element))


def _xsi_type(element: ET.Element) -> str | None:
    value = element.attrib.get(XSI_TYPE)
    if value is not None:
        return value
    for key, attr_value in element.attrib.items():
        if local_name(key) == "type":
            return attr_value
    return None


def _is_readable_extended_property(element: ET.Element) -> bool:
    xsi_type = _xsi_type(element)
    return bool(xsi_type and xsi_type.split(":", 1)[-1] == "ExtendedProperty")


def _xsi_type_local(element: ET.Element) -> str:
    xsi_type = _xsi_type(element)
    return xsi_type.split(":", 1)[-1] if xsi_type else ""


def _without_xsi_type(attrib: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in attrib.items() if not (key == XSI_TYPE or local_name(key) == "type")}


def _readable_property_value(element: ET.Element, name: str) -> ET.Element | None:
    return next((item for item in children(element) if local_name(item.tag) == name), None)


def _looks_like_type_description(element: ET.Element) -> bool:
    return _xsi_type_local(element) == "TypeDescription" or any(local_name(item.tag) == "Type" for item in children(element))


def _unwrap_type_description_extension(element: ET.Element, check_value: ET.Element, extend_value: ET.Element) -> ET.Element:
    unwrapped = ET.Element(element.tag, _without_xsi_type(element.attrib))
    unwrapped.text = check_value.text or extend_value.text
    unwrapped.tail = element.tail

    type_children: list[ET.Element] = []
    type_keys: set[bytes] = set()
    other_children: dict[str, ET.Element] = {}
    other_order: list[str] = []

    def add_other(item: ET.Element, *, replace: bool) -> None:
        key = local_name(item.tag)
        cloned = clone_element(item)
        if key not in other_children:
            other_children[key] = cloned
            other_order.append(key)
            return
        if replace and not _elements_equal(other_children[key], item):
            other_children[key] = cloned

    def add_source(source: ET.Element, *, replace_other: bool) -> None:
        for item in children(source):
            if local_name(item.tag) == "Type":
                key = _canonical_element(item)
                if key not in type_keys:
                    type_keys.add(key)
                    type_children.append(clone_element(item))
                continue
            add_other(item, replace=replace_other)

    add_source(check_value, replace_other=False)
    add_source(extend_value, replace_other=True)

    for item in type_children:
        unwrapped.append(item)
    for key in other_order:
        unwrapped.append(other_children[key])
    return unwrapped


def _unwrap_readable_extended_property(element: ET.Element) -> tuple[ET.Element | None, bool]:
    if not _is_readable_extended_property(element):
        return clone_element(element), False
    check_value = _readable_property_value(element, "CheckValue")
    extend_value = _readable_property_value(element, "ExtendValue")
    if extend_value is None:
        return None, False
    if (
        check_value is not None
        and _looks_like_type_description(check_value)
        and _looks_like_type_description(extend_value)
    ):
        return _unwrap_type_description_extension(element, check_value, extend_value), True
    unwrapped = ET.Element(element.tag, _without_xsi_type(element.attrib) | _without_xsi_type(extend_value.attrib))
    unwrapped.text = extend_value.text
    unwrapped.tail = element.tail
    for item in list(extend_value):
        if isinstance(item.tag, str):
            unwrapped.append(clone_element(item))
    return unwrapped, True


def _sanitize_readable_extended_properties(element: ET.Element) -> tuple[ET.Element | None, bool]:
    sanitized, changed = _unwrap_readable_extended_property(element)
    if sanitized is None:
        return None, False
    for item in list(sanitized):
        if not isinstance(item.tag, str):
            continue
        replacement, child_changed = _sanitize_readable_extended_properties(item)
        if replacement is None:
            return None, changed or child_changed
        if child_changed:
            index = list(sanitized).index(item)
            sanitized.remove(item)
            sanitized.insert(index, replacement)
            changed = True
    return sanitized, changed


def _sanitize_metadata_element_for_plain_result(element: ET.Element) -> ET.Element:
    sanitized, _ = _sanitize_readable_extended_properties(element)
    if sanitized is None:
        sanitized = clone_element(element)

    def remove_extension_properties(node: ET.Element) -> None:
        if local_name(node.tag) == "Properties":
            for item in list(node):
                if isinstance(item.tag, str) and local_name(item.tag) in EXTENSION_ONLY_PROPERTIES:
                    node.remove(item)
        for item in list(node):
            if isinstance(item.tag, str):
                remove_extension_properties(item)

    remove_extension_properties(sanitized)
    return sanitized


def _value_summary(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    data = _canonical_element(element)
    text = re.sub(rb"\s+", b" ", data).decode("utf-8", errors="replace").strip()
    if len(text) <= 160:
        return text
    digest = hashlib.sha1(data).hexdigest()[:12]
    return f"{text[:120]}... sha1:{digest}"


def _append_metadata_action(
    context: MetadataMergeContext,
    action: str,
    *,
    property_path: str | None = None,
    old_element: ET.Element | None = None,
    new_element: ET.Element | None = None,
    reason: str,
) -> None:
    context.report.add_metadata_action(
        object_path=context.object_path,
        object_type=context.object_type,
        property_path=property_path,
        action=action,
        old_value=_value_summary(old_element),
        new_value=_value_summary(new_element),
        reason=reason,
        source_path=context.rel_path,
    )


def extract_properties(element: ET.Element) -> ET.Element | None:
    return child(element, "Properties")


def _property_map(properties: ET.Element, context: MetadataMergeContext, side: str) -> dict[str, ET.Element]:
    result: dict[str, ET.Element] = {}
    duplicates: set[str] = set()
    for item in children(properties):
        key = local_name(item.tag)
        if key in result:
            duplicates.add(key)
            continue
        result[key] = item
    for key in sorted(duplicates):
        context.report.add_warning(
            "METADATA_PROPERTY_DUPLICATE_KEY",
            context.rel_path,
            f"{context.object_path}: duplicate {side} property {key}; property was not merged",
        )
        _append_metadata_action(
            context,
            "conflict",
            property_path=key,
            reason=f"duplicate_{side}_property_key",
        )
    return {key: value for key, value in result.items() if key not in duplicates}


def _insert_property(base_properties: ET.Element, extension_property: ET.Element) -> None:
    base_properties.append(clone_element(extension_property))


def _replace_property(base_properties: ET.Element, base_property: ET.Element, extension_property: ET.Element) -> ET.Element:
    index = list(base_properties).index(base_property)
    cloned = clone_element(extension_property)
    base_properties.remove(base_property)
    base_properties.insert(index, cloned)
    return cloned


def _register_record_key(item: ET.Element) -> str:
    return (item.text or "").strip()


def _register_record_map(items: list[ET.Element], context: MetadataMergeContext, side: str) -> dict[str, ET.Element]:
    result: dict[str, ET.Element] = {}
    duplicates: set[str] = set()
    for item in items:
        key = _register_record_key(item)
        if not key:
            _append_metadata_action(
                context,
                "conflict",
                property_path="RegisterRecords",
                new_element=item,
                reason=f"{side}_register_record_key_unknown",
            )
            continue
        if key in result:
            duplicates.add(key)
            continue
        result[key] = item
    for key in sorted(duplicates):
        _append_metadata_action(
            context,
            "conflict",
            property_path="RegisterRecords",
            reason=f"duplicate_{side}_register_record:{key}",
        )
    return {key: value for key, value in result.items() if key not in duplicates}


def _insert_register_records_property(base_properties: ET.Element, extension_property: ET.Element) -> ET.Element:
    cloned = clone_element(extension_property)
    for item in list(cloned):
        cloned.remove(item)
    base_properties.append(cloned)
    return cloned


def merge_register_records(
    base_properties: ET.Element,
    base_property: ET.Element | None,
    extension_property: ET.Element,
    context: MetadataMergeContext,
) -> bool:
    extension_items = children(extension_property, "Item")
    if not extension_items:
        return False

    if base_property is None:
        base_property = _insert_register_records_property(base_properties, extension_property)

    base_records = _register_record_map(children(base_property, "Item"), context, "base")
    extension_records = _register_record_map(extension_items, context, "extension")
    changed = False

    for key, extension_item in extension_records.items():
        if key in base_records:
            continue
        cloned = clone_element(extension_item)
        base_property.append(cloned)
        base_records[key] = cloned
        changed = True
        _append_metadata_action(
            context,
            "register_record_added",
            property_path="RegisterRecords",
            new_element=cloned,
            reason="extension_register_record_absent_in_base",
        )

    if changed:
        _append_metadata_action(
            context,
            "register_records_merged",
            property_path="RegisterRecords",
            reason="document_register_records_semantic_merge",
        )
    return changed


def merge_properties(base_element: ET.Element, extension_element: ET.Element, context: MetadataMergeContext) -> bool:
    extension_properties = extract_properties(extension_element)
    if extension_properties is None:
        return False
    base_properties = extract_properties(base_element)
    if base_properties is None:
        base_properties = ET.Element(extension_properties.tag)
        base_element.insert(0, base_properties)

    base_by_name = _property_map(base_properties, context, "base")
    extension_by_name = _property_map(extension_properties, context, "extension")
    changed = False

    for prop_name, extension_property in extension_by_name.items():
        if prop_name in EXTENSION_ONLY_PROPERTIES:
            continue
        plain_extension_property, unwrapped = _sanitize_readable_extended_properties(extension_property)
        if plain_extension_property is None:
            context.report.add_warning(
                "METADATA_PROPERTY_EXTENDED_VALUE_NOT_FOUND",
                context.rel_path,
                f"{context.object_path}: property {prop_name} has xr:ExtendedProperty without xr:ExtendValue",
            )
            _append_metadata_action(
                context,
                "conflict",
                property_path=prop_name,
                new_element=extension_property,
                reason="readable_extended_property_without_extend_value",
            )
            continue
        reason = "extension_property_differs"
        if unwrapped:
            reason = "extension_property_differs;readable_extended_property_unwrapped"
        base_property = base_by_name.get(prop_name)
        if context.object_type == "Document" and prop_name == "RegisterRecords":
            if merge_register_records(base_properties, base_property, plain_extension_property, context):
                base_by_name[prop_name] = child(base_properties, "RegisterRecords")
                changed = True
            continue
        if (
            prop_name == "Type"
            and base_property is not None
            and not unwrapped
            and is_adopted(extension_element)
            and not _elements_equal(base_property, plain_extension_property)
        ):
            reason = "adopted_plain_type_ignored"
            if _is_effectively_empty(plain_extension_property) and not _is_effectively_empty(base_property):
                reason = "empty_extension_type_ignored"
            _append_metadata_action(
                context,
                "property_preserved",
                property_path=prop_name,
                old_element=base_property,
                new_element=plain_extension_property,
                reason=reason,
            )
            continue
        if prop_name in UNSAFE_LINKAGE_PROPERTIES:
            if base_property is not None and not _elements_equal(base_property, plain_extension_property):
                context.report.add_warning(
                    "METADATA_PROPERTY_REQUIRES_SPECIAL_MERGE",
                    context.rel_path,
                    f"{context.object_path}: property {prop_name} was not merged by generic metadata property merge",
                )
                _append_metadata_action(
                    context,
                    "conflict",
                    property_path=prop_name,
                    old_element=base_property,
                    new_element=plain_extension_property,
                    reason="unsafe_linkage_property_not_merged",
                )
            continue
        if prop_name in IDENTITY_PROPERTIES:
            if base_property is not None and not _elements_equal(base_property, plain_extension_property):
                _append_metadata_action(
                    context,
                    "conflict",
                    property_path=prop_name,
                    old_element=base_property,
                    new_element=plain_extension_property,
                    reason="identity_property_not_merged",
                )
            continue
        if base_property is None:
            _insert_property(base_properties, plain_extension_property)
            base_by_name[prop_name] = children(base_properties)[-1]
            changed = True
            _append_metadata_action(
                context,
                "property_added",
                property_path=prop_name,
                new_element=plain_extension_property,
                reason="extension_property_absent_in_base" + (";readable_extended_property_unwrapped" if unwrapped else ""),
            )
            continue
        if _elements_equal(base_property, plain_extension_property):
            continue
        base_by_name[prop_name] = _replace_property(base_properties, base_property, plain_extension_property)
        changed = True
        _append_metadata_action(
            context,
            "property_replaced",
            property_path=prop_name,
            old_element=base_property,
            new_element=plain_extension_property,
            reason=reason,
        )

    return changed


def element_key(element: ET.Element, context: MetadataMergeContext | None = None) -> tuple[str, str] | None:
    name = child_text(element, ["Properties", "Name"])
    source = "properties_name"
    if not name:
        name = element.attrib.get("name")
        source = "attribute_name"
    if not name and local_name(element.tag) == "AdditionalColumns":
        name = element.attrib.get("table")
        source = "attribute_table"
    if not name:
        text = (element.text or "").strip()
        if text:
            name = text
            source = "text"
    if not name:
        name = element.attrib.get("id", "")
        source = "attribute_id"
    if not name:
        if context is not None:
            context.report.add_warning(
                "METADATA_CHILD_KEY_UNKNOWN",
                context.rel_path,
                f"{context.object_path}: cannot determine stable key for child {local_name(element.tag)}",
            )
            _append_metadata_action(
                context,
                "conflict",
                reason="child_key_unknown",
            )
        return None
    return local_name(element.tag), name


def _child_key_map(
    items: Iterable[ET.Element],
    context: MetadataMergeContext,
    side: str,
) -> dict[tuple[str, str], ET.Element]:
    result: dict[tuple[str, str], ET.Element] = {}
    duplicates: set[tuple[str, str]] = set()
    for item in items:
        key = element_key(item, context)
        if key is None:
            continue
        if key in result:
            duplicates.add(key)
            continue
        result[key] = item
    for key in sorted(duplicates):
        context.report.add_warning(
            "METADATA_CHILD_DUPLICATE_KEY",
            context.rel_path,
            f"{context.object_path}: duplicate {side} child key {key[0]}.{key[1]}; child was not merged",
        )
        _append_metadata_action(
            context,
            "conflict",
            reason=f"duplicate_{side}_child_key:{key[0]}.{key[1]}",
        )
    return {key: value for key, value in result.items() if key not in duplicates}


def _insert_child(base_child_objects: ET.Element, extension_child: ET.Element) -> ET.Element:
    cloned = _sanitize_metadata_element_for_plain_result(extension_child)
    base_child_objects.append(cloned)
    return cloned


def merge_child_objects(base_element: ET.Element, extension_element: ET.Element, context: MetadataMergeContext) -> bool:
    extension_child_objects = child(extension_element, "ChildObjects")
    if extension_child_objects is None:
        return False
    extension_children = children(extension_child_objects)
    if not extension_children:
        return False

    base_child_objects = child(base_element, "ChildObjects")
    if base_child_objects is None:
        base_child_objects = ET.Element(extension_child_objects.tag)
        base_element.append(base_child_objects)

    base_by_key = _child_key_map(children(base_child_objects), context, "base")
    changed = False

    for extension_child in extension_children:
        key = element_key(extension_child, context)
        if key is None:
            continue
        base_child = base_by_key.get(key)
        child_context = context.nested(key[0], key[1])
        if base_child is None:
            if is_adopted(extension_child):
                context.report.add_warning(
                    "ADOPTED_CHILD_NOT_IN_BASE",
                    context.rel_path,
                    f"{context.object_path}: adopted child {key[0]}.{key[1]} not found in base",
                )
                _append_metadata_action(
                    child_context,
                    "conflict",
                    reason="adopted_child_not_found_in_base",
                )
                continue
            inserted = _insert_child(base_child_objects, extension_child)
            base_by_key[key] = inserted
            changed = True
            _append_metadata_action(
                child_context,
                "child_object_added",
                new_element=inserted,
                reason="native_extension_child_absent_in_base",
            )
            continue

        child_changed = merge_metadata_element(base_child, extension_child, child_context)
        if child_changed:
            changed = True
            _append_metadata_action(
                child_context,
                "child_object_merged",
                old_element=None,
                new_element=None,
                reason="existing_child_properties_or_children_merged",
            )

    return changed


def merge_metadata_element(base_element: ET.Element, extension_element: ET.Element, context: MetadataMergeContext) -> bool:
    changed = False
    if merge_properties(base_element, extension_element, context):
        changed = True
    if merge_child_objects(base_element, extension_element, context):
        changed = True
    return changed
