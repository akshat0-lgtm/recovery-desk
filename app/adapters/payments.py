"""Stage 7: payments feed (bank remittance or finance system) and claims ledger. Stubbed in v1."""
from __future__ import annotations

from typing import Protocol


class PaymentsAdapter(Protocol):
    connected: bool
    def receipts(self, since_iso: str) -> list[dict]: ...      # {payer, amount, reference, received_on}
    def book_recovery(self, claim_number: str, amount: float) -> dict: ...
    def refund_deductible(self, claim_number: str, amount: float, payee: str) -> dict: ...


class StubPayments:
    connected = False

    def receipts(self, since_iso):
        return []

    def book_recovery(self, claim_number, amount):
        return {"status": "not_booked", "reason": "stub"}

    def refund_deductible(self, claim_number, amount, payee):
        return {"status": "not_paid", "reason": "stub"}


def get_payments() -> PaymentsAdapter:
    return StubPayments()
