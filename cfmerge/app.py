from __future__ import annotations

import ctypes
import sys
import traceback


APP_TITLE = "1C_cf_cfu_merge"


def _show_startup_error(message: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, message, APP_TITLE, 0x10)
    except Exception:
        print(message, file=sys.stderr)


def main() -> int:
    try:
        from cfmerge.gui import main as gui_main

        return gui_main()
    except Exception as exc:
        details = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        _show_startup_error(f"Не удалось запустить приложение.\n\n{details}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
