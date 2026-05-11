from __future__ import annotations

import xml.etree.ElementTree as ET

from .form_identity import conditional_appearance_rule_signatures, normalize_xml_fragment
from .form_report import FormMergeStats
from .models import MergeReport
from .xml_utils import child, children, clone_element


def _attribute_container(root: ET.Element | None) -> ET.Element | None:
    if root is None:
        return None
    return child(root, "Attributes")


def _conditional_appearance(root: ET.Element | None) -> ET.Element | None:
    attrs = _attribute_container(root)
    if attrs is None:
        return None
    return child(attrs, "ConditionalAppearance")


def _rule_maps(container: ET.Element | None) -> tuple[dict[str, ET.Element], dict[str, list[ET.Element]], list[ET.Element]]:
    if container is None:
        return {}, {}, []
    strict: dict[str, ET.Element] = {}
    loose: dict[str, list[ET.Element]] = {}
    rules = [item for item in list(container) if isinstance(item.tag, str)]
    for rule in rules:
        strict_sig, loose_sig = conditional_appearance_rule_signatures(rule)
        strict[strict_sig] = rule
        loose.setdefault(loose_sig, []).append(rule)
    return strict, loose, rules


def merge_conditional_appearance(
    current_root: ET.Element,
    ancestor_root: ET.Element | None,
    extension_root: ET.Element,
    *,
    rel_path: str,
    report: MergeReport,
    stats: FormMergeStats,
) -> None:
    extension_ca = _conditional_appearance(extension_root)
    if extension_ca is None:
        return
    current_attrs = _attribute_container(current_root)
    if current_attrs is None:
        return
    current_ca = _conditional_appearance(current_root)
    if current_ca is None:
        current_attrs.append(clone_element(extension_ca))
        stats.conditional_appearance_added += len([item for item in list(extension_ca) if isinstance(item.tag, str)])
        return
    ancestor_ca = _conditional_appearance(ancestor_root)

    ancestor_strict, ancestor_loose, _ = _rule_maps(ancestor_ca)
    current_strict, current_loose, current_rules = _rule_maps(current_ca)
    extension_strict, extension_loose, extension_rules = _rule_maps(extension_ca)
    used_current: set[int] = set()
    merged_rules: list[ET.Element] = []

    for extension_rule in extension_rules:
        strict_sig, loose_sig = conditional_appearance_rule_signatures(extension_rule)
        ancestor_rule = ancestor_strict.get(strict_sig)
        if ancestor_rule is None:
            loose_matches = ancestor_loose.get(loose_sig, [])
            if len(loose_matches) == 1:
                ancestor_rule = loose_matches[0]
        current_rule = current_strict.get(strict_sig)
        if current_rule is None:
            loose_current = current_loose.get(loose_sig, [])
            if len(loose_current) == 1:
                current_rule = loose_current[0]
        if ancestor_rule is None and current_rule is None:
            merged_rules.append(clone_element(extension_rule))
            stats.conditional_appearance_added += 1
            continue
        if ancestor_rule is None and current_rule is not None:
            current_sig = normalize_xml_fragment(current_rule)
            extension_sig = normalize_xml_fragment(extension_rule)
            if current_sig != extension_sig:
                report.add_conflict(
                    "FORM_CONDITIONAL_APPEARANCE_CONFLICT",
                    rel_path,
                    strict_sig[:200],
                    object_type="ConditionalAppearance",
                    context={"current": current_sig[:4000], "extension": extension_sig[:4000]},
                )
            merged_rules.append(current_rule)
            used_current.add(id(current_rule))
            continue
        if current_rule is None:
            report.add_warning(
                "FORM_CONDITIONAL_APPEARANCE_TARGET_NOT_FOUND_APPLIED_AS_ADD",
                rel_path,
                strict_sig[:200],
            )
            merged_rules.append(clone_element(extension_rule))
            stats.conditional_appearance_added += 1
            continue
        ancestor_sig = normalize_xml_fragment(ancestor_rule)
        current_sig = normalize_xml_fragment(current_rule)
        extension_sig = normalize_xml_fragment(extension_rule)
        if extension_sig == ancestor_sig or current_sig == extension_sig:
            merged_rules.append(current_rule)
            used_current.add(id(current_rule))
            continue
        if current_sig == ancestor_sig:
            merged_rules.append(clone_element(extension_rule))
            stats.conditional_appearance_changed += 1
            continue
        report.add_conflict(
            "FORM_CONDITIONAL_APPEARANCE_CONFLICT",
            rel_path,
            strict_sig[:200],
            object_type="ConditionalAppearance",
            context={
                "ancestor": ancestor_sig[:4000],
                "current": current_sig[:4000],
                "extension": extension_sig[:4000],
            },
        )
        merged_rules.append(current_rule)
        used_current.add(id(current_rule))

    for current_rule in current_rules:
        if id(current_rule) not in used_current:
            merged_rules.append(current_rule)

    for item in list(current_ca):
        current_ca.remove(item)
    for item in merged_rules:
        current_ca.append(item)
