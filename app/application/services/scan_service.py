from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Callable
import logging

from app.domain.entities import ScanSnapshot
from app.application.services.heuristics_service import HeuristicEngine
from app.domain.ports import (
    DiagnosticsPort,
    ProcessScannerPort,
    ReportRepositoryPort,
    SamplerPort,
    ServiceScannerPort,
    StartupScannerPort,
    VirusTotalPort,
)


logger = logging.getLogger(__name__)


class ScanService:
    def __init__(
        self,
        process_scanner: ProcessScannerPort,
        startup_scanner: StartupScannerPort,
        service_scanner: ServiceScannerPort,
        vt: VirusTotalPort,
        reports: ReportRepositoryPort,
        diagnostics: DiagnosticsPort | None = None,
        sampler: SamplerPort | None = None,
    ) -> None:
        self._process_scanner = process_scanner
        self._startup_scanner = startup_scanner
        self._service_scanner = service_scanner
        self._vt = vt
        self._reports = reports
        self._diagnostics = diagnostics
        self._sampler = sampler
        self._heuristics = HeuristicEngine()

    def full_scan(
        self,
        high_mb: int,
        medium_mb: int,
        with_vt: bool,
        enable_process_module: bool = True,
        enable_startup_module: bool = True,
        enable_services_module: bool = True,
        sampling_points: int = 0,
        sampling_interval_sec: int = 3,
        max_workers: int = 4,
        cancel_check=lambda: False,
        pause_check=lambda: None,
        progress_callback: Callable[[int, int, str], None] | None = None,
        report_callback: Callable[[str], None] | None = None,
        process_record_callback: Callable[[object], None] | None = None,
        heuristics_enabled: bool = True,
        heuristic_rules: dict[str, bool] | None = None,
    ) -> tuple[ScanSnapshot, str]:
        def _emit_progress(current: int, total: int, message: str) -> None:
            if progress_callback is not None:
                progress_callback(max(0, int(current)), max(1, int(total)), message)

        def _emit_report(message: str) -> None:
            if report_callback is not None and message:
                report_callback(message)

        proc_estimate = self._safe_estimate(self._process_scanner, "estimate_process_count") if enable_process_module else 0
        startup_estimate = self._safe_estimate(self._startup_scanner, "estimate_startup_count") if enable_startup_module else 0
        service_estimate = self._safe_estimate(self._service_scanner, "estimate_service_count") if enable_services_module else 0
        vt_estimate = proc_estimate if with_vt else 0
        heuristic_rules_count = sum(1 for _ in self._heuristics.rules) if heuristics_enabled and enable_process_module else 0
        heur_estimate = proc_estimate * heuristic_rules_count

        total_units = max(10, 2 + proc_estimate + startup_estimate + service_estimate + vt_estimate + heur_estimate + 5)
        current_units = 0

        def _step(message: str, units: int = 1) -> None:
            nonlocal current_units
            current_units = min(total_units, current_units + max(1, units))
            _emit_progress(current_units, total_units, message)

        _step("Подготовка к сканированию", 1)
        process_records = []
        startup_entries = []
        service_records = []

        if enable_process_module:
            pause_check()
            if cancel_check():
                _emit_progress(total_units, total_units, "Сканирование остановлено")
                return self._build_and_save_snapshot(total_ram=0.0, used_ram=0.0, process_records=[], startup_entries=[], service_records=[], sampling_points=[])
            try:
                _step("Сканирование процессов", 1)

                process_progress = {"done": 0}

                def _proc_item(message: str) -> None:
                    process_progress["done"] += 1
                    _step(f"Сканирование процессов ({process_progress['done']})")
                    _emit_report(message)

                process_records = self._scan_processes_with_callback(high_mb, medium_mb, _proc_item, process_record_callback)
            except Exception as exc:
                logger.exception("Process scan failed: %s", exc)

        if enable_startup_module:
            pause_check()
            if cancel_check():
                _emit_progress(total_units, total_units, "Сканирование остановлено")
                return self._build_and_save_snapshot(total_ram=0.0, used_ram=0.0, process_records=process_records, startup_entries=[], service_records=[], sampling_points=[])
            try:
                _step("Сканирование автозагрузки", 1)

                startup_progress = {"done": 0}

                def _startup_item(message: str) -> None:
                    startup_progress["done"] += 1
                    _step(f"Сканирование автозагрузки ({startup_progress['done']})")
                    _emit_report(message)

                startup_entries = self._scan_startup_with_callback(_startup_item)
            except Exception as exc:
                logger.exception("Startup scan failed: %s", exc)

        if enable_services_module:
            pause_check()
            if cancel_check():
                _emit_progress(total_units, total_units, "Сканирование остановлено")
                return self._build_and_save_snapshot(total_ram=0.0, used_ram=0.0, process_records=process_records, startup_entries=startup_entries, service_records=[], sampling_points=[])
            try:
                _step("Сканирование служб", 1)

                service_progress = {"done": 0}

                def _service_item(message: str) -> None:
                    service_progress["done"] += 1
                    _step(f"Сканирование служб ({service_progress['done']})")
                    _emit_report(message)

                service_records = self._scan_services_with_callback(_service_item)
            except Exception as exc:
                logger.exception("Service scan failed: %s", exc)

        total_ram, used_ram = self._process_scanner.memory_totals()
        _step("Анализ использования памяти", 2)
        _emit_report(f"Системная память: {used_ram:.2f} / {total_ram:.2f} ГБ")

        if with_vt:
            candidates = [r for r in process_records if r.file_sha256]
            if candidates:
                workers = max(1, int(max_workers))
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {pool.submit(self._vt.lookup_by_sha256, r.file_sha256): r for r in candidates}
                    done = 0
                    for future in as_completed(futures):
                        pause_check()
                        if cancel_check():
                            break
                        rec = futures[future]
                        try:
                            rec.vt_result = future.result()
                            _emit_report(
                                f"VirusTotal: {rec.name} (PID {rec.pid}) | {self._vt_line(rec)}"
                            )
                        except Exception as exc:
                            logger.exception("VT lookup failed for pid=%s: %s", rec.pid, exc)
                        finally:
                            done += 1
                            _step(f"Проверка процессов в VirusTotal ({done}/{len(candidates)})")

        if heuristics_enabled and process_records:
            _step("Эвристический анализ", 1)
            enabled_map = heuristic_rules or self._heuristics.schema()
            enabled_rules = [rule for rule in self._heuristics.rules if enabled_map.get(rule.rule_id, True)]
            checks_done = 0
            for rec in process_records:
                score = 0
                hits: list[str] = []
                for rule in enabled_rules:
                    delta, msg = rule.evaluate(rec)
                    checks_done += 1
                    _step(f"Эвристика: проверка правил ({checks_done})")
                    if delta > 0 and msg:
                        score += delta
                        hits.append(f"{rule.title}: {msg}")
                        _emit_report(
                            f"Эвристика: {rec.name} (PID {rec.pid}) | правило: {rule.rule_id} ({rule.title}) | результат: подтверждено | +{delta}"
                        )
                rec.heuristic_score = min(100, score)
                rec.heuristic_hits = hits
                if not hits:
                    _emit_report(
                        f"Эвристика: {rec.name} (PID {rec.pid}) | проверено правил: {len(enabled_rules)} | совпадений: 0"
                    )

        diag_snapshot = None
        if self._diagnostics is not None:
            try:
                _step("Сбор диагностических метрик", 1)
                diag_snapshot = self._diagnostics.collect(used_ram_gb=used_ram, process_records=process_records)
            except Exception as exc:
                logger.exception("Diagnostics collection failed: %s", exc)

        sample_points = []
        if self._sampler is not None and sampling_points > 0 and enable_process_module:
            try:
                _step("Сбор сэмплов", 1)
                sample_points = self._sampler.collect_points(
                    interval_sec=sampling_interval_sec,
                    points=sampling_points,
                    high_mb=high_mb,
                    medium_mb=medium_mb,
                    cancel_check=cancel_check,
                )
            except Exception as exc:
                logger.exception("Sampling collection failed: %s", exc)

        snapshot = ScanSnapshot(
            created_at=datetime.now(),
            total_ram_gb=total_ram,
            used_ram_gb=used_ram,
            process_records=process_records,
            startup_entries=startup_entries,
            service_records=service_records,
            diagnostics=self._build_diagnostics(total_ram, used_ram, process_records),
            diagnostics_snapshot=diag_snapshot,
            sampling_points=sample_points,
        )

        report_path = self._reports.save_snapshot(snapshot)
        _step("Сохранение отчета", 2)
        _emit_progress(total_units, total_units, "Сканирование завершено")
        return snapshot, report_path

    def scan_startup_only(self) -> list:
        return self._scan_startup_with_callback(None)

    def scan_processes_only(self, high_mb: int, medium_mb: int) -> list:
        return self._scan_processes_with_callback(high_mb, medium_mb, None, None)

    def scan_services_only(self) -> list:
        return self._scan_services_with_callback(None)

    def lookup_sha256(self, sha256: str | None):
        if not sha256:
            return None
        return self._vt.lookup_by_sha256(sha256)

    @staticmethod
    def _safe_estimate(scanner_obj, method_name: str) -> int:
        method = getattr(scanner_obj, method_name, None)
        if method is None:
            return 0
        try:
            return max(0, int(method()))
        except Exception:
            return 0

    def _scan_processes_with_callback(self, high_mb: int, medium_mb: int, callback, record_callback):
        try:
            return self._process_scanner.scan_processes(
                high_mb=high_mb,
                medium_mb=medium_mb,
                item_callback=callback,
                record_callback=record_callback,
            )
        except TypeError:
            records = self._process_scanner.scan_processes(high_mb=high_mb, medium_mb=medium_mb)
            if callback is not None:
                for rec in records:
                    level_ru = {"high": "высокий", "medium": "средний", "low": "низкий"}.get(rec.memory_level.value, rec.memory_level.value)
                    callback(
                        f"Процесс: {rec.name} (PID {rec.pid}) | память: {rec.memory_mb:.1f} МБ | уровень: {level_ru} | путь: {rec.exe_path or '-'}"
                    )
            if record_callback is not None:
                for rec in records:
                    record_callback(rec)
            return records

    def _scan_startup_with_callback(self, callback):
        try:
            return self._startup_scanner.scan_startup_entries(item_callback=callback)
        except TypeError:
            records = self._startup_scanner.scan_startup_entries()
            if callback is not None:
                for rec in records:
                    callback(
                        f"Автозагрузка: {rec.name} | состояние: {'включена' if rec.enabled else 'отключена'} | источник: {rec.location} | команда: {rec.command}"
                    )
            return records

    def _scan_services_with_callback(self, callback):
        try:
            return self._service_scanner.scan_services(item_callback=callback)
        except TypeError:
            records = self._service_scanner.scan_services()
            if callback is not None:
                for rec in records:
                    callback(
                        f"Служба: {rec.name} | статус: {rec.status} | PID: {rec.pid or '-'} | риск: {rec.risk_score} | путь: {rec.executable_path or '-'}"
                    )
            return records

    @staticmethod
    def _vt_line(rec) -> str:
        vt = rec.vt_result
        if vt is None:
            return "нет данных"
        if not vt.available:
            return vt.summary or "недоступно"
        return f"M:{vt.malicious} S:{vt.suspicious} H:{vt.harmless}"

    def _build_and_save_snapshot(
        self,
        total_ram: float,
        used_ram: float,
        process_records,
        startup_entries,
        service_records,
        sampling_points,
    ) -> tuple[ScanSnapshot, str]:
        snapshot = ScanSnapshot(
            created_at=datetime.now(),
            total_ram_gb=total_ram,
            used_ram_gb=used_ram,
            process_records=process_records,
            startup_entries=startup_entries,
            service_records=service_records,
            diagnostics=self._build_diagnostics(total_ram, used_ram, process_records),
            diagnostics_snapshot=None,
            sampling_points=sampling_points,
        )
        return snapshot, self._reports.save_snapshot(snapshot)

    @staticmethod
    def snapshot_to_rows(snapshot: ScanSnapshot) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for p in snapshot.process_records:
            vt = p.vt_result
            vt_state = "n/a"
            if vt and vt.available:
                vt_state = f"M:{vt.malicious} S:{vt.suspicious} H:{vt.harmless}"
            rows.append(
                {
                    "pid": str(p.pid),
                    "name": p.name,
                    "memory_mb": f"{p.memory_mb:.1f}",
                    "level": p.memory_level.value,
                    "path": p.exe_path,
                    "user": p.username,
                    "vt": vt_state,
                }
            )
        return rows

    @staticmethod
    def snapshot_json(snapshot: ScanSnapshot) -> dict:
        return asdict(snapshot)

    @staticmethod
    def _build_diagnostics(total_ram_gb: float, used_ram_gb: float, process_records: list) -> list[str]:
        diagnostics: list[str] = []
        proc_sum_gb = sum(x.memory_mb for x in process_records) / 1024.0
        hidden_gb = max(0.0, used_ram_gb - proc_sum_gb)
        high = sum(1 for x in process_records if x.memory_level.value == "high")
        medium = sum(1 for x in process_records if x.memory_level.value == "medium")
        low = sum(1 for x in process_records if x.memory_level.value == "low")

        diagnostics.append(f"Использование RAM: {used_ram_gb:.2f} / {total_ram_gb:.2f} ГБ")
        diagnostics.append(f"Сумма памяти процессов: {proc_sum_gb:.2f} ГБ")
        diagnostics.append(f"Неатрибутированная память (оценка): {hidden_gb:.2f} ГБ")
        diagnostics.append(f"Процессы по уровню -> высокий: {high}, средний: {medium}, низкий: {low}")
        if hidden_gb > 2.0:
            diagnostics.append("Предупреждение: высокий объем неатрибутированной памяти; проверьте драйверы, сжатие памяти и пулы ядра")
        return diagnostics
