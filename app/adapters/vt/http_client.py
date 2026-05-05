from __future__ import annotations

import requests

from app.domain.entities import VirusTotalResult
from app.domain.ports import VirusTotalPort


class VirusTotalHttpClient(VirusTotalPort):
    def __init__(self, api_key: str, timeout_sec: int = 15) -> None:
        self._api_key = api_key
        self._timeout = timeout_sec

    def lookup_by_sha256(self, sha256: str) -> VirusTotalResult:
        if not self._api_key:
            return VirusTotalResult(available=False, summary="VirusTotal API key not configured")

        url = f"https://www.virustotal.com/api/v3/files/{sha256}"
        headers = {"x-apikey": self._api_key}
        try:
            response = requests.get(url, headers=headers, timeout=self._timeout)
            if response.status_code != 200:
                return VirusTotalResult(
                    available=False,
                    summary=f"VirusTotal unavailable: HTTP {response.status_code}",
                )
            payload = response.json()
            stats = payload.get("data", {}).get("attributes", {}).get("last_analysis_stats", {})
            return VirusTotalResult(
                available=True,
                malicious=int(stats.get("malicious", 0)),
                suspicious=int(stats.get("suspicious", 0)),
                harmless=int(stats.get("harmless", 0)),
                undetected=int(stats.get("undetected", 0)),
                permalink=payload.get("data", {}).get("links", {}).get("self"),
                summary="ok",
            )
        except requests.RequestException as exc:
            return VirusTotalResult(available=False, summary=f"VirusTotal error: {exc}")
