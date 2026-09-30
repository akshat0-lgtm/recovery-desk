"""Stage 4-6: E-Subro Hub. The hub has no public API; access comes with Arbitration Forums membership or
through a claims-system integration (e.g. AF's Guidewire ClaimCenter accelerator). This interface is what
the agent will call; `StubHub` records the call so the rest of the product can be built and tested."""
from __future__ import annotations

from typing import Protocol


class HubAdapter(Protocol):
    connected: bool
    def issue_demand(self, payload: dict, attachments: list[tuple[str, bytes]]) -> dict: ...
    def send_message(self, demand_ref: str, kind: str, text: str) -> dict: ...  # counter | request_info | fyi | accept
    def fetch_updates(self, since_iso: str) -> list[dict]: ...
    def file_arbitration(self, demand_ref: str, packet: dict) -> dict: ...


class StubHub:
    connected = False

    def issue_demand(self, payload, attachments):
        return {"status": "not_sent", "reason": "E-Subro Hub connection not configured (stub)",
                "would_send": {"fields": list(payload.keys()), "attachments": [n for n, _ in attachments]}}

    def send_message(self, demand_ref, kind, text):
        return {"status": "not_sent", "reason": "stub"}

    def fetch_updates(self, since_iso):
        return []

    def file_arbitration(self, demand_ref, packet):
        return {"status": "not_filed", "reason": "stub"}


def get_hub() -> HubAdapter:
    return StubHub()
