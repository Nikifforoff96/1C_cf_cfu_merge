from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter

from .models import ProgressEvent


ProgressCallback = Callable[[ProgressEvent], None]


@dataclass(frozen=True, slots=True)
class ProgressPhase:
    key: str
    title: str
    weight: float


DEFAULT_PHASES: tuple[ProgressPhase, ...] = (
    ProgressPhase("scan", "Сканирование", 35.0),
    ProgressPhase("build_registries", "Построение реестров", 1.0),
    ProgressPhase("copy_base_configuration", "Копирование основной конфигурации", 30.0),
    ProgressPhase("merge_configuration", "Слияние Configuration.xml", 1.0),
    ProgressPhase("merge_metadata", "Метаданные", 3.0),
    ProgressPhase("merge_forms", "Формы", 2.0),
    ProgressPhase("merge_bsl", "BSL", 2.0),
    ProgressPhase("build_result_registry", "Построение реестра результата", 1.0),
    ProgressPhase("merge_resources", "Ресурсы", 3.0),
    ProgressPhase("merge_configuration_report", "Отчет конфигурации", 1.0),
    ProgressPhase("config_dump_info", "ConfigDumpInfo", 10.0),
    ProgressPhase("validate_xml", "Валидация XML", 8.0),
    ProgressPhase("validate_bsl", "Валидация BSL", 1.0),
    ProgressPhase("validate_1c", "Валидация 1С", 1.0),
    ProgressPhase("write_reports", "Отчеты", 1.0),
)


def merge_progress_phases(*, validate_xml: bool, validate_bsl: bool, validate_1c: bool) -> tuple[ProgressPhase, ...]:
    excluded: set[str] = set()
    if not validate_xml:
        excluded.add("validate_xml")
    if not validate_bsl:
        excluded.add("validate_bsl")
    if not validate_1c:
        excluded.add("validate_1c")
    return tuple(phase for phase in DEFAULT_PHASES if phase.key not in excluded)


class ProgressTracker:
    def __init__(
        self,
        callback: ProgressCallback | None,
        phases: tuple[ProgressPhase, ...],
        *,
        throttle_seconds: float = 0.2,
    ) -> None:
        self._callback = callback
        self._phases = {phase.key: phase for phase in phases}
        total_weight = sum(phase.weight for phase in phases) or 1.0
        self._weights = {phase.key: phase.weight * 100.0 / total_weight for phase in phases}
        self._completed: set[str] = set()
        self._last_progress_emit: dict[str, float] = {}
        self._last_overall = 0.0
        self._throttle_seconds = throttle_seconds

    def start(
        self,
        key: str,
        *,
        title: str | None = None,
        message: str | None = None,
        current: int | None = None,
        total: int | None = None,
        unit: str | None = None,
        path: str | None = None,
    ) -> None:
        phase = self._phases.get(key)
        phase_title = title or (phase.title if phase is not None else key)
        self._emit(
            key,
            phase_title,
            "phase_start",
            message or phase_title,
            current=current,
            total=total,
            unit=unit,
            path=path,
            force=True,
        )

    def update(
        self,
        key: str,
        current: int,
        *,
        total: int | None = None,
        title: str | None = None,
        message: str | None = None,
        unit: str | None = None,
        path: str | None = None,
        force: bool = False,
    ) -> None:
        now = perf_counter()
        if not force and self._throttle_seconds > 0:
            last_progress = self._last_progress_emit.get(key)
            if last_progress is not None and now - last_progress < self._throttle_seconds:
                return
            self._last_progress_emit[key] = now
        phase = self._phases.get(key)
        phase_title = title or (phase.title if phase is not None else key)
        self._emit(
            key,
            phase_title,
            "phase_progress",
            message or phase_title,
            current=current,
            total=total,
            unit=unit,
            path=path,
            force=True,
        )

    def done(
        self,
        key: str,
        *,
        title: str | None = None,
        message: str | None = None,
        current: int | None = None,
        total: int | None = None,
        unit: str | None = None,
        path: str | None = None,
    ) -> None:
        self._completed.add(key)
        phase = self._phases.get(key)
        phase_title = title or (phase.title if phase is not None else key)
        if total is not None and current is None:
            current = total
        self._emit(
            key,
            phase_title,
            "phase_done",
            message or phase_title,
            current=current,
            total=total,
            unit=unit,
            path=path,
            phase_percent=100.0 if total is not None else None,
            overall_percent=self._completed_percent(),
            force=True,
        )

    def _emit(
        self,
        key: str,
        phase_title: str,
        event_type: str,
        message: str,
        *,
        current: int | None = None,
        total: int | None = None,
        unit: str | None = None,
        path: str | None = None,
        phase_percent: float | None = None,
        overall_percent: float | None = None,
        force: bool = False,
    ) -> None:
        if self._callback is None:
            return
        if phase_percent is None and total and total > 0 and current is not None:
            phase_percent = min(100.0, max(0.0, current * 100.0 / total))
        if overall_percent is None and phase_percent is not None:
            completed_before = self._completed_percent(excluding=key)
            overall_percent = completed_before + self._weights.get(key, 0.0) * phase_percent / 100.0
        if overall_percent is not None:
            overall_percent = min(100.0, max(self._last_overall, round(overall_percent, 2)))
            self._last_overall = overall_percent
        self._callback(
            ProgressEvent(
                time=datetime.now().strftime("%H:%M:%S"),
                level="Инфо",
                stage=phase_title,
                message=message,
                path=path,
                phase_key=key,
                phase_title=phase_title,
                event_type=event_type,
                current=current,
                total=total,
                unit=unit,
                phase_percent=phase_percent,
                overall_percent=overall_percent,
            )
        )

    def _completed_percent(self, *, excluding: str | None = None) -> float:
        return sum(
            weight
            for key, weight in self._weights.items()
            if key in self._completed and key != excluding
        )
