from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import queue
import threading
from typing import Any

from .merge_engine import merge
from .models import MergeConfig, MergeReport, ProgressEvent

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except Exception:  # pragma: no cover - tkinter can be absent in minimal Python builds.
    tk = None
    filedialog = None
    messagebox = None
    ttk = None


WINDOW_TITLE = "1C_cf_cfu_merge - объединение конфигурациии расширения"
RUN_BUTTON_TEXT = "Выполнить объединение"

STATUS_LABELS = {
    "completed": "завершено",
    "completed_with_warnings": "завершено с предупреждениями",
    "failed": "ошибка",
}

VALIDATION_LABELS = {
    "passed": "пройдена",
    "failed": "ошибка",
}


@dataclass(frozen=True, slots=True)
class GuiLogRow:
    time: str
    level: str
    stage: str
    message: str
    path: str = ""

    def values(self) -> tuple[str, str, str, str, str]:
        return self.time, self.level, self.stage, self.message, self.path


@dataclass(frozen=True, slots=True)
class GuiProgressState:
    title: str
    detail: str
    percent: float | None
    indeterminate: bool


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def resolve_merge_paths(cf_dir: str | Path, cfu_dir: str | Path, out_dir: str | Path) -> tuple[Path, Path, Path]:
    return (
        Path(cf_dir).expanduser().resolve(strict=False),
        Path(cfu_dir).expanduser().resolve(strict=False),
        Path(out_dir).expanduser().resolve(strict=False),
    )


def _same_or_inside(candidate: Path, parent: Path) -> bool:
    try:
        return candidate == parent or candidate.is_relative_to(parent)
    except ValueError:
        return False


def validate_merge_paths(cf_dir: str | Path, cfu_dir: str | Path, out_dir: str | Path) -> list[str]:
    errors: list[str] = []
    raw_cf = str(cf_dir).strip()
    raw_cfu = str(cfu_dir).strip()
    raw_out = str(out_dir).strip()

    if not raw_cf:
        errors.append("Укажите каталог поля \"Основная конфигурация\".")
    if not raw_cfu:
        errors.append("Укажите каталог поля \"Расширение\".")
    if not raw_out:
        errors.append("Укажите каталог поля \"Результат объединения\".")
    if errors:
        return errors

    cf_path, cfu_path, out_path = resolve_merge_paths(raw_cf, raw_cfu, raw_out)

    if not cf_path.is_dir():
        errors.append("Каталог \"Основная конфигурация\" не существует.")
    elif not (cf_path / "Configuration.xml").is_file():
        errors.append("Каталог \"Основная конфигурация\" не содержит Configuration.xml.")

    if not cfu_path.is_dir():
        errors.append("Каталог \"Расширение\" не существует.")
    elif not (cfu_path / "Configuration.xml").is_file():
        errors.append("Каталог \"Расширение\" не содержит Configuration.xml.")

    if out_path.exists() and not out_path.is_dir():
        errors.append("Путь \"Результат объединения\" существует, но не является каталогом.")

    if _same_or_inside(out_path, cf_path):
        errors.append("Каталог результата не должен совпадать с основной конфигурацией или находиться внутри нее.")
    if _same_or_inside(out_path, cfu_path):
        errors.append("Каталог результата не должен совпадать с расширением или находиться внутри него.")
    if _same_or_inside(cf_path, out_path) or _same_or_inside(cfu_path, out_path):
        errors.append("Каталог результата не должен содержать исходные каталоги.")

    return errors


def build_gui_merge_config(
    cf_dir: str | Path,
    cfu_dir: str | Path,
    out_dir: str | Path,
    progress_callback: Callable[[ProgressEvent], None] | None = None,
    validate_1c: bool = False,
) -> MergeConfig:
    cf_path, cfu_path, out_path = resolve_merge_paths(cf_dir, cfu_dir, out_dir)
    return MergeConfig(
        cf_dir=cf_path,
        cfu_dir=cfu_path,
        out_dir=out_path,
        report_path=out_path / "merge-report.json",
        human_report_path=out_path / "merge-report.txt",
        force=True,
        backup=True,
        validate_xml=True,
        validate_bsl=True,
        validate_1c=validate_1c,
        progress_callback=progress_callback,
    )


def progress_event_to_log_row(event: ProgressEvent) -> GuiLogRow:
    return GuiLogRow(
        time=event.time,
        level=event.level,
        stage=event.stage,
        message=event.message,
        path=event.path or "",
    )


def is_progress_event(event: ProgressEvent) -> bool:
    return event.event_type in {"phase_start", "phase_progress", "phase_done"}


def progress_event_to_progress_state(event: ProgressEvent) -> GuiProgressState:
    title = event.phase_title or event.stage
    unit = event.unit or ""
    if event.current is not None and event.total is not None:
        counter = f"{event.current} / {event.total} {unit}".strip()
    elif event.current is not None:
        counter = f"{event.current} {unit}".strip()
    else:
        counter = ""
    percent_text = f"{event.overall_percent:.0f}%" if event.overall_percent is not None else ""
    detail = "  ".join(part for part in (counter, percent_text) if part)
    return GuiProgressState(
        title=title,
        detail=detail,
        percent=event.overall_percent,
        indeterminate=event.total is None and event.event_type != "phase_done",
    )


def _status_level(status: str) -> str:
    if status == "failed":
        return "Ошибка"
    if status == "completed_with_warnings":
        return "Предупреждение"
    return "Успех"


def merge_report_to_log_rows(report: MergeReport, time_value: str | None = None) -> list[GuiLogRow]:
    stamp = time_value or _now()
    rows = [
        GuiLogRow(stamp, _status_level(report.status), "Итог", f"Статус: {STATUS_LABELS.get(report.status, report.status)}"),
        GuiLogRow(
            stamp,
            "Инфо",
            "Итог",
            (
                f"Файлов cf: {report.summary.get('files_scanned_cf', 0)}; "
                f"cfu: {report.summary.get('files_scanned_cfu', 0)}; "
                f"скопировано: {report.summary.get('files_copied', 0)}"
            ),
        ),
        GuiLogRow(
            stamp,
            "Инфо",
            "Итог",
            (
                f"Добавлено: {report.summary.get('files_added', 0)}; "
                f"изменено: {report.summary.get('files_changed', 0)}; "
                f"пропущено: {report.summary.get('files_skipped', 0)}"
            ),
        ),
        GuiLogRow(
            stamp,
            "Инфо",
            "Итог",
            (
                f"Предупреждений: {report.summary.get('warnings', 0)}; "
                f"конфликтов: {report.summary.get('conflicts', 0)}"
            ),
        ),
    ]

    for item in report.warnings[:200]:
        details = f": {item.details}" if item.details else ""
        rows.append(GuiLogRow(stamp, "Предупреждение", "Предупреждения", f"{item.code}{details}", item.path))
    if len(report.warnings) > 200:
        rows.append(GuiLogRow(stamp, "Предупреждение", "Предупреждения", f"Показано 200 из {len(report.warnings)} предупреждений."))

    for item in report.conflicts:
        details = f": {item.details}" if item.details else ""
        rows.append(GuiLogRow(stamp, "Ошибка", "Конфликты", f"{item.code}{details}", item.path))

    for name, status in report.validation.items():
        level = "Успех" if status == "passed" else "Ошибка"
        rows.append(GuiLogRow(stamp, level, "Валидация", f"{name}: {VALIDATION_LABELS.get(status, status)}"))

    return rows


class CfMergeGui:
    def __init__(self) -> None:
        if tk is None or ttk is None or filedialog is None or messagebox is None:
            raise RuntimeError("Tkinter недоступен в текущей установке Python.")

        self.root = tk.Tk()
        self.root.title(WINDOW_TITLE)
        self.root.geometry("1120x650")
        self.root.minsize(900, 520)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.cf_var = tk.StringVar()
        self.cfu_var = tk.StringVar()
        self.out_var = tk.StringVar()
        self.validate_1c_var = tk.BooleanVar(value=False)
        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress_stage_var = tk.StringVar(value="Ожидание")
        self.progress_detail_var = tk.StringVar(value="")
        self._progress_indeterminate = False
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._running = False
        self._busy_widgets: list[Any] = []

        self._configure_style()
        self._build_layout()
        self.root.after(100, self._poll_queue)

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TButton", padding=(10, 6))
        style.configure("TEntry", padding=(4, 4))
        style.configure("Treeview", rowheight=24)

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        main = ttk.Frame(self.root, padding=12)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(1, weight=1)
        main.rowconfigure(6, weight=1)

        self._add_path_row(main, 0, "Основная конфигурация", self.cf_var)
        self._add_path_row(main, 1, "Расширение", self.cfu_var)
        self._add_path_row(main, 2, "Результат объединения", self.out_var)

        self.validate_1c_check = ttk.Checkbutton(main, text="Валидация 1С", variable=self.validate_1c_var)
        self.validate_1c_check.grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self._busy_widgets.append(self.validate_1c_check)

        self.run_button = ttk.Button(main, text=RUN_BUTTON_TEXT, command=self._start_merge)
        self.run_button.grid(row=4, column=0, columnspan=3, sticky="w", pady=(12, 12))
        self._busy_widgets.append(self.run_button)

        self._build_progress_panel(main)
        self._build_log_table(main)

    def _add_path_row(self, parent: Any, row: int, label_text: str, variable: Any) -> None:
        label = ttk.Label(parent, text=label_text)
        label.grid(row=row, column=0, sticky="w", pady=4, padx=(0, 10))

        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=4)

        button = ttk.Button(parent, text="Выбрать...", command=lambda: self._select_directory(variable, label_text))
        button.grid(row=row, column=2, sticky="e", pady=4, padx=(10, 0))
        self._busy_widgets.extend([entry, button])

    def _build_progress_panel(self, parent: Any) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        frame.columnconfigure(0, weight=1)

        self.progress_stage_label = ttk.Label(frame, textvariable=self.progress_stage_var)
        self.progress_stage_label.grid(row=0, column=0, sticky="w")

        self.progress_detail_label = ttk.Label(frame, textvariable=self.progress_detail_var, anchor="e")
        self.progress_detail_label.grid(row=0, column=1, sticky="e", padx=(12, 0))

        self.progress_bar = ttk.Progressbar(frame, mode="determinate", maximum=100, variable=self.progress_var)
        self.progress_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))

    def _build_log_table(self, parent: Any) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=6, column=0, columnspan=3, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        columns = ("time", "level", "stage", "message", "path")
        self.log_table = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "time": "Время",
            "level": "Уровень",
            "stage": "Этап",
            "message": "Сообщение",
            "path": "Путь/объект",
        }
        widths = {
            "time": 80,
            "level": 120,
            "stage": 180,
            "message": 420,
            "path": 300,
        }
        for column in columns:
            self.log_table.heading(column, text=headings[column])
            self.log_table.column(column, width=widths[column], minwidth=60, stretch=column in {"message", "path"})

        y_scroll = ttk.Scrollbar(frame, orient="vertical", command=self.log_table.yview)
        x_scroll = ttk.Scrollbar(frame, orient="horizontal", command=self.log_table.xview)
        self.log_table.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        self.log_table.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")

        self.log_table.tag_configure("Ошибка", foreground="#b00020")
        self.log_table.tag_configure("Предупреждение", foreground="#8a5a00")
        self.log_table.tag_configure("Успех", foreground="#0b6b28")

    def _select_directory(self, variable: Any, title: str) -> None:
        current = variable.get().strip()
        initial_dir = current if current and Path(current).exists() else str(Path.cwd())
        selected = filedialog.askdirectory(parent=self.root, title=title, initialdir=initial_dir)
        if selected:
            variable.set(selected)

    def _start_merge(self) -> None:
        if self._running:
            return
        self._clear_log()
        self._reset_progress("Ожидание")
        errors = validate_merge_paths(self.cf_var.get(), self.cfu_var.get(), self.out_var.get())
        if errors:
            for error in errors:
                self._insert_row(GuiLogRow(_now(), "Ошибка", "Проверка входов", error))
            messagebox.showerror("Ошибка заполнения", "\n".join(errors), parent=self.root)
            return

        self._running = True
        self._set_busy(True)
        self._reset_progress("Объединение запущено")
        self._insert_row(GuiLogRow(_now(), "Инфо", "Старт", "Объединение запущено"))

        cfg = build_gui_merge_config(
            self.cf_var.get(),
            self.cfu_var.get(),
            self.out_var.get(),
            progress_callback=lambda event: self._queue.put(("event", event)),
            validate_1c=self.validate_1c_var.get(),
        )
        worker = threading.Thread(target=self._run_merge_worker, args=(cfg,), daemon=True)
        worker.start()

    def _run_merge_worker(self, cfg: MergeConfig) -> None:
        try:
            report = merge(cfg)
        except Exception as exc:
            self._queue.put(("error", exc))
        else:
            self._queue.put(("report", report))
        finally:
            self._queue.put(("done", None))

    def _poll_queue(self) -> None:
        while True:
            try:
                kind, payload = self._queue.get_nowait()
            except queue.Empty:
                break

            if kind == "event":
                if is_progress_event(payload):
                    self._apply_progress_event(payload)
                else:
                    self._insert_row(progress_event_to_log_row(payload))
            elif kind == "report":
                for row in merge_report_to_log_rows(payload):
                    self._insert_row(row)
            elif kind == "error":
                message = str(payload) or payload.__class__.__name__
                self._stop_progress_indeterminate()
                self.progress_stage_var.set("Ошибка выполнения")
                self.progress_detail_var.set("")
                self._insert_row(GuiLogRow(_now(), "Ошибка", "Ошибка выполнения", message))
                messagebox.showerror("Ошибка выполнения", message, parent=self.root)
            elif kind == "done":
                self._stop_progress_indeterminate()
                self._running = False
                self._set_busy(False)

        self.root.after(100, self._poll_queue)

    def _insert_row(self, row: GuiLogRow) -> None:
        self.log_table.insert("", "end", values=row.values(), tags=(row.level,))
        self.log_table.yview_moveto(1.0)

    def _clear_log(self) -> None:
        for item in self.log_table.get_children():
            self.log_table.delete(item)

    def _reset_progress(self, title: str) -> None:
        self._stop_progress_indeterminate()
        self.progress_bar.configure(mode="determinate")
        self.progress_var.set(0.0)
        self.progress_stage_var.set(title)
        self.progress_detail_var.set("")

    def _apply_progress_event(self, event: ProgressEvent) -> None:
        state = progress_event_to_progress_state(event)
        self.progress_stage_var.set(state.title)
        self.progress_detail_var.set(state.detail)
        if state.indeterminate:
            if not self._progress_indeterminate:
                self.progress_bar.configure(mode="indeterminate")
                self.progress_bar.start(50)
                self._progress_indeterminate = True
            return
        self._stop_progress_indeterminate()
        self.progress_bar.configure(mode="determinate")
        if state.percent is not None:
            self.progress_var.set(state.percent)

    def _stop_progress_indeterminate(self) -> None:
        if self._progress_indeterminate:
            self.progress_bar.stop()
            self._progress_indeterminate = False

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for widget in self._busy_widgets:
            widget.configure(state=state)

    def _on_close(self) -> None:
        if self._running and not messagebox.askyesno("Закрытие", "Объединение еще выполняется. Закрыть окно?", parent=self.root):
            return
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    app = CfMergeGui()
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
