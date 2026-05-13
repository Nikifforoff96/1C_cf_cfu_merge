from __future__ import annotations

from pathlib import Path

from cfmerge.gui import (
    build_gui_merge_config,
    progress_event_to_progress_state,
    merge_report_to_log_rows,
    progress_event_to_log_row,
    validate_merge_paths,
)
from cfmerge.merge_engine import merge
from cfmerge.models import MergeConfig, MergeReport, ProgressEvent
from cfmerge.scanner import scan_tree


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CF = ROOT / "examples" / "small" / "cf"
EXAMPLE_CFU = ROOT / "examples" / "small" / "cfu"


def _config_dir(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "Configuration.xml").write_text("<root />", encoding="utf-8")
    return path


def test_validate_merge_paths_accepts_valid_directories(tmp_path: Path) -> None:
    cf = _config_dir(tmp_path / "cf")
    cfu = _config_dir(tmp_path / "cfu")
    out = tmp_path / "merged"

    assert validate_merge_paths(cf, cfu, out) == []


def test_validate_merge_paths_rejects_missing_config_and_nested_output(tmp_path: Path) -> None:
    cf = tmp_path / "cf"
    cfu = _config_dir(tmp_path / "cfu")
    cf.mkdir()

    errors = validate_merge_paths(cf, cfu, cf / "merged")

    assert any("Configuration.xml" in item for item in errors)
    assert any("основной конфигурацией" in item for item in errors)


def test_validate_merge_paths_rejects_output_containing_sources(tmp_path: Path) -> None:
    cf = _config_dir(tmp_path / "cf")
    cfu = _config_dir(tmp_path / "cfu")

    errors = validate_merge_paths(cf, cfu, tmp_path)

    assert any("содержать исходные каталоги" in item for item in errors)


def test_build_gui_merge_config_uses_planned_defaults(tmp_path: Path) -> None:
    cf = _config_dir(tmp_path / "cf")
    cfu = _config_dir(tmp_path / "cfu")
    out = tmp_path / "merged"

    cfg = build_gui_merge_config(cf, cfu, out)

    assert cfg.force is True
    assert cfg.backup is True
    assert cfg.validate_xml is True
    assert cfg.validate_bsl is True
    assert cfg.validate_1c is False
    assert cfg.report_path == out / "merge-report.json"
    assert cfg.human_report_path == out / "merge-report.txt"


def test_build_gui_merge_config_can_enable_1c_validation(tmp_path: Path) -> None:
    cf = _config_dir(tmp_path / "cf")
    cfu = _config_dir(tmp_path / "cfu")
    out = tmp_path / "merged"

    cfg = build_gui_merge_config(cf, cfu, out, validate_1c=True)

    assert cfg.validate_1c is True


def test_app_entry_point_imports_without_starting_gui() -> None:
    from cfmerge import app

    assert callable(app.main)


def test_progress_event_to_log_row() -> None:
    event = ProgressEvent(time="12:00:00", level="Инфо", stage="Этап", message="Сообщение", path="file.xml")

    row = progress_event_to_log_row(event)

    assert row.values() == ("12:00:00", "Инфо", "Этап", "Сообщение", "file.xml")


def test_progress_event_keeps_old_constructor_compatible() -> None:
    event = ProgressEvent(time="12:00:00", level="Инфо", stage="Этап", message="Сообщение")

    assert event.event_type is None
    assert event.phase_key is None
    assert event.current is None
    assert event.total is None
    assert event.overall_percent is None


def test_progress_event_to_progress_state() -> None:
    event = ProgressEvent(
        time="12:00:00",
        level="Инфо",
        stage="Сканирование",
        message="Сканирование",
        event_type="phase_progress",
        phase_key="scan",
        phase_title="Сканирование",
        current=25,
        total=100,
        unit="файлов",
        overall_percent=12.5,
    )

    state = progress_event_to_progress_state(event)

    assert state.title == "Сканирование"
    assert state.detail == "25 / 100 файлов  12%"
    assert state.percent == 12.5
    assert state.indeterminate is False


def test_merge_report_to_log_rows_includes_summary_warnings_conflicts_and_validation() -> None:
    report = MergeReport()
    report.status = "failed"
    report.summary["files_scanned_cf"] = 10
    report.summary["files_scanned_cfu"] = 5
    report.summary["files_added"] = 2
    report.summary["files_changed"] = 1
    report.summary["files_skipped"] = 3
    report.add_warning("WARN_CODE", "warning.xml", "warning details")
    report.add_conflict("CONFLICT_CODE", "conflict.xml", "conflict details")
    report.validation["xml_parse"] = "passed"

    rows = merge_report_to_log_rows(report, time_value="12:00:00")

    assert any(row.stage == "Итог" and "Статус: ошибка" in row.message for row in rows)
    assert any(row.stage == "Предупреждения" and "WARN_CODE" in row.message for row in rows)
    assert any(row.stage == "Конфликты" and "CONFLICT_CODE" in row.message for row in rows)
    assert any(row.stage == "Валидация" and "xml_parse: пройдена" in row.message for row in rows)


def test_merge_without_callback_still_returns_report(tmp_path: Path) -> None:
    report = merge(MergeConfig(cf_dir=EXAMPLE_CF, cfu_dir=EXAMPLE_CFU, out_dir=tmp_path / "merged", force=True))

    assert report.summary["files_scanned_cf"] > 0
    assert report.summary["files_scanned_cfu"] > 0


def test_merge_validate_1c_flag_controls_runner(tmp_path: Path, monkeypatch) -> None:
    calls: list[Path] = []

    def fake_run_1c_validation(cfg: MergeConfig, report: MergeReport) -> None:
        calls.append(cfg.out_dir)
        report.validation["fake_1c"] = "passed"

    monkeypatch.setattr("cfmerge.merge_engine.run_1c_validation", fake_run_1c_validation)

    report_without = merge(MergeConfig(cf_dir=EXAMPLE_CF, cfu_dir=EXAMPLE_CFU, out_dir=tmp_path / "without", force=True))
    report_with = merge(MergeConfig(cf_dir=EXAMPLE_CF, cfu_dir=EXAMPLE_CFU, out_dir=tmp_path / "with", force=True, validate_1c=True))

    assert report_without.validation.get("fake_1c") is None
    assert report_with.validation["fake_1c"] == "passed"
    assert calls == [(tmp_path / "with").resolve()]


def test_scan_tree_computes_sha256_lazily(tmp_path: Path, monkeypatch) -> None:
    cf = _config_dir(tmp_path / "cf")
    calls: list[Path] = []

    def fake_sha256_file(path: Path) -> str:
        calls.append(path)
        return "hash"

    monkeypatch.setattr("cfmerge.models.sha256_file", fake_sha256_file)

    records = scan_tree(cf)

    assert calls == []
    assert records["Configuration.xml"].sha256 == "hash"
    assert calls == [cf / "Configuration.xml"]


def test_merge_with_callback_emits_key_progress_events(tmp_path: Path) -> None:
    events: list[ProgressEvent] = []

    report = merge(
        MergeConfig(
            cf_dir=EXAMPLE_CF,
            cfu_dir=EXAMPLE_CFU,
            out_dir=tmp_path / "merged",
            force=True,
            progress_callback=events.append,
        )
    )

    assert report.summary["files_scanned_cf"] > 0
    assert any(event.stage == "Сканирование" for event in events)
    assert any(event.stage == "Подготовка результата" for event in events)
    assert any(event.event_type == "phase_start" and event.phase_key == "scan" for event in events)
    assert any(event.event_type == "phase_done" and event.phase_key == "write_reports" for event in events)
    assert events[-1].stage == "Завершение"
