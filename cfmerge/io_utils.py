from __future__ import annotations

from collections.abc import Callable
import hashlib
import os
import shutil
from pathlib import Path


UTF8_BOM = b"\xef\xbb\xbf"
UTF16LE_BOM = b"\xff\xfe"
UTF16BE_BOM = b"\xfe\xff"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_encoding_and_newline(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    if data.startswith(UTF16LE_BOM):
        encoding = "utf-16"
        sample = data.decode("utf-16", errors="ignore")
    elif data.startswith(UTF16BE_BOM):
        encoding = "utf-16"
        sample = data.decode("utf-16", errors="ignore")
    elif data.startswith(UTF8_BOM):
        encoding = "utf-8-sig"
        sample = data.decode("utf-8-sig", errors="ignore")
    else:
        try:
            sample = data.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            sample = data.decode("cp1251", errors="ignore")
            encoding = "cp1251"

    crlf = sample.count("\r\n")
    lf = sample.count("\n") - crlf
    if crlf and not lf:
        newline = "crlf"
    elif lf and not crlf:
        newline = "lf"
    elif crlf:
        newline = "mixed"
    else:
        newline = "none"
    return encoding, newline


def read_text(path: Path, encoding: str | None = None) -> str:
    enc = encoding or detect_encoding_and_newline(path)[0]
    return path.read_text(encoding=enc)


def write_text(path: Path, text: str, encoding: str = "utf-8-sig", newline: str = "crlf") -> None:
    if newline == "crlf":
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    elif newline == "lf":
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding=encoding, newline="")
    os.replace(tmp, path)


def copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def copy_tree_contents(src: Path, dst: Path, progress_callback: Callable[[int, str], None] | None = None) -> None:
    copied = 0
    for path in sorted(src.rglob("*"), key=lambda p: str(p.relative_to(src)).lower()):
        rel = path.relative_to(src)
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            copy_file(path, target)
            copied += 1
            if progress_callback is not None:
                progress_callback(copied, normalize_rel(rel))


def prepare_output_dir(out_dir: Path, force: bool, backup: bool) -> None:
    if out_dir.exists():
        if not force:
            raise FileExistsError(f"Output directory already exists: {out_dir}")
        if backup:
            suffix = 1
            while True:
                candidate = out_dir.with_name(f"{out_dir.name}.bak{suffix}")
                if not candidate.exists():
                    shutil.copytree(out_dir, candidate)
                    break
                suffix += 1
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)


def normalize_rel(path: Path | str) -> str:
    return str(path).replace("\\", "/")
