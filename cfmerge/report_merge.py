from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import re

from .io_utils import detect_encoding_and_newline, read_text, write_text
from .models import MergeReport


HEADER_RE = re.compile(r"^(?P<indent>\t*)- (?P<full_name>.+?)\s*$")
OWNERSHIP_RE = re.compile(r'^\s*ПринадлежностьОбъекта:\s*"([^"]+)"')


@dataclass(slots=True)
class ReportBlock:
    header_line: str
    property_lines: list[str] = field(default_factory=list)
    children: list["ReportBlock"] = field(default_factory=list)
    full_name: str = ""
    report_kind: str = ""
    indent_level: int = 0
    owning_path: str = ""

    @property
    def object_belonging(self) -> str | None:
        for line in self.property_lines:
            match = OWNERSHIP_RE.match(line)
            if match:
                return match.group(1)
        return None

    @property
    def is_own(self) -> bool:
        return self.object_belonging == "Собственный"


@dataclass(slots=True)
class ReportDocument:
    prefix_lines: list[str] = field(default_factory=list)
    blocks: list[ReportBlock] = field(default_factory=list)


def _lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    text = read_text(path)
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _report_kind(full_name: str) -> str:
    parts = [part for part in full_name.split(".") if part]
    if len(parts) >= 2:
        return parts[-2]
    return full_name


def _owning_path(full_name: str) -> str:
    parts = [part for part in full_name.split(".") if part]
    if len(parts) <= 2:
        return ""
    return ".".join(parts[:-2])


def _make_block(header_line: str) -> ReportBlock:
    match = HEADER_RE.match(header_line)
    if not match:
        raise ValueError(f"Invalid report header line: {header_line!r}")
    full_name = match.group("full_name")
    return ReportBlock(
        header_line=header_line,
        full_name=full_name,
        report_kind=_report_kind(full_name),
        indent_level=len(match.group("indent")),
        owning_path=_owning_path(full_name),
    )


def parse_report_text(text: str) -> ReportDocument:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    document = ReportDocument()
    stack: list[ReportBlock] = []
    current: ReportBlock | None = None
    for line in lines:
        match = HEADER_RE.match(line)
        if match:
            block = _make_block(line)
            while stack and stack[-1].indent_level >= block.indent_level:
                stack.pop()
            if stack:
                stack[-1].children.append(block)
            else:
                document.blocks.append(block)
            stack.append(block)
            current = block
            continue
        if current is None:
            document.prefix_lines.append(line)
        else:
            current.property_lines.append(line)
    return document


def serialize_report_text(document: ReportDocument) -> str:
    lines: list[str] = []
    lines.extend(document.prefix_lines)
    for block in document.blocks:
        _append_block_lines(lines, block)
    return "\n".join(lines).rstrip("\n") + "\n"


def _append_block_lines(lines: list[str], block: ReportBlock) -> None:
    lines.append(block.header_line)
    lines.extend(block.property_lines)
    for child in block.children:
        _append_block_lines(lines, child)


def _walk_blocks(blocks: list[ReportBlock]) -> list[ReportBlock]:
    result: list[ReportBlock] = []
    for block in blocks:
        result.append(block)
        result.extend(_walk_blocks(block.children))
    return result


def _build_block_index(blocks: list[ReportBlock]) -> dict[str, ReportBlock]:
    return {block.full_name: block for block in _walk_blocks(blocks)}


def _grandparent_path(full_name: str) -> str:
    return _owning_path(full_name)


def _collect_extension_insertions(ext_root: ReportBlock, hints: set[str], report: MergeReport) -> list[ReportBlock]:
    use_hints = bool(hints)
    eligible: dict[str, ReportBlock] = {}
    for block in _walk_blocks(ext_root.children):
        hinted = block.full_name in hints if use_hints else False
        if use_hints and hinted and not block.is_own:
            report.add_warning(
                "CONFIGURATION_REPORT_HINT_NOT_OWN",
                "ОтчетПоКонфигурации.txt",
                f"Объект есть в СобственныеОбъекты.txt, но в отчете не помечен как собственный: {block.full_name}",
            )
        if use_hints and (not hinted) and block.is_own:
            report.add_warning(
                "CONFIGURATION_REPORT_OWN_OBJECT_MISSING_HINT",
                "ОтчетПоКонфигурации.txt",
                f"Объект помечен как собственный в отчете, но отсутствует в СобственныеОбъекты.txt: {block.full_name}",
            )
        if (use_hints and hinted and block.is_own) or ((not use_hints) and block.is_own):
            eligible[block.full_name] = block

    roots: list[ReportBlock] = []
    eligible_names = set(eligible)
    for full_name, block in eligible.items():
        ancestor = block.owning_path
        has_own_ancestor = False
        while ancestor:
            if ancestor in eligible_names:
                has_own_ancestor = True
                break
            ancestor = _grandparent_path(ancestor)
        if not has_own_ancestor:
            roots.append(block)
    return roots


def _merge_sibling_blocks(
    base_children: list[ReportBlock],
    additions: list[ReportBlock],
) -> tuple[list[ReportBlock], int, list[str]]:
    if not additions:
        return list(base_children), 0, []
    grouped: dict[str, list[ReportBlock]] = {}
    ordered_kinds: list[str] = []
    for block in additions:
        if block.report_kind not in grouped:
            grouped[block.report_kind] = []
            ordered_kinds.append(block.report_kind)
        grouped[block.report_kind].append(block)

    last_index_by_kind: dict[str, int] = {}
    for index, block in enumerate(base_children):
        last_index_by_kind[block.report_kind] = index

    merged: list[ReportBlock] = []
    inserted = 0
    consumed: set[str] = set()
    for index, block in enumerate(base_children):
        merged.append(block)
        if last_index_by_kind.get(block.report_kind) == index and block.report_kind in grouped:
            clones = [deepcopy(item) for item in grouped[block.report_kind]]
            merged.extend(clones)
            inserted += len(clones)
            consumed.add(block.report_kind)

    missing_kinds: list[str] = []
    for kind in ordered_kinds:
        if kind in consumed:
            continue
        clones = [deepcopy(item) for item in grouped[kind]]
        merged.extend(clones)
        inserted += len(clones)
        missing_kinds.append(kind)
    return merged, inserted, missing_kinds


def _top_level_insertions(insertion_roots: list[ReportBlock]) -> list[ReportBlock]:
    return [block for block in insertion_roots if not block.owning_path]


def _nested_insertions(insertion_roots: list[ReportBlock]) -> dict[str, list[ReportBlock]]:
    result: dict[str, list[ReportBlock]] = {}
    for block in insertion_roots:
        if not block.owning_path:
            continue
        result.setdefault(block.owning_path, []).append(block)
    return result


def merge_configuration_report(base_path: Path, ext_path: Path, out_path: Path, report: MergeReport) -> None:
    base_lines = _lines(base_path)
    ext_lines = _lines(ext_path)
    encoding, newline = ("utf-16", "lf")
    if base_path.exists():
        encoding, newline = detect_encoding_and_newline(base_path)

    if not base_lines:
        text = "\n".join(ext_lines).rstrip("\n") + ("\n" if ext_lines else "")
        write_text(out_path, text, encoding=encoding, newline=newline)
        report.add_warning("CONFIGURATION_REPORT_MERGED", "ОтчетПоКонфигурации.txt", "Базовый отчет отсутствовал; использован отчет расширения без структурного merge")
        return

    base_doc = parse_report_text("\n".join(base_lines))
    ext_doc = parse_report_text("\n".join(ext_lines))
    if not base_doc.blocks:
        write_text(out_path, "\n".join(base_lines).rstrip("\n") + "\n", encoding=encoding, newline=newline)
        report.add_warning("CONFIGURATION_REPORT_MERGED", "ОтчетПоКонфигурации.txt", "Не удалось выделить блоки в базовом отчете; сохранен base report")
        return

    hints = read_native_object_hints(ext_path.parent)
    result_doc = deepcopy(base_doc)
    base_root = result_doc.blocks[0]

    insertion_roots = []
    if ext_doc.blocks:
        insertion_roots = _collect_extension_insertions(ext_doc.blocks[0], hints, report)

    top_level_insertions = _top_level_insertions(insertion_roots)
    nested_insertions = _nested_insertions(insertion_roots)

    added_top_level = 0
    added_nested = 0
    unresolved_parents = 0

    merged_children, inserted_top, missing_top_kinds = _merge_sibling_blocks(base_root.children, top_level_insertions)
    base_root.children = merged_children
    added_top_level += inserted_top
    for kind in missing_top_kinds:
        report.add_warning(
            "CONFIGURATION_REPORT_KIND_NOT_FOUND_IN_BASE",
            "ОтчетПоКонфигурации.txt",
            f"В базовом отчете не найден верхнеуровневый kind {kind}; собственные блоки расширения добавлены в конец соответствующего уровня",
        )

    block_index = _build_block_index(result_doc.blocks)
    for parent_full_name, blocks in nested_insertions.items():
        parent = block_index.get(parent_full_name)
        if parent is None:
            report.add_conflict(
                "CONFIGURATION_REPORT_PARENT_NOT_FOUND",
                "ОтчетПоКонфигурации.txt",
                f"Не найден родительский блок для собственных объектов расширения: {parent_full_name}",
                severity="manual-review",
            )
            unresolved_parents += len(blocks)
            continue
        merged_children, inserted_nested, missing_kinds = _merge_sibling_blocks(parent.children, blocks)
        parent.children = merged_children
        added_nested += inserted_nested
        for kind in missing_kinds:
            report.add_warning(
                "CONFIGURATION_REPORT_CHILD_KIND_NOT_FOUND_IN_BASE",
                "ОтчетПоКонфигурации.txt",
                f"У родителя {parent_full_name} не найден дочерний kind {kind}; собственные блоки расширения добавлены в конец дочернего уровня",
            )
        block_index = _build_block_index(result_doc.blocks)

    merged_text = serialize_report_text(result_doc)
    write_text(out_path, merged_text, encoding=encoding, newline=newline)
    report.diagnostics["configuration_report_merge"] = {
        "strategy": "structural",
        "added_top_level": added_top_level,
        "added_nested": added_nested,
        "unresolved_parents": unresolved_parents,
    }
    report.add_warning(
        "CONFIGURATION_REPORT_MERGED",
        "ОтчетПоКонфигурации.txt",
        f"Структурный merge отчета выполнен: top_level={added_top_level}, nested={added_nested}, unresolved_parents={unresolved_parents}",
    )


def read_native_object_hints(cfu_dir: Path) -> set[str]:
    hints: set[str] = set()
    own = cfu_dir / "СобственныеОбъекты.txt"
    if own.exists():
        for line in _lines(own):
            line = line.strip()
            if line:
                hints.add(line)
    return hints
