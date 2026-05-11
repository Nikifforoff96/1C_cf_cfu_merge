from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .io_utils import write_text
from .models import MergeReport


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    return value


def write_json_report(report: MergeReport, path: Path) -> None:
    data = _to_jsonable(report)
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8-sig", newline="lf")


def write_human_report(report: MergeReport, path: Path) -> None:
    lines: list[str] = []
    lines.append(f"Статус: {report.status}")
    lines.append("")
    lines.append("Сводка:")
    for key, value in report.summary.items():
        lines.append(f"- {key}: {value}")
    if report.objects["added"]:
        lines.append("")
        lines.append("Добавлено:")
        for item in report.objects["added"][:200]:
            lines.append(f"- {item.get('type')}: {item.get('name')} ({item.get('path')}) [{item.get('strategy')}]")
        if len(report.objects["added"]) > 200:
            lines.append(f"- ... еще {len(report.objects['added']) - 200}")
    if report.objects["modified"]:
        lines.append("")
        lines.append("Изменено:")
        for item in report.objects["modified"][:200]:
            lines.append(f"- {item.get('type')}: {item.get('name')} ({item.get('path')}) [{item.get('strategy')}]")
        if len(report.objects["modified"]) > 200:
            lines.append(f"- ... еще {len(report.objects['modified']) - 200}")
    if report.metadata_merge:
        lines.append("")
        lines.append("Metadata merge:")
        for item in report.metadata_merge[:300]:
            object_path = item.get("object_path") or item.get("source_path")
            prop = item.get("property_path")
            action = item.get("action")
            reason = item.get("reason")
            suffix = f" {prop}" if prop else ""
            lines.append(f"- {action}: {object_path}{suffix} [{reason}]")
        if len(report.metadata_merge) > 300:
            lines.append(f"- ... more {len(report.metadata_merge) - 300}")
    if report.warnings:
        lines.append("")
        lines.append("Предупреждения:")
        for item in report.warnings[:200]:
            lines.append(f"- {item.code}: {item.path}: {item.details}")
        if len(report.warnings) > 200:
            lines.append(f"- ... еще {len(report.warnings) - 200}")
    if report.conflicts:
        lines.append("")
        lines.append("Конфликты:")
        for item in report.conflicts:
            lines.append(f"- {item.code}: {item.path}: {item.details}")
    if report.validation:
        lines.append("")
        lines.append("Валидация:")
        for key, value in report.validation.items():
            lines.append(f"- {key}: {value}")
    write_text(path, "\n".join(lines) + "\n", encoding="utf-8-sig", newline="lf")
