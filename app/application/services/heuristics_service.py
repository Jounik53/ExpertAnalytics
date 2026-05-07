from __future__ import annotations

from dataclasses import dataclass, field
import json
from json import JSONDecodeError
import re
from pathlib import Path
from typing import Any

from app.domain.entities import MemoryLevel, ProcessRecord


ALLOWED_FIELDS = {
    "name": str,
    "exe_path": str,
    "username": str,
    "memory_mb": (int, float),
    "cpu_percent": (int, float),
    "create_time": str,
    "is_system": bool,
    "command_line": str,
    "file_sha256": str,
}

SUSPICIOUS_PARENTS = {
    "winword.exe",
    "excel.exe",
    "outlook.exe",
    "wscript.exe",
    "cscript.exe",
    "mshta.exe",
    "rundll32.exe",
    "regsvr32.exe",
}

LOLBAS_NAMES = {
    "powershell.exe",
    "pwsh.exe",
    "cmd.exe",
    "wscript.exe",
    "cscript.exe",
    "mshta.exe",
    "regsvr32.exe",
    "rundll32.exe",
    "wmic.exe",
    "bitsadmin.exe",
    "certutil.exe",
    "msbuild.exe",
    "installutil.exe",
    "msiexec.exe",
    "schtasks.exe",
}


@dataclass(slots=True)
class RuleDiagnostic:
    code: str
    message: str
    path: str = ""
    line: int = 0
    column: int = 0
    severity: str = "error"

    def format_like_compiler(self) -> str:
        pos = ""
        if self.line > 0:
            pos = f" ({self.line}:{max(1, self.column)})"
        path = self.path or "<rule>"
        return f"{path}{pos}: {self.severity} {self.code}: {self.message}"


@dataclass(slots=True)
class HeuristicRuleDefinition:
    rule_id: str
    title: str
    description: str = ""
    enabled_by_default: bool = True
    score: int = 10
    applies_to: list[str] = field(default_factory=lambda: ["scan", "heuristic"])
    tags: list[str] = field(default_factory=list)
    script: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class HeuristicMatch:
    rule_id: str
    title: str
    score: int
    message: str


@dataclass(slots=True)
class HeuristicEvalResult:
    score: int
    hits: list[str]
    matches: list[HeuristicMatch]


@dataclass(slots=True)
class CompiledRule:
    rule_id: str
    title: str
    description: str
    enabled_by_default: bool
    score: int
    applies_to: list[str]
    tags: list[str]
    script: dict[str, Any]

    def evaluate(self, rec: ProcessRecord) -> tuple[int, str | None]:
        ok, note = ScriptEvaluator.eval_expr(self.script, rec)
        if not ok:
            return 0, None
        msg = note or self.description or "Условие правила выполнено"
        return max(0, int(self.score)), msg


class ScriptEvaluator:
    @staticmethod
    def _str(value: Any) -> str:
        return str(value or "")

    @staticmethod
    def _get_field(rec: ProcessRecord, field_name: str) -> Any:
        if field_name == "memory_level":
            return rec.memory_level.value
        return getattr(rec, field_name, None)

    @classmethod
    def eval_expr(cls, expr: dict[str, Any], rec: ProcessRecord) -> tuple[bool, str | None]:
        if "all" in expr:
            parts = expr.get("all")
            if not isinstance(parts, list):
                return False, None
            details: list[str] = []
            for p in parts:
                ok, msg = cls.eval_expr(p, rec)
                if not ok:
                    return False, None
                if msg:
                    details.append(msg)
            return True, "; ".join(details) if details else None

        if "any" in expr:
            parts = expr.get("any")
            if not isinstance(parts, list):
                return False, None
            notes: list[str] = []
            for p in parts:
                ok, msg = cls.eval_expr(p, rec)
                if ok:
                    if msg:
                        notes.append(msg)
                    return True, "; ".join(notes) if notes else msg
            return False, None

        if "not" in expr:
            target = expr.get("not")
            if not isinstance(target, dict):
                return False, None
            ok, _ = cls.eval_expr(target, rec)
            return (not ok), None

        op = str(expr.get("op") or "").strip().lower()
        field_name = str(expr.get("field") or "").strip()
        value = expr.get("value")
        values = expr.get("values")
        case_insensitive = bool(expr.get("case_insensitive", True))
        text = cls._get_field(rec, field_name)

        if op in {"contains", "starts_with", "ends_with", "equals"}:
            left = cls._str(text)
            right = cls._str(value)
            if case_insensitive:
                left = left.lower()
                right = right.lower()
            if op == "contains":
                ok = right in left
            elif op == "starts_with":
                ok = left.startswith(right)
            elif op == "ends_with":
                ok = left.endswith(right)
            else:
                ok = left == right
            return ok, expr.get("message") if ok else None

        if op in {"in", "any_contains"}:
            if not isinstance(values, list):
                return False, None
            if op == "in":
                needle = cls._str(text)
                pool = [cls._str(x) for x in values]
                if case_insensitive:
                    needle = needle.lower()
                    pool = [x.lower() for x in pool]
                ok = needle in pool
            else:
                hay = cls._str(text)
                pool = [cls._str(x) for x in values]
                if case_insensitive:
                    hay = hay.lower()
                    pool = [x.lower() for x in pool]
                ok = any(x in hay for x in pool)
            return ok, expr.get("message") if ok else None

        if op == "regex":
            pattern = cls._str(value)
            flags = re.IGNORECASE if case_insensitive else 0
            try:
                ok = re.search(pattern, cls._str(text), flags=flags) is not None
            except re.error:
                return False, None
            return ok, expr.get("message") if ok else None

        if op in {"gt", "gte", "lt", "lte"}:
            try:
                left = float(text)
                right = float(value)
            except Exception:
                return False, None
            if op == "gt":
                ok = left > right
            elif op == "gte":
                ok = left >= right
            elif op == "lt":
                ok = left < right
            else:
                ok = left <= right
            return ok, expr.get("message") if ok else None

        return False, None


class HeuristicRuleValidator:
    def validate_definition(self, rule: HeuristicRuleDefinition, source_path: str = "") -> list[RuleDiagnostic]:
        diags: list[RuleDiagnostic] = []
        if not re.fullmatch(r"[a-z0-9_\-.]{3,64}", rule.rule_id or ""):
            diags.append(RuleDiagnostic("E1001", "Недопустимый rule_id", path=source_path or rule.rule_id))
        if not str(rule.title or "").strip():
            diags.append(RuleDiagnostic("E1002", "Пустой title", path=source_path or rule.rule_id))
        if not isinstance(rule.score, int) or rule.score <= 0 or rule.score > 100:
            diags.append(RuleDiagnostic("E1003", "score должен быть в диапазоне 1..100", path=source_path or rule.rule_id))
        if not isinstance(rule.applies_to, list) or not rule.applies_to:
            diags.append(RuleDiagnostic("E1004", "applies_to должен быть непустым списком", path=source_path or rule.rule_id))
        else:
            bad_modes = [x for x in rule.applies_to if x not in {"scan", "heuristic"}]
            if bad_modes:
                diags.append(RuleDiagnostic("E1005", f"Неизвестные режимы applies_to: {bad_modes}", path=source_path or rule.rule_id))
        diags.extend(self._validate_expr(rule.script, source_path or rule.rule_id, "script"))
        return diags

    def _validate_expr(self, expr: Any, source_path: str, path: str) -> list[RuleDiagnostic]:
        diags: list[RuleDiagnostic] = []
        if not isinstance(expr, dict):
            return [RuleDiagnostic("E1100", "Выражение должно быть объектом", path=source_path)]

        if "all" in expr:
            items = expr["all"]
            if not isinstance(items, list) or not items:
                diags.append(RuleDiagnostic("E1101", "all должен быть непустым списком", path=source_path))
                return diags
            for idx, item in enumerate(items):
                diags.extend(self._validate_expr(item, source_path, f"{path}.all[{idx}]"))
            return diags

        if "any" in expr:
            items = expr["any"]
            if not isinstance(items, list) or not items:
                diags.append(RuleDiagnostic("E1102", "any должен быть непустым списком", path=source_path))
                return diags
            for idx, item in enumerate(items):
                diags.extend(self._validate_expr(item, source_path, f"{path}.any[{idx}]"))
            return diags

        if "not" in expr:
            item = expr["not"]
            if not isinstance(item, dict):
                diags.append(RuleDiagnostic("E1103", "not должен содержать объект", path=source_path))
                return diags
            diags.extend(self._validate_expr(item, source_path, f"{path}.not"))
            return diags

        op = str(expr.get("op") or "").strip().lower()
        field_name = str(expr.get("field") or "").strip()
        if op not in {
            "contains",
            "starts_with",
            "ends_with",
            "equals",
            "in",
            "any_contains",
            "regex",
            "gt",
            "gte",
            "lt",
            "lte",
        }:
            diags.append(RuleDiagnostic("E1104", f"Неизвестный оператор: {op}", path=source_path))
        if field_name not in ALLOWED_FIELDS and field_name != "memory_level":
            diags.append(RuleDiagnostic("E1105", f"Недопустимое поле: {field_name}", path=source_path))

        if op in {"in", "any_contains"}:
            if not isinstance(expr.get("values"), list) or len(expr.get("values") or []) == 0:
                diags.append(RuleDiagnostic("E1106", f"Для {op} требуется непустой values", path=source_path))
        else:
            if "value" not in expr:
                diags.append(RuleDiagnostic("E1107", f"Для {op} требуется value", path=source_path))

        if op in {"gt", "gte", "lt", "lte"}:
            if field_name not in {"memory_mb", "cpu_percent"}:
                diags.append(RuleDiagnostic("E1108", f"Числовой оператор {op} допустим только для memory_mb/cpu_percent", path=source_path))

        if op == "regex":
            try:
                re.compile(str(expr.get("value") or ""))
            except re.error as exc:
                diags.append(RuleDiagnostic("E1109", f"Некорректный regex: {exc}", path=source_path))

        return diags


class HeuristicEngine:
    def __init__(self, custom_rules: list[dict[str, Any]] | None = None) -> None:
        self._validator = HeuristicRuleValidator()
        self._builtin_defs = self._default_rule_definitions()
        merged = list(self._builtin_defs)
        if custom_rules:
            merged.extend(self._from_raw_list(custom_rules))

        self._definitions = self._dedupe_rules(merged)
        self.rules = [self._compile(d) for d in self._definitions]

    @staticmethod
    def parse_rules_text(text: str) -> tuple[list[dict[str, Any]], list[RuleDiagnostic]]:
        if not text.strip():
            return [], []
        try:
            data = json.loads(text)
        except JSONDecodeError as exc:
            return [], [
                RuleDiagnostic(
                    code="E0001",
                    message=exc.msg,
                    line=int(exc.lineno or 0),
                    column=int(exc.colno or 0),
                )
            ]
        if not isinstance(data, list):
            return [], [RuleDiagnostic(code="E0002", message="Ожидается JSON-массив правил")]
        if not all(isinstance(x, dict) for x in data):
            return [], [RuleDiagnostic(code="E0003", message="Каждый элемент массива должен быть JSON-объектом")]
        return data, []

    def validate_raw_rules(self, payload: list[dict[str, Any]]) -> list[RuleDiagnostic]:
        defs = self._from_raw_list(payload)
        diags: list[RuleDiagnostic] = []
        seen: set[str] = set()
        for d in defs:
            if d.rule_id in seen:
                diags.append(RuleDiagnostic("E1200", f"Дублирующийся rule_id: {d.rule_id}", path=d.rule_id))
            seen.add(d.rule_id)
            diags.extend(self._validator.validate_definition(d, d.rule_id))
        return diags

    def schema(self) -> dict[str, bool]:
        return {rule.rule_id: rule.enabled_by_default for rule in self.rules}

    def definitions_payload(self) -> list[dict[str, Any]]:
        return [
            {
                "rule_id": d.rule_id,
                "title": d.title,
                "description": d.description,
                "enabled_by_default": d.enabled_by_default,
                "score": d.score,
                "applies_to": list(d.applies_to),
                "tags": list(d.tags),
                "script": d.script,
            }
            for d in self._definitions
        ]

    def evaluate_many(self, records: list[ProcessRecord], enabled: dict[str, bool], mode: str = "scan", selected_rule_ids: list[str] | None = None) -> None:
        allowed = set(selected_rule_ids or [])
        for rec in records:
            result = self.evaluate_one(rec, enabled=enabled, mode=mode, selected_rule_ids=allowed if allowed else None)
            rec.heuristic_score = result.score
            rec.heuristic_hits = result.hits

    def evaluate_one(
        self,
        rec: ProcessRecord,
        enabled: dict[str, bool] | None = None,
        mode: str = "heuristic",
        selected_rule_ids: set[str] | None = None,
    ) -> HeuristicEvalResult:
        enabled_map = enabled or self.schema()
        score = 0
        hits: list[str] = []
        matches: list[HeuristicMatch] = []
        for rule in self.rules:
            if mode not in rule.applies_to:
                continue
            if selected_rule_ids is not None and rule.rule_id not in selected_rule_ids:
                continue
            if not enabled_map.get(rule.rule_id, True):
                continue
            delta, msg = rule.evaluate(rec)
            if delta > 0 and msg:
                score += delta
                hits.append(f"{rule.title}: {msg}")
                matches.append(HeuristicMatch(rule.rule_id, rule.title, delta, msg))
        return HeuristicEvalResult(min(100, score), hits, matches)

    def evaluate_file(
        self,
        file_path: str,
        enabled: dict[str, bool] | None = None,
        mode: str = "heuristic",
        selected_rule_ids: set[str] | None = None,
    ) -> HeuristicEvalResult:
        p = Path(file_path)
        rec = ProcessRecord(
            pid=0,
            name=p.name or "<file>",
            exe_path=str(p),
            username="local",
            memory_mb=0.0,
            memory_level=MemoryLevel.LOW,
            cpu_percent=0.0,
            create_time="",
            is_system=("\\windows\\" in str(p).lower()),
            command_line=str(p),
            file_sha256=None,
        )
        return self.evaluate_one(rec, enabled=enabled, mode=mode, selected_rule_ids=selected_rule_ids)

    @staticmethod
    def default_rule_packs() -> dict[str, list[str]]:
        return {
            "all": [],
            "miners": ["miner_keywords", "stratum_url", "suspicious_pool_ports", "high_cpu_memory"],
            "powershell_abuse": ["ps_encoded", "ps_suspicious_switches", "ps_download_cradle", "ps_obfuscation"],
            "lolbas": ["lolbas_binary", "living_off_the_land_suspicious_args"],
            "execution_from_user_writable": ["suspicious_path", "public_ps1", "unsigned_outside_system"],
        }

    def _compile(self, d: HeuristicRuleDefinition) -> CompiledRule:
        return CompiledRule(
            rule_id=d.rule_id,
            title=d.title,
            description=d.description,
            enabled_by_default=d.enabled_by_default,
            score=d.score,
            applies_to=list(d.applies_to),
            tags=list(d.tags),
            script=d.script,
        )

    @staticmethod
    def _dedupe_rules(defs: list[HeuristicRuleDefinition]) -> list[HeuristicRuleDefinition]:
        out: dict[str, HeuristicRuleDefinition] = {}
        for d in defs:
            out[d.rule_id] = d
        return list(out.values())

    @staticmethod
    def _from_raw_list(items: list[dict[str, Any]]) -> list[HeuristicRuleDefinition]:
        defs: list[HeuristicRuleDefinition] = []
        for item in items:
            defs.append(
                HeuristicRuleDefinition(
                    rule_id=str(item.get("rule_id") or "").strip(),
                    title=str(item.get("title") or "").strip(),
                    description=str(item.get("description") or "").strip(),
                    enabled_by_default=bool(item.get("enabled_by_default", True)),
                    score=int(item.get("score", 10)),
                    applies_to=[str(x).strip() for x in item.get("applies_to", ["scan", "heuristic"]) if str(x).strip()],
                    tags=[str(x).strip() for x in item.get("tags", []) if str(x).strip()],
                    script=dict(item.get("script", {})) if isinstance(item.get("script"), dict) else {},
                )
            )
        return defs

    @staticmethod
    def _default_rule_definitions() -> list[HeuristicRuleDefinition]:
        return [
            HeuristicRuleDefinition(
                rule_id="suspicious_path",
                title="Подозрительный путь",
                description="Исполняемый файл в user-writable каталоге",
                score=25,
                tags=["path", "persistence"],
                script={"op": "any_contains", "field": "exe_path", "values": ["\\temp\\", "\\appdata\\", "\\downloads\\", "\\public\\"], "message": "Запуск из подозрительного пути"},
            ),
            HeuristicRuleDefinition(
                rule_id="miner_keywords",
                title="Ключевые слова майнера",
                description="Майнинг-артефакты в имени/CLI",
                score=45,
                tags=["miner"],
                script={"op": "any_contains", "field": "command_line", "values": ["xmrig", "cpuminer", "cgminer", "stratum", "nicehash", "miner"], "message": "Обнаружены ключевые слова майнера"},
            ),
            HeuristicRuleDefinition(
                rule_id="high_cpu_memory",
                title="Высокая CPU+RAM",
                description="Высокая вычислительная нагрузка",
                score=20,
                tags=["resource"],
                script={"all": [{"op": "gte", "field": "cpu_percent", "value": 60, "message": "CPU >= 60%"}, {"op": "gte", "field": "memory_mb", "value": 500, "message": "RAM >= 500MB"}]},
            ),
            HeuristicRuleDefinition(
                rule_id="unsigned_outside_system",
                title="Необычное расположение",
                description="Исполняемый файл вне системных каталогов",
                score=10,
                tags=["path"],
                script={"all": [{"op": "equals", "field": "is_system", "value": False}, {"not": {"op": "any_contains", "field": "exe_path", "values": ["\\program files\\", "\\windows\\"]}}], "message": "Необычное расположение исполняемого файла"},
            ),
            HeuristicRuleDefinition(
                rule_id="ps_encoded",
                title="PowerShell EncodedCommand",
                description="Признаки obfuscated powershell",
                score=30,
                tags=["powershell", "mitre:T1059.001"],
                script={"all": [{"op": "any_contains", "field": "command_line", "values": ["powershell", "pwsh"], "message": "powershell/pwsh"}, {"op": "regex", "field": "command_line", "value": "(?i)\\s-(e|en|enc|encodedcommand)\\b", "message": "EncodedCommand switch"}]},
            ),
            HeuristicRuleDefinition(
                rule_id="ps_suspicious_switches",
                title="PowerShell suspicious switches",
                description="NOP/NONI/IEX/DownloadString",
                score=25,
                tags=["powershell", "mitre:T1059.001"],
                script={"all": [{"op": "any_contains", "field": "command_line", "values": ["powershell", "pwsh"]}, {"op": "any_contains", "field": "command_line", "values": [" -nop", " -noni", " invoke-expression", " iex ", ".downloadstring", "downloadfile"], "message": "Suspicious PowerShell cmdlets/switches"}]},
            ),
            HeuristicRuleDefinition(
                rule_id="ps_download_cradle",
                title="PowerShell download cradle",
                description="Скачивание и выполнение из сети",
                score=35,
                tags=["powershell", "network", "mitre:T1105"],
                script={"all": [{"op": "any_contains", "field": "command_line", "values": ["powershell", "pwsh"]}, {"op": "any_contains", "field": "command_line", "values": ["http://", "https://", "new-object net.webclient", "invoke-webrequest", "iwr ", "wget "], "message": "Remote content transfer"}]},
            ),
            HeuristicRuleDefinition(
                rule_id="ps_obfuscation",
                title="PowerShell obfuscation",
                description="Подозрительная обфускация аргументов",
                score=20,
                tags=["powershell", "obfuscation", "mitre:T1027"],
                script={"all": [{"op": "any_contains", "field": "command_line", "values": ["powershell", "pwsh"]}, {"op": "regex", "field": "command_line", "value": "[\\^\\$%+]{4,}", "message": "High special-char density"}]},
            ),
            HeuristicRuleDefinition(
                rule_id="public_ps1",
                title="PS1 из Public",
                description="Скрипт .ps1 запущен из Public",
                score=20,
                tags=["powershell", "path"],
                script={"all": [{"op": "any_contains", "field": "command_line", "values": ["powershell", "pwsh"]}, {"op": "regex", "field": "command_line", "value": "(?i)c:\\\\users\\\\public\\\\.*\\.ps1", "message": "PowerShell script from Public"}]},
            ),
            HeuristicRuleDefinition(
                rule_id="stratum_url",
                title="Stratum URL",
                description="Протокол stratum в аргументах",
                score=35,
                tags=["miner"],
                script={"op": "regex", "field": "command_line", "value": "(?i)stratum(\\+tcp)?://", "message": "Stratum URL pattern"},
            ),
            HeuristicRuleDefinition(
                rule_id="suspicious_pool_ports",
                title="Пул-порты майнинга",
                description="Типичные порты mining pool",
                score=20,
                tags=["miner", "network"],
                script={"op": "regex", "field": "command_line", "value": "(?i)(:3333|:4444|:5555|:7777|:14444|:14433)", "message": "Suspicious pool ports"},
            ),
            HeuristicRuleDefinition(
                rule_id="lolbas_binary",
                title="LOLBAS бинарь",
                description="Процесс из списка LOLBAS",
                score=12,
                tags=["lolbas", "mitre:T1218"],
                script={"op": "in", "field": "name", "values": sorted(LOLBAS_NAMES), "message": "Known LOLBAS executable"},
            ),
            HeuristicRuleDefinition(
                rule_id="living_off_the_land_suspicious_args",
                title="LOLBAS suspicious args",
                description="Аргументы LOLBAS указывают на загрузку/выполнение",
                score=30,
                tags=["lolbas", "mitre:T1218", "mitre:T1105"],
                script={"all": [{"op": "in", "field": "name", "values": ["regsvr32.exe", "rundll32.exe", "mshta.exe", "wmic.exe", "certutil.exe", "bitsadmin.exe"]}, {"op": "any_contains", "field": "command_line", "values": ["http://", "https://", "scrobj.dll", "javascript:", "url.dll", "download", " /i:"], "message": "Suspicious LOLBAS arguments"}]},
            ),
            HeuristicRuleDefinition(
                rule_id="office_spawn_shell",
                title="Office/Script parent spawning shell",
                description="CLI содержит родитель-лоадер и shell",
                score=28,
                tags=["execution", "mitre:T1059"],
                script={"all": [{"op": "any_contains", "field": "command_line", "values": sorted(SUSPICIOUS_PARENTS)}, {"op": "any_contains", "field": "command_line", "values": ["powershell", "cmd.exe", "wscript", "cscript"], "message": "Suspicious parent-shell chain hints"}]},
            ),
        ]
