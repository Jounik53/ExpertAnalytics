from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path


class ExeBuildService:
    def build(self, project_root: Path, progress_callback: Callable[[int, str], None] | None = None) -> tuple[bool, str]:
        def _emit(percent: int, message: str) -> None:
            if progress_callback is not None:
                progress_callback(max(0, min(100, int(percent))), message)

        cmd = [
            "pyinstaller",
            "--noconfirm",
            "--windowed",
            "--name",
            "ExpertAnalytics",
            "main.py",
        ]
        try:
            _emit(3, "Запуск PyInstaller")
            proc = subprocess.Popen(
                cmd,
                cwd=str(project_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            latest = ""
            if proc.stdout is not None:
                for raw in proc.stdout:
                    line = (raw or "").strip()
                    if not line:
                        continue
                    latest = line
                    lower = line.lower()
                    if "analyzing" in lower:
                        _emit(20, "Анализ файлов")
                    elif "building" in lower and "pyz" in lower:
                        _emit(45, "Сборка PYZ")
                    elif "building" in lower and "exe" in lower:
                        _emit(70, "Формирование EXE")
                    elif "collect" in lower:
                        _emit(90, "Финализация сборки")
                    else:
                        _emit(12, line)
            ret = proc.wait()
            if ret == 0:
                _emit(100, "Сборка завершена")
                return True, "dist/ExpertAnalytics/ExpertAnalytics.exe"
            _emit(100, "Сборка завершилась с ошибкой")
            return False, latest or "Ошибка сборки EXE"
        except Exception as exc:
            _emit(100, "Ошибка запуска сборки")
            return False, str(exc)
