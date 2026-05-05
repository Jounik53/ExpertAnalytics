from __future__ import annotations

from datetime import datetime
from pathlib import Path

import psutil

from app.domain.entities import ServiceRecord
from app.domain.ports import ServiceScannerPort


class PsutilServiceScanner(ServiceScannerPort):
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
                record = ServiceRecord(
                    name=str(info.get("name") or ""),
                    display_name=str(info.get("display_name") or ""),
                    status=str(info.get("status") or "unknown"),
                    start_type=str(info.get("start_type") or "unknown"),
                    executable_path=str(info.get("binpath") or ""),
                    pid=info.get("pid"),
                    signer=self._guess_signer(str(info.get("binpath") or "")),
                    file_version="",
                    file_date=self._file_date(str(info.get("binpath") or "")),
                    risk_score=self._risk_score(str(info.get("name") or ""), str(info.get("binpath") or "")),
                    risk_reason=self._risk_reason(str(info.get("name") or ""), str(info.get("binpath") or "")),
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
                    item_callback(
                        f"Служба: {record.name} | статус: {record.status} | PID: {record.pid or '-'} | память: {mem_text} | риск: {record.risk_score} | путь: {record.executable_path or '-'}"
                    )
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
        records.sort(key=lambda x: x.name.lower())
        return records

    def _risk_score(self, name: str, path: str) -> int:
        p = path.lower()
        score = 0
        if "appdata" in p or "temp" in p:
            score += 50
        if "windows\\system32" not in p and "program files" not in p:
            score += 20
        if any(x in name.lower() for x in ["update", "helper", "host"]):
            score += 10
        return min(100, score)

    def _risk_reason(self, name: str, path: str) -> str:
        reasons = []
        lp = path.lower()
        if "appdata" in lp or "temp" in lp:
            reasons.append("service binary in user-writable path")
        if "windows\\system32" not in lp and "program files" not in lp:
            reasons.append("non-standard service path")
        if any(x in name.lower() for x in ["update", "helper", "host"]):
            reasons.append("generic service naming")
        return ", ".join(reasons)

    def _guess_signer(self, path: str) -> str:
        lp = path.lower()
        if "windows\\system32" in lp:
            return "Microsoft (assumed)"
        return "unknown"

    def _file_date(self, path: str) -> str:
        cleaned = path.strip('"').split(" ")[0]
        p = Path(cleaned)
        if not p.exists():
            return ""
        try:
            return datetime.fromtimestamp(p.stat().st_mtime).isoformat()
        except OSError:
            return ""
