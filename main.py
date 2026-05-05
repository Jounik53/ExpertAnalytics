from app.bootstrap import build_app
import ctypes
import os
import sys


def main() -> None:
    if os.name == "nt" and not _is_admin():
        if _relaunch_as_admin():
            return
    app = build_app()
    app.run()


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _relaunch_as_admin() -> bool:
    try:
        params = " ".join(f'"{arg}"' for arg in sys.argv)
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        return int(rc) > 32
    except Exception:
        return False


if __name__ == "__main__":
    main()
