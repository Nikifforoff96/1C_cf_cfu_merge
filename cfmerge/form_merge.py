from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .bsl_merge import EventHook
from .io_utils import read_text
from .models import MergeReport
from .xml_patch import (
    et_path,
    insert_before_close,
    insert_root_events_block,
    parent_map,
    remove_base_form,
    replace_span_text,
    root_container_path,
    serialize_et_element_from_source,
    span_map,
    strip_call_type,
    write_patched_like_source,
)
from .xml_utils import (
    NS_LF,
    child,
    children,
    clone_element,
    element_key,
    local_name,
    parse_xml,
    remove_children_by_local,
    write_xml,
)


CALL_TYPE_MODEL = {
    "Before": "before",
    "After": "after",
    "Override": "override",
}
FORM_DELTA_CONTAINERS = ("ChildItems", "Attributes", "Commands", "Columns")
FORM_DELTA_STRUCTURAL_NODES = set(FORM_DELTA_CONTAINERS) | {"Events", "Action", "BaseForm"}


def _container(root: ET.Element, name: str) -> ET.Element | None:
    return child(root, name)


def _keyed_children(container: ET.Element | None) -> dict[tuple[str, str], ET.Element]:
    if container is None:
        return {}
    return {element_key(item): item for item in children(container)}


def _find_by_key(root: ET.Element, target: ET.Element) -> ET.Element | None:
    key = element_key(target)
    for item in root.iter():
        if not isinstance(item.tag, str):
            continue
        if element_key(item) == key:
            return item
    return None


def _ensure_container(root: ET.Element, name: str) -> ET.Element:
    found = child(root, name)
    if found is not None:
        return found
    found = ET.Element(f"{{{NS_LF}}}{name}")
    if name == "Events":
        for idx, item in enumerate(list(root)):
            if isinstance(item.tag, str) and local_name(item.tag) in {"ChildItems", "Attributes", "Commands"}:
                root.insert(idx, found)
                return found
    root.append(found)
    return found


def _method_names(module_text: str | None) -> set[str]:
    if not module_text:
        return set()
    names = set(re.findall(r"(?im)^[ \t]*(?:Асинх\s+)?(?:Процедура|Функция)\s+([A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*)\s*\(", module_text))
    return {n.lower() for n in names}


def _event_parent_locator(root: ET.Element, event: ET.Element) -> ET.Element | None:
    # xml.etree has no parent pointers; locate by scanning known containers.
    for parent in root.iter():
        if not isinstance(parent.tag, str):
            continue
        for item in list(parent):
            if item is event:
                return parent
            if local_name(item.tag) in {"Events", "Commands"}:
                for nested in list(item):
                    if nested is event:
                        return parent
    return None


def _collect_event_like(root: ET.Element) -> list[ET.Element]:
    result: list[ET.Element] = []
    for item in root.iter():
        if not isinstance(item.tag, str):
            continue
        if local_name(item.tag) == "Event":
            result.append(item)
        elif local_name(item.tag) == "Action":
            result.append(item)
    return result


def _find_matching_event(current_root: ET.Element, ext_root: ET.Element, ext_event: ET.Element) -> ET.Element | None:
    ext_parent = _event_parent_locator(ext_root, ext_event)
    if ext_parent is None:
        return None
    parent_local = local_name(ext_parent.tag)
    if parent_local in {"Events", "Commands"}:
        current_parent = child(current_root, parent_local)
    elif parent_local == "Form":
        current_parent = current_root
    else:
        current_parent = _find_by_key(current_root, ext_parent)
    if current_parent is None:
        return None
    if local_name(ext_event.tag) == "Event":
        events = child(current_parent, "Events")
        if events is None:
            return None
        for item in children(events, "Event"):
            if item.attrib.get("name") == ext_event.attrib.get("name"):
                return item
    elif local_name(ext_event.tag) == "Action":
        action = child(current_parent, "Action")
        if action is not None:
            return action
    return None


def _add_or_replace_event(current_root: ET.Element, ext_root: ET.Element, ext_event: ET.Element, handler: str) -> None:
    current_event = _find_matching_event(current_root, ext_root, ext_event)
    if current_event is not None:
        current_event.text = handler
        current_event.attrib.pop("callType", None)
        return
    ext_parent = _event_parent_locator(ext_root, ext_event)
    if ext_parent is None:
        return
    current_parent = _find_by_key(current_root, ext_parent)
    if current_parent is None and local_name(ext_parent.tag) == "Form":
        current_parent = current_root
    if current_parent is None:
        return
    if local_name(ext_event.tag) == "Event":
        events = _ensure_container(current_parent, "Events")
        new_event = clone_element(ext_event)
        new_event.attrib.pop("callType", None)
        events.append(new_event)
    elif local_name(ext_event.tag) == "Action":
        old = child(current_parent, "Action")
        if old is not None:
            current_parent.remove(old)
        new_action = clone_element(ext_event)
        new_action.attrib.pop("callType", None)
        current_parent.append(new_action)


def _copy_added_container_children(current_root: ET.Element, ext_root: ET.Element, snapshot: ET.Element | None, report: MergeReport, rel_path: str) -> int:
    changed = 0
    for container_name in ("ChildItems", "Attributes", "Commands"):
        ext_container = _container(ext_root, container_name)
        if ext_container is None:
            continue
        snap_container = _container(snapshot, container_name) if snapshot is not None else None
        current_container = _ensure_container(current_root, container_name)
        snapshot_keys = set(_keyed_children(snap_container))
        current_keys = set(_keyed_children(current_container))
        for item in children(ext_container):
            key = element_key(item)
            if key in snapshot_keys or key in current_keys:
                continue
            cloned = clone_element(item)
            for ev in _collect_event_like(cloned):
                ev.attrib.pop("callType", None)
            current_container.append(cloned)
            current_keys.add(key)
            changed += 1
            report.objects["added"].append({
                "type": f"Form.{container_name}.{key[0]}",
                "name": key[1],
                "path": rel_path,
                "strategy": "form_delta_add_child",
            })
    return changed


def _element_label(element: ET.Element | None) -> str:
    if element is None:
        return "<none>"
    name = element.attrib.get("name")
    return f"{local_name(element.tag)}:{name}" if name else local_name(element.tag)


def _subtree_has_added_children(ext_parent: ET.Element, snapshot_parent: ET.Element | None) -> bool:
    container_entries: list[tuple[ET.Element, ET.Element | None]] = []
    for container_name in FORM_DELTA_CONTAINERS:
        ext_container = child(ext_parent, container_name)
        if ext_container is None:
            continue
        snap_container = child(snapshot_parent, container_name) if snapshot_parent is not None else None
        container_entries.append((ext_container, snap_container))
    if local_name(ext_parent.tag) == "AdditionalColumns":
        container_entries.append((ext_parent, snapshot_parent))
    for ext_container, snap_container in container_entries:
        snapshot_children = _keyed_children(snap_container)
        for ext_child in children(ext_container):
            key = element_key(ext_child)
            snap_child = snapshot_children.get(key)
            if snap_child is None:
                return True
            if _subtree_has_added_children(ext_child, snap_child):
                return True
    return False


def _first_child_by_local(parent: ET.Element | None, local: str) -> ET.Element | None:
    if parent is None:
        return None
    for item in children(parent):
        if local_name(item.tag) == local:
            return item
    return None


def _property_signature(element: ET.Element | None) -> str:
    if element is None:
        return "<missing>"
    text = ET.tostring(element, encoding="unicode", short_empty_elements=True)
    text = re.sub(r">\s+<", "><", text)
    return text.strip()


def _report_unapplied_property_deltas(
    current_parent: ET.Element,
    ext_parent: ET.Element,
    snapshot_parent: ET.Element | None,
    report: MergeReport,
    rel_path: str,
    counter: list[int],
) -> None:
    if snapshot_parent is None or counter[0] >= 100:
        return
    element_name = element_key(ext_parent)[1] or _element_label(ext_parent)
    for ext_prop in children(ext_parent):
        prop_name = local_name(ext_prop.tag)
        if prop_name in FORM_DELTA_STRUCTURAL_NODES:
            continue
        snapshot_prop = _first_child_by_local(snapshot_parent, prop_name)
        if snapshot_prop is None:
            continue
        if _property_signature(ext_prop) == _property_signature(snapshot_prop):
            continue
        current_prop = _first_child_by_local(current_parent, prop_name)
        if _property_signature(current_prop) == _property_signature(ext_prop):
            continue
        report.add_warning(
            "FORM_PROPERTY_DELTA_NOT_APPLIED",
            rel_path,
            f"Свойство {element_name}/{prop_name} отличается в overlay и BaseForm, но текущий patch layer применяет только добавления элементов/события",
            context={
                "element": element_name,
                "property": prop_name,
                "snapshot_value": _property_signature(snapshot_prop)[:1000],
                "overlay_value": _property_signature(ext_prop)[:1000],
            },
        )
        counter[0] += 1
        if counter[0] >= 100:
            return

    for container_name in FORM_DELTA_CONTAINERS:
        ext_container = child(ext_parent, container_name)
        snap_container = child(snapshot_parent, container_name)
        current_container = child(current_parent, container_name)
        if ext_container is None or snap_container is None or current_container is None:
            continue
        snapshot_children = _keyed_children(snap_container)
        current_children = _keyed_children(current_container)
        for ext_child in children(ext_container):
            key = element_key(ext_child)
            snapshot_child = snapshot_children.get(key)
            current_child = current_children.get(key)
            if snapshot_child is None or current_child is None:
                continue
            _report_unapplied_property_deltas(current_child, ext_child, snapshot_child, report, rel_path, counter)
            if counter[0] >= 100:
                return


def _patch_added_form_children(
    result_text: str,
    current_parent: ET.Element,
    ext_parent: ET.Element,
    snapshot_parent: ET.Element | None,
    current_root: ET.Element,
    ext_root: ET.Element,
    ext_text: str,
    report: MergeReport,
    rel_path: str,
) -> tuple[str, int]:
    changed = 0
    current_parents = parent_map(current_root)
    ext_parents = parent_map(ext_root)
    container_entries: list[tuple[str, ET.Element, ET.Element | None, ET.Element | None]] = []
    for container_name in FORM_DELTA_CONTAINERS:
        ext_container = child(ext_parent, container_name)
        if ext_container is None:
            continue
        snap_container = child(snapshot_parent, container_name) if snapshot_parent is not None else None
        current_container = child(current_parent, container_name)
        container_entries.append((container_name, ext_container, snap_container, current_container))
    if local_name(ext_parent.tag) == "AdditionalColumns":
        container_entries.append(("AdditionalColumns", ext_parent, snapshot_parent, current_parent))

    for container_name, ext_container, snap_container, current_container in container_entries:
        snapshot_children = _keyed_children(snap_container)
        current_children = _keyed_children(current_container)

        if current_container is None:
            if snap_container is not None:
                report.add_conflict(
                    "FORM_DELTA_TARGET_CONTAINER_NOT_FOUND",
                    rel_path,
                    f"{_element_label(current_parent)}/{container_name}",
                    severity="manual-review",
                )
                continue
            snippet = serialize_et_element_from_source(ext_text, et_path(ext_container, ext_parents))
            parent_span = span_map(result_text).get(et_path(current_parent, current_parents))
            if not snippet or not parent_span:
                report.add_conflict(
                    "FORM_DELTA_CONTAINER_PATCH_FAILED",
                    rel_path,
                    f"{_element_label(current_parent)}/{container_name}",
                    severity="manual-review",
                )
                continue
            result_text = insert_before_close(result_text, parent_span, strip_call_type(snippet))
            changed += 1
            continue

        for ext_child in children(ext_container):
            key = element_key(ext_child)
            snapshot_child = snapshot_children.get(key)
            current_child = current_children.get(key)
            if snapshot_child is None and current_child is None:
                snippet = serialize_et_element_from_source(ext_text, et_path(ext_child, ext_parents))
                container_span = span_map(result_text).get(et_path(current_container, current_parents))
                if not snippet or not container_span:
                    report.add_conflict(
                        "FORM_DELTA_CHILD_PATCH_FAILED",
                        rel_path,
                        f"{container_name}/{key}",
                        severity="manual-review",
                    )
                    continue
                result_text = insert_before_close(result_text, container_span, strip_call_type(snippet))
                current_children[key] = ext_child
                changed += 1
                report.objects["added"].append({
                    "type": f"Form.{container_name}.{key[0]}",
                    "name": key[1],
                    "path": rel_path,
                    "strategy": "form_delta_add_child_recursive_lossless_patch",
                })
                continue
            if current_child is None:
                if snapshot_child is not None and _subtree_has_added_children(ext_child, snapshot_child):
                    report.add_conflict(
                        "FORM_DELTA_TARGET_PARENT_NOT_FOUND",
                        rel_path,
                        f"{container_name}/{key}",
                        severity="manual-review",
                    )
                continue
            result_text, nested_changed = _patch_added_form_children(
                result_text,
                current_child,
                ext_child,
                snapshot_child,
                current_root,
                ext_root,
                ext_text,
                report,
                rel_path,
            )
            changed += nested_changed
    return result_text, changed


def _patch_event(
    text: str,
    ext_text: str,
    current_root: ET.Element,
    ext_root: ET.Element,
    current_event: ET.Element | None,
    ext_event: ET.Element,
    report: MergeReport,
    rel_path: str,
) -> tuple[str, bool]:
    ext_parents = parent_map(ext_root)
    ext_snippet = serialize_et_element_from_source(ext_text, et_path(ext_event, ext_parents))
    if not ext_snippet:
        report.add_warning("FORM_EVENT_SOURCE_SNIPPET_NOT_FOUND", rel_path, f"Не найден исходный XML-фрагмент события {ext_event.attrib.get('name', 'Action')}")
        return text, False
    ext_snippet = strip_call_type(ext_snippet)
    if current_event is not None:
        current_parents = parent_map(current_root)
        current_path = et_path(current_event, current_parents)
        spans = span_map(text)
        span = spans.get(current_path)
        if not span:
            report.add_warning("FORM_EVENT_TARGET_SPAN_NOT_FOUND", rel_path, f"Не найден XML-span события {ext_event.attrib.get('name', 'Action')}")
            return text, False
        return replace_span_text(text, span, ext_snippet), True

    ext_parent = _event_parent_locator(ext_root, ext_event)
    if ext_parent is None:
        return text, False
    ext_parent_local = local_name(ext_parent.tag)
    if ext_parent_local == "Form":
        if local_name(ext_event.tag) == "Event":
            spans = span_map(text)
            events_span = spans.get(root_container_path("Events"))
            if events_span:
                return insert_before_close(text, events_span, ext_snippet), True
            return insert_root_events_block(text, ext_snippet), True
        report.add_warning("FORM_ACTION_WITHOUT_PARENT_SKIPPED", rel_path, f"Action без команды: {(ext_event.text or '').strip()}")
        return text, False

    if local_name(ext_event.tag) == "Action":
        # If the whole command is extension-only, it is added by the Commands delta path.
        current_parent = _find_by_key(current_root, ext_parent)
        if current_parent is None:
            return text, False
        current_parents = parent_map(current_root)
        parent_path = et_path(current_parent, current_parents)
        spans = span_map(text)
        parent_span = spans.get(parent_path)
        if parent_span:
            return insert_before_close(text, parent_span, ext_snippet), True
    else:
        current_parent = _find_by_key(current_root, ext_parent)
        if current_parent is None:
            return text, False
        current_parents = parent_map(current_root)
        parent_path = et_path(current_parent, current_parents)
        events_path = parent_path + (("Events", ""),)
        spans = span_map(text)
        events_span = spans.get(events_path)
        if events_span:
            return insert_before_close(text, events_span, ext_snippet), True
        parent_span = spans.get(parent_path)
        if parent_span:
            nl = "\r\n" if "\r\n" in text else "\n"
            block = f"<Events>{nl}{ext_snippet.rstrip()}{nl}</Events>"
            return insert_before_close(text, parent_span, block), True
    report.add_warning("FORM_EVENT_PATCH_SKIPPED", rel_path, f"Не удалось минимально вставить событие {ext_event.attrib.get('name', 'Action')}")
    return text, False


def _event_hooks(current_root: ET.Element, ext_root: ET.Element, module_text: str | None, rel_path: str, report: MergeReport, base_text: str, ext_text: str) -> tuple[list[EventHook], str, int]:
    hooks: list[EventHook] = []
    patched_text = base_text
    patched = 0
    methods = _method_names(module_text)
    seen: set[tuple[str, str, str]] = set()
    for ext_event in _collect_event_like(ext_root):
        call_type = ext_event.attrib.get("callType")
        if not call_type:
            continue
        mode = CALL_TYPE_MODEL.get(call_type)
        handler = (ext_event.text or "").strip()
        if not mode:
            report.add_conflict("UNKNOWN_FORM_CALL_TYPE", rel_path, f"Неизвестный callType={call_type}", severity="error")
            continue
        current_event = _find_matching_event(current_root, ext_root, ext_event)
        base_handler = (current_event.text or "").strip() if current_event is not None else ""
        key = ((ext_event.attrib.get("name") or "Action"), handler, mode)
        if key in seen:
            continue
        seen.add(key)
        if mode == "override" or base_handler.lower() == handler.lower():
            patched_text, ok = _patch_event(patched_text, ext_text, current_root, ext_root, current_event, ext_event, report, rel_path)
            patched += int(ok)
            report.objects["modified"].append({
                "type": "FormEvent",
                "name": ext_event.attrib.get("name", "Action"),
                "path": rel_path,
                "strategy": f"form_event_{mode}",
            })
            continue
        if mode in {"before", "after"} and not base_handler:
            patched_text, ok = _patch_event(patched_text, ext_text, current_root, ext_root, current_event, ext_event, report, rel_path)
            if not ok and handler and handler in patched_text:
                ok = True
            patched += int(ok)
            report.objects["modified"].append({
                "type": "FormEvent",
                "name": ext_event.attrib.get("name", "Action"),
                "path": rel_path,
                "strategy": f"form_event_extension_only_{mode}_as_plain",
                "extension_handler": handler,
            })
            if not ok:
                report.add_warning("FORM_EVENT_EXTENSION_ONLY_PATCH_FAILED", rel_path, f"{ext_event.attrib.get('name', 'Action')} -> {handler}")
            continue
        if handler.lower() not in methods:
            report.add_warning("FORM_EVENT_HANDLER_NOT_FOUND_IN_EXTENSION_MODULE", rel_path, f"{handler} не найден в модуле формы; wrapper не создан")
            continue
        hooks.append(EventHook(
            target_handler=base_handler,
            extension_handler=handler,
            mode=mode,
            path=rel_path,
            event_name=ext_event.attrib.get("name", "Action"),
        ))
        # Plain XML keeps the base handler. Runtime order is materialized in BSL.
        report.objects["modified"].append({
            "type": "FormEvent",
            "name": ext_event.attrib.get("name", "Action"),
            "path": rel_path,
            "strategy": f"form_event_wrapper_{mode}",
            "base_handler": base_handler,
            "extension_handler": handler,
        })
    return hooks, patched_text, patched


def merge_form_visual(base_path: Path, ext_path: Path, out_path: Path, rel_path: str, report: MergeReport, module_text: str | None = None) -> list[EventHook]:
    base_tree = parse_xml(base_path)
    ext_tree = parse_xml(ext_path)
    current_root = base_tree.getroot()
    ext_root = ext_tree.getroot()
    base_text = read_text(base_path)
    ext_text = read_text(ext_path)
    result_text = base_text
    snapshot = child(ext_root, "BaseForm")
    if snapshot is not None:
        _report_unapplied_property_deltas(current_root, ext_root, snapshot, report, rel_path, [0])

    result_text, changed = _patch_added_form_children(
        result_text,
        current_root,
        ext_root,
        snapshot,
        current_root,
        ext_root,
        ext_text,
        report,
        rel_path,
    )

    hooks, result_text, event_patches = _event_hooks(current_root, ext_root, module_text, rel_path, report, result_text, ext_text)
    write_patched_like_source(out_path, base_path, result_text)
    if changed or hooks or event_patches:
        report.summary["files_changed"] += 1
    return hooks


def clean_native_form_xml(src_path: Path, out_path: Path) -> None:
    text = read_text(src_path)
    text = remove_base_form(strip_call_type(text))
    write_patched_like_source(out_path, src_path, text)
