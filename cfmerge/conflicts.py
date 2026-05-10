from __future__ import annotations


class MergeConflict(RuntimeError):
    def __init__(self, code: str, path: str, details: str = "", method: str | None = None, context: dict | None = None):
        super().__init__(f"{code}: {path}: {details}")
        self.code = code
        self.path = path
        self.details = details
        self.method = method
        self.context = context or {}
