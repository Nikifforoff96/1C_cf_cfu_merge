from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class MergeConfig:
    cf_dir: Path
    cfu_dir: Path
    out_dir: Path
    report_path: Path | None = None
    human_report_path: Path | None = None
    dry_run: bool = False
    force: bool = False
    backup: bool = False
    conflict_strategy: str = "fail"
    encoding_policy: str = "auto"
    line_endings: str = "preserve"
    preserve_formatting: bool = True
    validate_xml: bool = False
    validate_bsl: bool = False
    validate_1c: bool = False
    v8_path: Path | None = None
    infobase_path: Path | None = None
    verbose: bool = False
    unsafe_text_merge: bool = False
    fail_on_conflict: bool = False


@dataclass(slots=True)
class FileRecord:
    rel_path: str
    abs_path: Path
    kind: str
    encoding: str
    newline: str
    sha256: str
    object_type: str | None = None
    object_name: str | None = None


@dataclass(slots=True)
class Parameter:
    raw: str
    name: str
    default: str | None = None
    by_value: bool = False


@dataclass(slots=True)
class BslMethod:
    local_name: str
    target_name: str | None
    kind: str
    async_method: bool
    params: list[Parameter]
    export: bool
    compile_directives: list[str]
    extension_annotation: str | None
    raw_text: str
    body_text: str
    start_offset: int
    end_offset: int
    header_start: int
    body_start: int
    body_end: int
    footer_start: int
    header_text: str
    footer_text: str


@dataclass(slots=True)
class BslModule:
    text: str
    methods: list[BslMethod]


@dataclass(slots=True)
class ConflictRecord:
    code: str
    severity: str
    path: str
    details: str
    object_type: str | None = None
    object_name: str | None = None
    method: str | None = None
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MergeAction:
    path: str
    strategy: str
    object_type: str | None = None
    object_name: str | None = None
    details: str | None = None


@dataclass(slots=True)
class ValidationResult:
    name: str
    status: str
    command: str | None = None
    output: str | None = None


@dataclass
class MergeReport:
    status: str = "completed"
    input: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, int] = field(default_factory=lambda: {
        "files_scanned_cf": 0,
        "files_scanned_cfu": 0,
        "files_added": 0,
        "files_changed": 0,
        "files_copied": 0,
        "files_skipped": 0,
        "conflicts": 0,
        "warnings": 0,
    })
    objects: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {
        "added": [],
        "modified": [],
        "skipped": [],
    })
    conflicts: list[ConflictRecord] = field(default_factory=list)
    warnings: list[ConflictRecord] = field(default_factory=list)
    validation: dict[str, str] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    actions: list[MergeAction] = field(default_factory=list)

    def add_action(self, action: MergeAction) -> None:
        self.actions.append(action)

    def add_warning(self, code: str, path: str, details: str = "", **kwargs: Any) -> None:
        self.warnings.append(ConflictRecord(code=code, severity="warning", path=path, details=details, **kwargs))
        self.summary["warnings"] = len(self.warnings)
        if self.status == "completed":
            self.status = "completed_with_warnings"

    def add_conflict(self, code: str, path: str, details: str = "", severity: str = "error", **kwargs: Any) -> None:
        self.conflicts.append(ConflictRecord(code=code, severity=severity, path=path, details=details, **kwargs))
        self.summary["conflicts"] = len(self.conflicts)
        self.status = "failed" if severity == "error" else "completed_with_warnings"
