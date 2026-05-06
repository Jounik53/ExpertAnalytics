from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import psutil

from app.domain.entities import ServiceRecord
from app.domain.ports import ServiceScannerPort


class PsutilServiceScanner(ServiceScannerPort):
    def __init__(self, trusted_db_path: Path | None = None) -> None:
        default_path = Path(__file__).resolve().parents[2] / "data" / "trusted_windows_services.json"
        self._trusted_db_path = trusted_db_path or default_path
        self._trusted_db = self._load_trusted_db(self._trusted_db_path)

    def estimate_service_count(self) -> int:
        try:
            return sum(1 for _ in psutil.win_service_iter())
        except Exception:
            return 0

    def scan_services(self, item_callback=None) -> list[ServiceRecord]:
        records: list[ServiceRecord] = []
        for service in psutil.win_service_iter():
            try:
                info = service.as_dict()
                raw_bin = str(info.get("binpath") or "")
                exe_path = self._extract_exe_path(raw_bin)
                file_sha = self._sha256(exe_path) if exe_path else ""
                trusted, trust_reason = self._is_trusted(
                    name=str(info.get("name") or ""),
                    display_name=str(info.get("display_name") or ""),
                    path=exe_path,
                    file_sha256=file_sha,
                )

                risk_score = self._risk_score(str(info.get("name") or ""), exe_path, trusted)
                risk_reason = self._risk_reason(str(info.get("name") or ""), exe_path, trusted)

                record = ServiceRecord(
                    name=str(info.get("name") or ""),
                    display_name=str(info.get("display_name") or ""),
                    status=str(info.get("status") or "unknown"),
                    start_type=str(info.get("start_type") or "unknown"),
                    executable_path=exe_path,
                    pid=info.get("pid"),
                    signer=self._guess_signer(exe_path),
                    file_version="",
                    file_date=self._file_date(exe_path),
                    file_sha256=file_sha,
                    trusted=trusted,
                    trust_reason=trust_reason,
                    risk_score=risk_score,
                    risk_reason=risk_reason,
                )
                records.append(record)
                if item_callback is not None:
                    mem_text = "-"
                    if record.pid:
                        try:
                            mem_mb = psutil.Process(int(record.pid)).memory_info().rss / (1024 * 1024)
                            mem_text = f"{mem_mb:.1f} МБ"
                        except Exception:
                            mem_text = "н/д"
                    trusted_text = "trusted" if record.trusted else "untrusted"
                    item_callback(
                        f"Служба: {record.name} | статус: {record.status} | PID: {record.pid or '-'} | память: {mem_text} | риск: {record.risk_score} | {trusted_text} | путь: {record.executable_path or '-'}"
                    )
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
        records.sort(key=lambda x: x.name.lower())
        return records

    @staticmethod
    def _extract_exe_path(binpath: str) -> str:
        text = (binpath or "").strip()
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
    def _load_trusted_db(path: Path) -> dict:
        if not path.exists():
            return {"services": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            return {"services": []}
        return {"services": []}

    def _is_trusted(self, name: str, display_name: str, path: str, file_sha256: str) -> tuple[bool, str]:
        items = self._trusted_db.get("services", [])
        lname = (name or "").strip().lower()
        ldisp = (display_name or "").strip().lower()
        lpath = (path or "").strip().lower()
        lsha = (file_sha256 or "").strip().lower()

        for item in items:
            if not isinstance(item, dict):
                continue
            names = [str(x).strip().lower() for x in item.get("names", []) if str(x).strip()]
            displays = [str(x).strip().lower() for x in item.get("display_names", []) if str(x).strip()]
            paths = [str(x).strip().lower() for x in item.get("paths", []) if str(x).strip()]
            hashes = [str(x).strip().lower() for x in item.get("sha256", []) if str(x).strip()]

            name_ok = not names or lname in names
            display_ok = not displays or ldisp in displays
            path_ok = not paths or lpath in paths
            hash_ok = not hashes or (lsha and lsha in hashes)

            if name_ok and display_ok and path_ok and hash_ok:
                reason = []
                if names:
                    reason.append("name")
                if paths:
                    reason.append("path")
                if hashes:
                    reason.append("hash")
                return True, "trusted by " + "+".join(reason or ["policy"])
        return False, "not in trusted services list"

    def _risk_score(self, name: str, path: str, trusted: bool) -> int:
        if trusted:
            return 0
        p = (path or "").lower()
        score = 0
        if "appdata" in p or "temp" in p:
            score += 50
        if "windows\\system32" not in p and "program files" not in p:
            score += 20
        if any(x in name.lower() for x in ["update", "helper", "host"]):
            score += 10
        return min(100, score)

    def _risk_reason(self, name: str, path: str, trusted: bool) -> str:
        if trusted:
            return "trusted system service"
        reasons = []
        lp = (path or "").lower()
        if "appdata" in lp or "temp" in lp:
            reasons.append("service binary in user-writable path")
        if "windows\\system32" not in lp and "program files" not in lp:
            reasons.append("non-standard service path")
        if any(x in name.lower() for x in ["update", "helper", "host"]):
            reasons.append("generic service naming")
        return ", ".join(reasons)

    @staticmethod
    def _guess_signer(path: str) -> str:
        lp = (path or "").lower()
        if "windows\\system32" in lp:
            return "Microsoft (assumed)"
        return "unknown"

    @staticmethod
    def _file_date(path: str) -> str:
        p = Path(path)
        if not p.exists():
            return ""
        try:
            return datetime.fromtimestamp(p.stat().st_mtime).isoformat()
        except OSError:
            return ""
