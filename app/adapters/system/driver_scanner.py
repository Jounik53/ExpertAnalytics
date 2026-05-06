from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from app.domain.entities import DriverRecord
from app.domain.ports import DriverScannerPort


class WindowsDriverScanner(DriverScannerPort):
    def estimate_driver_count(self) -> int:
        try:
            return len(self.scan_drivers())
        except Exception:
            return 0

    def scan_drivers(self, item_callback=None) -> list[DriverRecord]:
        script = (
            "$drivers = Get-CimInstance Win32_SystemDriver -ErrorAction SilentlyContinue | ForEach-Object {"
            "  [PSCustomObject]@{"
            "    Name=[string]$_.Name;"
            "    DisplayName=[string]$_.DisplayName;"
            "    State=[string]$_.State;"
            "    StartMode=[string]$_.StartMode;"
            "    PathName=[string]$_.PathName"
            "  }"
            "};"
            "$drivers | ConvertTo-Json -Compress"
        )
        rows = self._run_powershell_json(script)
        result: list[DriverRecord] = []
        for row in rows:
            name = str(row.get("Name") or "")
            display_name = str(row.get("DisplayName") or "")
            state = str(row.get("State") or "unknown")
            start_mode = str(row.get("StartMode") or "unknown")
            raw_path = str(row.get("PathName") or "")
            path = self._extract_path(raw_path)
            file_sha = self._sha256(path) if path else ""
            image_size_mb = self._file_size_mb(path) if path else 0.0
            risk_score, risk_reason = self._risk(path, name, start_mode)
            resource_score, resource_reason = self._resource(image_size_mb)

            rec = DriverRecord(
                name=name,
                display_name=display_name,
                state=state,
                start_mode=start_mode,
                executable_path=path,
                file_sha256=file_sha,
                image_size_mb=image_size_mb,
                risk_score=risk_score,
                risk_reason=risk_reason,
                resource_score=resource_score,
                resource_reason=resource_reason,
            )
            result.append(rec)
            if item_callback is not None:
                item_callback(
                    f"Драйвер: {rec.name} | состояние: {rec.state} | запуск: {rec.start_mode} | риск: {rec.risk_score} | размер: {rec.image_size_mb:.2f} МБ | путь: {rec.executable_path or '-'}"
                )
        result.sort(key=lambda x: x.name.lower())
        return result

    @staticmethod
    def _extract_path(pathname: str) -> str:
        text = (pathname or "").strip()
        if not text:
            return ""
        if text.startswith('"'):
            end = text.find('"', 1)
            if end > 1:
                text = text[1:end]
        else:
            text = text.split(" ")[0]
        if text.startswith(r"\SystemRoot\\"):
            text = text.replace(r"\SystemRoot", r"C:\Windows", 1)
        return text

    @staticmethod
    def _file_size_mb(path: str) -> float:
        p = Path(path)
        if not p.exists() or not p.is_file():
            return 0.0
        try:
            return p.stat().st_size / (1024 * 1024)
        except OSError:
            return 0.0

    @staticmethod
    def _sha256(path: str) -> str:
        p = Path(path)
        if not p.exists() or not p.is_file():
            return ""
        h = hashlib.sha256()
        try:
            with p.open("rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            return h.hexdigest()
        except OSError:
            return ""

    @staticmethod
    def _risk(path: str, name: str, start_mode: str) -> tuple[int, str]:
        lp = (path or "").lower()
        ln = (name or "").lower()
        ls = (start_mode or "").lower()
        score = 0
        reasons: list[str] = []
        if lp and ("appdata" in lp or "temp" in lp):
            score += 60
            reasons.append("driver in user-writable path")
        if lp and "windows\\system32\\drivers" not in lp:
            score += 20
            reasons.append("non-standard driver path")
        if ls in {"auto", "boot", "system"}:
            score += 10
            reasons.append("early auto-start")
        if any(x in ln for x in ["helper", "update", "host", "monitor"]):
            score += 10
            reasons.append("generic naming")
        return min(100, score), ", ".join(reasons)

    @staticmethod
    def _resource(size_mb: float) -> tuple[int, str]:
        if size_mb >= 25:
            return 80, "large driver image"
        if size_mb >= 12:
            return 45, "medium-large driver image"
        if size_mb >= 6:
            return 20, "noticeable image size"
        return 0, ""

    @staticmethod
    def _run_powershell_json(script: str) -> list[dict]:
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                capture_output=True,
                text=True,
                shell=False,
                timeout=10000,
            )
        except Exception:
            return []
        if proc.returncode != 0:
            return []
        out = (proc.stdout or "").strip()
        if not out:
            return []
        import json

        try:
            parsed = json.loads(out)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [x for x in parsed if isinstance(x, dict)]
        if isinstance(parsed, dict):
            return [parsed]
        return []
