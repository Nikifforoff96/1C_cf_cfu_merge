from __future__ import annotations

from dataclasses import asdict, dataclass

from .models import MergeReport


@dataclass(slots=True)
class FormMergeStats:
    elements_added: int = 0
    elements_moved: int = 0
    properties_changed: int = 0
    conditional_appearance_added: int = 0
    conditional_appearance_changed: int = 0
    command_interface_added: int = 0
    command_interface_changed: int = 0
    xml_events: int = 0
    bsl_hooks: int = 0


def apply_form_stats(report: MergeReport, rel_path: str, stats: FormMergeStats) -> None:
    report.diagnostics.setdefault("form_merge", {})[rel_path] = asdict(stats)
    for key, value in asdict(stats).items():
        report.summary[key] = report.summary.get(key, 0) + value
