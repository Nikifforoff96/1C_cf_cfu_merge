from __future__ import annotations

import argparse
import json
from pathlib import Path

from .merge_engine import merge
from .models import MergeConfig


def _project_defaults() -> tuple[Path | None, Path | None]:
    cfg_path = Path(".v8-project.json")
    if not cfg_path.exists():
        return None, None
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None, None
    v8 = Path(data["v8path"]) if data.get("v8path") else None
    db = None
    default = data.get("default")
    for item in data.get("databases", []):
        if item.get("id") == default:
            db = Path(item["path"]) if item.get("path") else None
            break
    return v8, db


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cfmerge")
    sub = parser.add_subparsers(dest="command")
    merge_parser = sub.add_parser("merge", help="Материализовать одно расширение cfu в обычную конфигурацию cf")
    merge_parser.add_argument("--cf", required=True, type=Path)
    merge_parser.add_argument("--cfu", required=True, type=Path)
    merge_parser.add_argument("--out", required=True, type=Path)
    merge_parser.add_argument("--report", type=Path)
    merge_parser.add_argument("--write-human-report", type=Path)
    merge_parser.add_argument("--dry-run", action="store_true")
    merge_parser.add_argument("--force", action="store_true")
    merge_parser.add_argument("--backup", action="store_true")
    merge_parser.add_argument("--conflict-strategy", choices=["fail", "manual-review", "skip"], default="fail")
    merge_parser.add_argument("--encoding", default="auto")
    merge_parser.add_argument("--line-endings", default="preserve")
    merge_parser.add_argument("--log-level", default="info")
    merge_parser.add_argument("--fail-on-conflict", action="store_true")
    merge_parser.add_argument("--preserve-formatting", action="store_true", default=True)
    merge_parser.add_argument("--unsafe-text-merge", action="store_true")
    merge_parser.add_argument("--validate-xml", action="store_true")
    merge_parser.add_argument("--validate-bsl", action="store_true")
    merge_parser.add_argument("--validate-1c", action="store_true")
    merge_parser.add_argument("--v8-path", type=Path)
    merge_parser.add_argument("--infobase-path", type=Path)
    merge_parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "merge":
        parser.print_help()
        return 2
    default_v8, default_db = _project_defaults()
    cfg = MergeConfig(
        cf_dir=args.cf,
        cfu_dir=args.cfu,
        out_dir=args.out,
        report_path=args.report,
        human_report_path=args.write_human_report,
        dry_run=args.dry_run,
        force=args.force,
        backup=args.backup,
        conflict_strategy=args.conflict_strategy,
        encoding_policy=args.encoding,
        line_endings=args.line_endings,
        validate_xml=args.validate_xml,
        validate_bsl=args.validate_bsl,
        validate_1c=args.validate_1c,
        v8_path=args.v8_path or default_v8,
        infobase_path=args.infobase_path or default_db,
        verbose=args.verbose,
        unsafe_text_merge=args.unsafe_text_merge,
        fail_on_conflict=args.fail_on_conflict,
    )
    try:
        report = merge(cfg)
    except Exception as exc:
        print(f"Ошибка merge: {exc}")
        return 1
    print(f"Статус: {report.status}")
    print(f"Файлов cf: {report.summary['files_scanned_cf']}, cfu: {report.summary['files_scanned_cfu']}")
    print(f"Добавлено: {report.summary['files_added']}, изменено: {report.summary['files_changed']}, предупреждений: {report.summary['warnings']}, конфликтов: {report.summary['conflicts']}")
    if report.validation:
        for name, status in report.validation.items():
            print(f"{name}: {status}")
    return 1 if report.conflicts else 0
