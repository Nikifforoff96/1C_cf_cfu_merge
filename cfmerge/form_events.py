from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .bsl_merge import EventHook
from .bsl_parser import parse_module
from .form_identity import FormIdentity
from .form_report import FormMergeStats
from .models import MergeReport
from .xml_utils import child, children, clone_element, namespace


CALL_TYPE_MODEL = {
    "Before": "before",
    "After": "after",
    "Override": "override",
}


@dataclass(slots=True)
class ModuleMethodIndex:
    names: set[str]


def module_method_index(module_text: str | None) -> ModuleMethodIndex:
    if not module_text:
        return ModuleMethodIndex(names=set())
    return ModuleMethodIndex(names={method.local_name.lower() for method in parse_module(module_text).methods})


def _handler_exists(handler: str, methods: ModuleMethodIndex) -> bool:
    return bool(handler) and handler.lower() in methods.names


def _event_map(container: ET.Element | None) -> dict[str, ET.Element]:
    if container is None:
        return {}
    return {item.attrib.get("name", ""): item for item in children(container, "Event")}


def _ensure_events(owner: ET.Element) -> ET.Element:
    events = child(owner, "Events")
    if events is not None:
        return events
    ns = namespace(owner.tag)
    events = ET.Element(f"{{{ns}}}Events" if ns else "Events")
    owner.append(events)
    return events


def _merge_action(
    current_owner: ET.Element,
    ancestor_owner: ET.Element | None,
    extension_owner: ET.Element,
    *,
    owner_key: FormIdentity | None,
    rel_path: str,
    report: MergeReport,
    stats: FormMergeStats,
    hooks: list[EventHook],
    methods: ModuleMethodIndex,
) -> None:
    extension_action = child(extension_owner, "Action")
    if extension_action is None:
        return
    current_action = child(current_owner, "Action")
    ancestor_action = child(ancestor_owner, "Action") if ancestor_owner is not None else None
    call_type = extension_action.attrib.get("callType")
    extension_handler = (extension_action.text or "").strip()
    current_handler = (current_action.text or "").strip() if current_action is not None else ""
    ancestor_handler = (ancestor_action.text or "").strip() if ancestor_action is not None else ""
    owner_label = owner_key.render() if owner_key is not None else "form"

    if call_type:
        mode = CALL_TYPE_MODEL.get(call_type)
        if mode is None:
            report.add_conflict("UNKNOWN_FORM_CALL_TYPE", rel_path, f"{owner_label}/Action callType={call_type}")
            return
        if not _handler_exists(extension_handler, methods):
            report.add_conflict(
                "FORM_EVENT_HANDLER_NOT_FOUND_IN_EXTENSION_MODULE",
                rel_path,
                f"{owner_label}/Action -> {extension_handler}",
                object_type="FormAction",
                object_name=owner_label,
            )
            return
        if mode == "override":
            if current_action is None:
                current_owner.append(clone_element(extension_action))
            else:
                current_action.text = extension_handler
                current_action.attrib.pop("callType", None)
            stats.xml_events += 1
            return
        target_handler = current_handler or ancestor_handler
        if target_handler:
            if target_handler.lower() == extension_handler.lower():
                return
            hooks.append(EventHook(
                target_handler=target_handler,
                extension_handler=extension_handler,
                mode=mode,
                path=rel_path,
                event_name="Action",
            ))
            stats.bsl_hooks += 1
            return
        if current_action is None:
            current_owner.append(clone_element(extension_action))
        else:
            current_action.text = extension_handler
            current_action.attrib.pop("callType", None)
        stats.xml_events += 1
        return

    if current_action is None and _handler_exists(extension_handler, methods):
        current_owner.append(clone_element(extension_action))
        stats.xml_events += 1
        return
    if current_action is None:
        report.add_conflict("FORM_ACTION_HANDLER_NOT_FOUND", rel_path, f"{owner_label}/Action -> {extension_handler}")
        return
    if extension_handler == ancestor_handler or extension_handler == current_handler:
        return
    if current_handler == ancestor_handler and _handler_exists(extension_handler, methods):
        current_action.text = extension_handler
        stats.xml_events += 1
        return
    report.add_conflict(
        "FORM_ACTION_CONFLICT",
        rel_path,
        f"{owner_label}/Action",
        object_type="FormAction",
        object_name=owner_label,
        context={
            "ancestor": ancestor_handler,
            "current": current_handler,
            "extension": extension_handler,
        },
    )


def merge_events_for_owner(
    current_owner: ET.Element,
    ancestor_owner: ET.Element | None,
    extension_owner: ET.Element,
    *,
    owner_key: FormIdentity | None,
    rel_path: str,
    report: MergeReport,
    stats: FormMergeStats,
    hooks: list[EventHook],
    methods: ModuleMethodIndex,
) -> None:
    extension_events = _event_map(child(extension_owner, "Events"))
    ancestor_events = _event_map(child(ancestor_owner, "Events") if ancestor_owner is not None else None)
    current_events_container = child(current_owner, "Events")
    current_events = _event_map(current_events_container)
    owner_label = owner_key.render() if owner_key is not None else "form"

    for event_name, extension_event in extension_events.items():
        ancestor_event = ancestor_events.get(event_name)
        current_event = current_events.get(event_name)
        call_type = extension_event.attrib.get("callType")
        extension_handler = (extension_event.text or "").strip()
        current_handler = (current_event.text or "").strip() if current_event is not None else ""
        ancestor_handler = (ancestor_event.text or "").strip() if ancestor_event is not None else ""

        if call_type:
            mode = CALL_TYPE_MODEL.get(call_type)
            if mode is None:
                report.add_conflict("UNKNOWN_FORM_CALL_TYPE", rel_path, f"{owner_label}/{event_name} callType={call_type}")
                continue
            if not _handler_exists(extension_handler, methods):
                report.add_conflict(
                    "FORM_EVENT_HANDLER_NOT_FOUND_IN_EXTENSION_MODULE",
                    rel_path,
                    f"{owner_label}/{event_name} -> {extension_handler}",
                    object_type="FormEvent",
                    object_name=event_name,
                )
                continue
            if mode == "override":
                if current_event is None:
                    events = _ensure_events(current_owner)
                    new_event = clone_element(extension_event)
                    new_event.attrib.pop("callType", None)
                    events.append(new_event)
                else:
                    current_event.text = extension_handler
                    current_event.attrib.pop("callType", None)
                stats.xml_events += 1
                continue
            target_handler = current_handler or ancestor_handler
            if target_handler:
                if target_handler.lower() == extension_handler.lower():
                    continue
                hooks.append(EventHook(
                    target_handler=target_handler,
                    extension_handler=extension_handler,
                    mode=mode,
                    path=rel_path,
                    event_name=event_name,
                ))
                stats.bsl_hooks += 1
                continue
            events = _ensure_events(current_owner)
            if current_event is None:
                new_event = clone_element(extension_event)
                new_event.attrib.pop("callType", None)
                events.append(new_event)
            else:
                current_event.text = extension_handler
                current_event.attrib.pop("callType", None)
            stats.xml_events += 1
            continue

        if current_event is None and _handler_exists(extension_handler, methods):
            events = _ensure_events(current_owner)
            events.append(clone_element(extension_event))
            stats.xml_events += 1
            continue
        if current_event is None:
            report.add_conflict("FORM_EVENT_HANDLER_NOT_FOUND", rel_path, f"{owner_label}/{event_name} -> {extension_handler}")
            continue
        if extension_handler == ancestor_handler or extension_handler == current_handler:
            continue
        if current_handler == ancestor_handler and _handler_exists(extension_handler, methods):
            current_event.text = extension_handler
            stats.xml_events += 1
            continue
        report.add_conflict(
            "FORM_EVENT_CONFLICT",
            rel_path,
            f"{owner_label}/{event_name}",
            object_type="FormEvent",
            object_name=event_name,
            context={
                "ancestor": ancestor_handler,
                "current": current_handler,
                "extension": extension_handler,
            },
        )

    _merge_action(
        current_owner,
        ancestor_owner,
        extension_owner,
        owner_key=owner_key,
        rel_path=rel_path,
        report=report,
        stats=stats,
        hooks=hooks,
        methods=methods,
    )
