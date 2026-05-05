from __future__ import annotations

from app.domain.entities import VirusTotalResult
from app.domain.ports import VirusTotalPort


class NoopVirusTotalClient(VirusTotalPort):
    def lookup_by_sha256(self, sha256: str) -> VirusTotalResult:
        return VirusTotalResult(available=False, summary="VirusTotal lookup disabled")
