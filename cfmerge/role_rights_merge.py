from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .io_utils import copy_file, write_text
from .models import MergeReport
from .xml_utils import (
    child,
    child_text,
    children,
    clone_element,
    collect_namespace_declarations,
    indent_xml,
    local_name,
    parse_xml,
)


NS_ROLES = "http://v8.1c.ru/8.2/roles"

TOP_LEVEL_FLAGS = (
    "setForNewObjects",
    "setForAttributesByDefault",
    "independentRightsOfChildObjects",
)
SUPPORTED_TOP_LEVEL = set(TOP_LEVEL_FLAGS) | {"object", "restrictionTemplate"}


@dataclass(frozen=True, slots=True)
class RoleRightsMergeResult:
    changed: bool
    strategy: str


def _role_name_from_rel(rel_path: str) -> str:
    parts = rel_path.replace("\\", "/").split("/")
    return parts[1] if len(parts) >= 2 else rel_path


def _rights_root_path(rel_path: str) -> str:
    return f"Role.{_role_name_from_rel(rel_path)}/Rights"


def _canonical_text(value: str | None) -> str:
    return (value or "").strip()


def _canonical_object_name(name: str | None, base_config_name: str | None, ext_config_name: str | None) -> str:
    value = _canonical_text(name)
    if base_config_name and ext_config_name and value == f"Configuration.{ext_config_name}":
        return f"Configuration.{base_config_name}"
    return value


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


def _value_summary(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    data = _canonical_element(element)
    text = re.sub(rb"\s+", b" ", data).decode("utf-8", errors="replace").strip()
    if len(text) <= 160:
        return text
    return f"sha1:{hashlib.sha1(data).hexdigest()[:12]}"


def _text_summary(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if len(value) <= 160:
        return value
    return f"{value[:120]}... sha1:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:12]}"


def _digest(element: ET.Element) -> str:
    return hashlib.sha1(_canonical_element(element)).hexdigest()[:12]


def _record(
    report: MergeReport,
    rel_path: str,
    *,
    object_path: str,
    property_path: str | None,
    action: str,
    old_value: str | None = None,
    new_value: str | None = None,
    old_element: ET.Element | None = None,
    new_element: ET.Element | None = None,
    reason: str,
) -> None:
    report.add_metadata_action(
        object_path=object_path,
        object_type="RightsXml",
        property_path=property_path,
        action=action,
        old_value=old_value if old_element is None else _value_summary(old_element),
        new_value=new_value if new_element is None else _value_summary(new_element),
        reason=reason,
        source_path=rel_path,
    )


def _record_conflict(
    report: MergeReport,
    rel_path: str,
    *,
    object_path: str,
    property_path: str | None,
    reason: str,
    details: str,
) -> None:
    report.add_warning("RIGHTS_XML_MERGE_CONFLICT", rel_path, details)
    _record(
        report,
        rel_path,
        object_path=object_path,
        property_path=property_path,
        action="conflict",
        reason=reason,
    )


def _record_unsupported_top_level(report: MergeReport, rel_path: str, element: ET.Element) -> None:
    name = local_name(element.tag)
    report.add_warning(
        "UNSUPPORTED_RIGHTS_XML_ELEMENT",
        rel_path,
        f"Unsupported top-level Rights.xml element was not merged: {name}",
    )
    _record(
        report,
        rel_path,
        object_path=_rights_root_path(rel_path),
        property_path=name,
        action="unsupported_rights_xml_element",
        new_element=element,
        reason="unsupported_top_level_rights_xml_element_not_merged",
    )


def _register_namespaces(extra_namespaces: dict[str, str]) -> None:
    ET.register_namespace("", NS_ROLES)
    for prefix, uri in extra_namespaces.items():
        try:
            ET.register_namespace(prefix, uri)
        except ValueError:
            pass


def _inject_namespace_declarations(text: str, extra_namespaces: dict[str, str]) -> str:
    start_end = text.find(">")
    if start_end < 0:
        return text
    start = text[:start_end]
    additions: list[str] = []
    if " xmlns=" not in start:
        additions.append(f'xmlns="{NS_ROLES}"')
    declared = set(re.findall(r"\sxmlns:([A-Za-z_][A-Za-z0-9_.-]*)=", start))
    for prefix, uri in extra_namespaces.items():
        if not prefix or prefix in declared or f"xmlns:{prefix}=" in start:
            continue
        additions.append(f'xmlns:{prefix}="{uri}"')
        declared.add(prefix)
    if not additions:
        return text
    return text[:start_end] + " " + " ".join(additions) + text[start_end:]


def _write_rights_xml(out_path: Path, tree: ET.ElementTree, base_path: Path, ext_path: Path) -> None:
    extra_ns = collect_namespace_declarations(base_path)
    extra_ns.update(collect_namespace_declarations(ext_path))
    _register_namespaces(extra_ns)
    indent_xml(tree.getroot(), 0)
    text = ET.tostring(tree.getroot(), encoding="unicode", short_empty_elements=True)
    text = _inject_namespace_declarations(text, extra_ns)
    write_text(out_path, '<?xml version="1.0" encoding="UTF-8"?>\n' + text + "\n", encoding="utf-8-sig", newline="crlf")


def _replace_all_text(value: str | None, old_ref: str, new_ref: str) -> tuple[str | None, bool]:
    if value is None or old_ref not in value:
        return value, False
    return value.replace(old_ref, new_ref), True


def _rebase_configuration_refs(
    element: ET.Element,
    base_config_name: str | None,
    ext_config_name: str | None,
) -> tuple[ET.Element, list[tuple[str, str]]]:
    cloned = clone_element(element)
    if not base_config_name or not ext_config_name or base_config_name == ext_config_name:
        return cloned, []

    old_ref = f"Configuration.{ext_config_name}"
    new_ref = f"Configuration.{base_config_name}"
    changes: list[tuple[str, str]] = []

    def visit(node: ET.Element) -> None:
        new_text, changed = _replace_all_text(node.text, old_ref, new_ref)
        if changed:
            changes.append((node.text or "", new_text or ""))
            node.text = new_text
        for key, value in list(node.attrib.items()):
            new_value, attr_changed = _replace_all_text(value, old_ref, new_ref)
            if attr_changed and new_value is not None:
                changes.append((value, new_value))
                node.attrib[key] = new_value
        for item in list(node):
            if isinstance(item.tag, str):
                visit(item)

    visit(cloned)
    return cloned, changes


def _record_rebase_changes(
    report: MergeReport,
    rel_path: str,
    object_name: str | None,
    changes: list[tuple[str, str]],
) -> None:
    if not changes:
        return
    old_value, new_value = changes[0]
    object_path = _rights_root_path(rel_path)
    if object_name:
        object_path += f"/{object_name}"
    _record(
        report,
        rel_path,
        object_path=object_path,
        property_path="Configuration",
        action="rights_xml_rebased",
        old_value=_text_summary(old_value),
        new_value=_text_summary(new_value),
        reason="rights_xml_configuration_reference_rebased",
    )


def _top_level_elements(root: ET.Element, local: str) -> list[ET.Element]:
    return [item for item in children(root) if local_name(item.tag) == local]


def _first_top_level(root: ET.Element, local: str) -> ET.Element | None:
    for item in children(root):
        if local_name(item.tag) == local:
            return item
    return None


def _find_insert_index(root: ET.Element) -> int:
    for index, item in enumerate(list(root)):
        if isinstance(item.tag, str) and local_name(item.tag) in {"object", "restrictionTemplate"}:
            return index
    return len(list(root))


def _insert_or_replace_flag(
    base_root: ET.Element,
    ext_flag: ET.Element,
    report: MergeReport,
    rel_path: str,
) -> bool:
    flag_name = local_name(ext_flag.tag)
    base_flag = _first_top_level(base_root, flag_name)
    old_value = _canonical_text(base_flag.text if base_flag is not None else None)
    new_value = _canonical_text(ext_flag.text)
    if base_flag is not None and old_value == new_value and _elements_equal(base_flag, ext_flag):
        return False

    if base_flag is None:
        base_root.insert(_find_insert_index(base_root), clone_element(ext_flag))
    else:
        index = list(base_root).index(base_flag)
        base_root.remove(base_flag)
        base_root.insert(index, clone_element(ext_flag))

    _record(
        report,
        rel_path,
        object_path=_rights_root_path(rel_path),
        property_path=flag_name,
        action="role_rights_flag_replaced",
        old_value=old_value if base_flag is not None else None,
        new_value=new_value,
        reason="extension_top_level_flag_differs",
    )
    return True


def _object_key(
    element: ET.Element,
    base_config_name: str | None,
    ext_config_name: str | None,
) -> str:
    return _canonical_object_name(child_text(element, ["name"]), base_config_name, ext_config_name)


def _right_key(element: ET.Element) -> str:
    return _canonical_text(child_text(element, ["name"]))


def _restriction_template_key(element: ET.Element) -> str:
    name = _canonical_text(child_text(element, ["name"]))
    return name or f"digest:{_digest(element)}"


def _object_path(rel_path: str, object_name: str) -> str:
    return f"{_rights_root_path(rel_path)}/{object_name}"


def _map_unique(
    items: list[ET.Element],
    key_func,
    report: MergeReport,
    rel_path: str,
    *,
    side: str,
    kind: str,
    parent_object_path: str,
) -> dict[str, ET.Element]:
    result: dict[str, ET.Element] = {}
    duplicates: set[str] = set()
    for item in items:
        key = key_func(item)
        if not key:
            _record_conflict(
                report,
                rel_path,
                object_path=parent_object_path,
                property_path=None,
                reason=f"{kind}_key_unknown",
                details=f"Cannot determine {kind} key in {side} Rights.xml",
            )
            continue
        if key in result:
            duplicates.add(key)
            continue
        result[key] = item

    for key in sorted(duplicates):
        _record_conflict(
            report,
            rel_path,
            object_path=parent_object_path if kind != "object" else _object_path(rel_path, key),
            property_path=key if kind != "object" else None,
            reason=f"duplicate_{side}_{kind}_key",
            details=f"Duplicate {side} {kind} key in Rights.xml: {key}",
        )

    return {key: value for key, value in result.items() if key not in duplicates}


def _replace_element(parent: ET.Element, old: ET.Element, new: ET.Element) -> ET.Element:
    index = list(parent).index(old)
    parent.remove(old)
    cloned = clone_element(new)
    parent.insert(index, cloned)
    return cloned


def _merge_rights_for_object(
    base_object: ET.Element,
    ext_object: ET.Element,
    object_name: str,
    report: MergeReport,
    rel_path: str,
    base_config_name: str | None,
    ext_config_name: str | None,
) -> bool:
    object_path = _object_path(rel_path, object_name)
    base_rights = _map_unique(
        children(base_object, "right"),
        _right_key,
        report,
        rel_path,
        side="base",
        kind="right",
        parent_object_path=object_path,
    )
    ext_rights = _map_unique(
        children(ext_object, "right"),
        _right_key,
        report,
        rel_path,
        side="extension",
        kind="right",
        parent_object_path=object_path,
    )
    changed = False

    for right_name, ext_right in ext_rights.items():
        rebased_ext_right, changes = _rebase_configuration_refs(ext_right, base_config_name, ext_config_name)
        _record_rebase_changes(report, rel_path, object_name, changes)
        base_right = base_rights.get(right_name)
        if base_right is None:
            inserted = clone_element(rebased_ext_right)
            base_object.append(inserted)
            base_rights[right_name] = inserted
            changed = True
            _record(
                report,
                rel_path,
                object_path=object_path,
                property_path=right_name,
                action="role_right_added",
                new_element=inserted,
                reason="extension_right_absent_in_base",
            )
            continue
        if _elements_equal(base_right, rebased_ext_right):
            continue
        replaced = _replace_element(base_object, base_right, rebased_ext_right)
        base_rights[right_name] = replaced
        changed = True
        _record(
            report,
            rel_path,
            object_path=object_path,
            property_path=right_name,
            action="role_right_replaced",
            old_element=base_right,
            new_element=replaced,
            reason="extension_right_differs",
        )

    if changed:
        _record(
            report,
            rel_path,
            object_path=object_path,
            property_path=None,
            action="rights_object_merged",
            reason="existing_rights_object_merged",
        )
    return changed


def _merge_objects(
    base_root: ET.Element,
    ext_root: ET.Element,
    report: MergeReport,
    rel_path: str,
    base_config_name: str | None,
    ext_config_name: str | None,
) -> bool:
    base_objects = _map_unique(
        _top_level_elements(base_root, "object"),
        lambda element: _object_key(element, base_config_name, ext_config_name),
        report,
        rel_path,
        side="base",
        kind="object",
        parent_object_path=_rights_root_path(rel_path),
    )
    ext_objects = _map_unique(
        _top_level_elements(ext_root, "object"),
        lambda element: _object_key(element, base_config_name, ext_config_name),
        report,
        rel_path,
        side="extension",
        kind="object",
        parent_object_path=_rights_root_path(rel_path),
    )
    changed = False

    for object_name, ext_object in ext_objects.items():
        rebased_ext_object, changes = _rebase_configuration_refs(ext_object, base_config_name, ext_config_name)
        _record_rebase_changes(report, rel_path, object_name, changes)
        base_object = base_objects.get(object_name)
        if base_object is None:
            inserted = clone_element(rebased_ext_object)
            base_root.append(inserted)
            base_objects[object_name] = inserted
            changed = True
            _record(
                report,
                rel_path,
                object_path=_object_path(rel_path, object_name),
                property_path=None,
                action="rights_object_added",
                new_element=inserted,
                reason="extension_rights_object_absent_in_base",
            )
            continue
        if _merge_rights_for_object(
            base_object,
            rebased_ext_object,
            object_name,
            report,
            rel_path,
            base_config_name,
            ext_config_name,
        ):
            changed = True

    return changed


def _merge_restriction_templates(
    base_root: ET.Element,
    ext_root: ET.Element,
    report: MergeReport,
    rel_path: str,
    base_config_name: str | None,
    ext_config_name: str | None,
) -> bool:
    base_templates = _map_unique(
        _top_level_elements(base_root, "restrictionTemplate"),
        _restriction_template_key,
        report,
        rel_path,
        side="base",
        kind="restrictionTemplate",
        parent_object_path=f"{_rights_root_path(rel_path)}/restrictionTemplate",
    )
    ext_templates = _map_unique(
        _top_level_elements(ext_root, "restrictionTemplate"),
        _restriction_template_key,
        report,
        rel_path,
        side="extension",
        kind="restrictionTemplate",
        parent_object_path=f"{_rights_root_path(rel_path)}/restrictionTemplate",
    )
    changed = False

    for key, ext_template in ext_templates.items():
        rebased_ext_template, changes = _rebase_configuration_refs(ext_template, base_config_name, ext_config_name)
        _record_rebase_changes(report, rel_path, f"restrictionTemplate/{key}", changes)
        base_template = base_templates.get(key)
        template_path = f"{_rights_root_path(rel_path)}/restrictionTemplate/{key}"
        if base_template is None:
            inserted = clone_element(rebased_ext_template)
            base_root.append(inserted)
            base_templates[key] = inserted
            changed = True
            _record(
                report,
                rel_path,
                object_path=template_path,
                property_path=key,
                action="restriction_template_added",
                new_element=inserted,
                reason="extension_restriction_template_absent_in_base",
            )
            continue
        if _elements_equal(base_template, rebased_ext_template):
            continue
        replaced = _replace_element(base_root, base_template, rebased_ext_template)
        base_templates[key] = replaced
        changed = True
        _record(
            report,
            rel_path,
            object_path=template_path,
            property_path=key,
            action="restriction_template_replaced",
            old_element=base_template,
            new_element=replaced,
            reason="extension_restriction_template_differs",
        )

    return changed


def _record_unsupported_extension_top_level(ext_root: ET.Element, report: MergeReport, rel_path: str) -> None:
    for item in children(ext_root):
        if local_name(item.tag) not in SUPPORTED_TOP_LEVEL:
            _record_unsupported_top_level(report, rel_path, item)


def _extension_root_reference_left(path: Path, ext_config_name: str | None) -> bool:
    if not ext_config_name:
        return False
    return f"Configuration.{ext_config_name}" in path.read_text(encoding="utf-8-sig", errors="ignore")


def copy_role_rights(
    src: Path,
    dst: Path,
    rel_path: str,
    report: MergeReport,
    base_config_name: str | None,
    ext_config_name: str | None,
) -> RoleRightsMergeResult:
    if not base_config_name or not ext_config_name or base_config_name == ext_config_name:
        copy_file(src, dst)
        return RoleRightsMergeResult(changed=True, strategy="copy_native_resource")

    tree = parse_xml(src)
    rebased_root, changes = _rebase_configuration_refs(tree.getroot(), base_config_name, ext_config_name)
    if not changes:
        copy_file(src, dst)
        return RoleRightsMergeResult(changed=True, strategy="copy_native_resource")

    result_tree = ET.ElementTree(rebased_root)
    _record_rebase_changes(report, rel_path, None, changes)
    _write_rights_xml(dst, result_tree, src, src)
    if _extension_root_reference_left(dst, ext_config_name):
        _record_conflict(
            report,
            rel_path,
            object_path=_rights_root_path(rel_path),
            property_path="Configuration",
            reason="rights_xml_extension_configuration_reference_left",
            details=f"Rights.xml still contains Configuration.{ext_config_name} after rebase",
        )
    return RoleRightsMergeResult(changed=True, strategy="copy_native_resource_rebased")


def merge_role_rights(
    base_path: Path,
    ext_path: Path,
    out_path: Path,
    rel_path: str,
    report: MergeReport,
    base_config_name: str | None,
    ext_config_name: str | None,
) -> RoleRightsMergeResult:
    base_tree = parse_xml(base_path)
    ext_tree = parse_xml(ext_path)
    base_root = base_tree.getroot()
    ext_root = ext_tree.getroot()
    changed = False

    _record_unsupported_extension_top_level(ext_root, report, rel_path)

    for flag_name in TOP_LEVEL_FLAGS:
        ext_flags = _top_level_elements(ext_root, flag_name)
        if not ext_flags:
            continue
        if len(ext_flags) > 1:
            _record_conflict(
                report,
                rel_path,
                object_path=_rights_root_path(rel_path),
                property_path=flag_name,
                reason="duplicate_extension_flag_key",
                details=f"Duplicate extension top-level flag in Rights.xml: {flag_name}",
            )
            continue
        if _insert_or_replace_flag(base_root, ext_flags[0], report, rel_path):
            changed = True

    if _merge_objects(base_root, ext_root, report, rel_path, base_config_name, ext_config_name):
        changed = True
    if _merge_restriction_templates(base_root, ext_root, report, rel_path, base_config_name, ext_config_name):
        changed = True

    if changed:
        _write_rights_xml(out_path, base_tree, base_path, ext_path)
        if _extension_root_reference_left(out_path, ext_config_name):
            _record_conflict(
                report,
                rel_path,
                object_path=_rights_root_path(rel_path),
                property_path="Configuration",
                reason="rights_xml_extension_configuration_reference_left",
                details=f"Rights.xml still contains Configuration.{ext_config_name} after semantic merge",
            )
        return RoleRightsMergeResult(changed=True, strategy="merge_role_rights_semantic")

    if base_path.resolve() != out_path.resolve():
        copy_file(base_path, out_path)
    return RoleRightsMergeResult(changed=False, strategy="keep_base_role_rights")
