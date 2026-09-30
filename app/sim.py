"""Simulated counterparties for stages 4-7: the other carrier, the bank feed, the arbitration forum.

In production these are replaced by E-Subro Hub (or email) for carrier traffic, the bank or finance feed
for payments, and Arbitration Forums for decisions. The agent never sees these scripts: it only reads the
messages and payments they produce, exactly as it would read real ones.
"""
from __future__ import annotations

import hashlib

# Which behaviour the simulated carrier shows for each sample file (unknown files get one by hash).
SAMPLE_PROFILES = {
    "NG-25-10412": "request_info",       # asks for photos not in the file -> user request, then pays
    "NG-25-10877": "short_pay",          # pays without the deductible -> agent chases the balance
    "NG-26-00210": "counter_items",      # disputes rental days -> agent rebuts with repair dates
    "NG-25-11622": "counter_liability",  # 60% then 85% final -> auto-settles at the 85% threshold
    "NG-25-11966": "ignore",             # silent -> follow-ups at 15/30/45 -> arbitration at 60
    "NG-25-11304": "deny",               # denies liability -> arbitration filing for review
    "NG-26-00144": "counter_low",        # 60% then 70% final, over the AF limit -> settle or litigate
}
PROFILE_ROTATION = ["accept", "counter_liability", "request_info", "counter_items", "short_pay"]

# Days after our last outbound action before the carrier's next move.
STEPS = {
    "accept": [18],
    "short_pay": [16],
    "request_info": [14, 12],
    "counter_items": [16, 12],
    "counter_liability": [21, 14, 14],
    "counter_low": [21, 14, 14],
    "deny": [25, 20],
    "ignore": [],
}
SILENCE_GAP = 15
PAYMENT_LAG = 10
FORUM_DAYS = 30

HANDLERS = ["J. Alvarez", "M. Chen", "R. Okoro", "S. Patel", "T. Nguyen"]


def profile_for(claim_no: str) -> str:
    if claim_no in SAMPLE_PROFILES:
        return SAMPLE_PROFILES[claim_no]
    h = int(hashlib.sha1(claim_no.encode()).hexdigest(), 16)
    return PROFILE_ROTATION[h % len(PROFILE_ROTATION)]


def _sig(case) -> str:
    ex = case.extraction
    h = HANDLERS[int(hashlib.sha1(case.claim_no.encode()).hexdigest(), 16) % len(HANDLERS)]
    ref = ex.get("other_party_claim_number") or "their file"
    carrier = (case.decision.get("carrier") or {}).get("name") or "the carrier"
    return f"\n\n{h}\nSubrogation Unit, {carrier}\nRef {ref}"


def carrier_message(case) -> dict | None:
    """The carrier's next message for its profile and step. Returns {kind, text, offer?}."""
    neg, ex, dec = case.neg, case.extraction, case.decision
    prof, k, D = neg["profile"], neg["step"], neg["demand"]
    ours = case.claim_no
    fault = dec["insured_fault_pct"]
    sig = _sig(case)
    r10 = lambda x: round(x / 10) * 10
    if prof in ("accept", "short_pay"):
        return {"kind": "accept", "offer": D, "text": f"Re: your claim {ours}\n\nWe have reviewed your demand and the police report. We accept {100 - fault:.0f}% liability for our insured and will pay ${D:,.2f} within 10 business days.{sig}"}
    if prof == "request_info":
        if k == 0:
            return {"kind": "request", "offer": None, "requested": "color photographs of the damage to your insured's vehicle",
                    "text": f"Re: {ours}\n\nBefore we can evaluate this demand, please send color photographs of the damage to your insured's vehicle. The estimate alone is not enough for our damage review.{sig}"}
        return {"kind": "accept", "offer": D, "text": f"Re: {ours}\n\nThank you for the photographs. Liability accepted. We will pay ${D:,.2f} within 10 business days.{sig}"}
    if prof == "counter_items":
        if k == 0:
            rental = sum(i["amount"] for i in ex["line_items"] if i["category"] == "rental")
            days = ex.get("rental_days") or 1
            allow = max(1, int((ex.get("repair_days") or days) - 6))
            cut = max(0.0, (days - allow) * rental / days * (100 - fault) / 100)
            o = round(D - cut, 2)
            return {"kind": "counter", "offer": o, "text": f"Re: {ours}\n\nLiability accepted at {100 - fault:.0f}%. However the rental period is excessive: our review of the repair supports {allow} rental days, not {days:.0f}. We will pay ${o:,.2f}.{sig}"}
        return {"kind": "accept", "offer": D, "text": f"Re: {ours}\n\nThank you for the repair timeline. We will pay ${D:,.2f} within 10 business days.{sig}"}
    if prof in ("counter_liability", "counter_low"):
        final = 0.85 if prof == "counter_liability" else 0.70
        if k == 0:
            o = r10(D * 0.60)
            return {"kind": "counter", "offer": o, "text": f"Re: {ours}\n\nOur insured states the light was yellow when they entered the intersection and that your insured was travelling fast. We view liability as 60/40 and can offer ${o:,.2f} in full settlement.{sig}"}
        o = r10(D * final)
        if k == 1:
            return {"kind": "counter", "offer": o, "text": f"Re: {ours}\n\nWe have reviewed the material you cited. We can move to ${o:,.2f}. This is our final offer.{sig}"}
        return {"kind": "counter", "offer": o, "text": f"Re: {ours}\n\nWe are standing on ${o:,.2f}. If that is not acceptable you will need to pursue other remedies.{sig}"}
    if prof == "deny":
        if k == 0:
            why = "braked suddenly without cause" if "sudden" in (ex.get("loss_description") or "").lower() else "moved into our insured's lane without signalling"
            return {"kind": "deny", "offer": None, "text": f"Re: {ours}\n\nWe deny liability. Our insured reports that your insured {why}. With no police report and no independent witness, we have no payment to offer.{sig}"}
        return {"kind": "deny", "offer": None, "text": f"Re: {ours}\n\nWe maintain our denial.{sig}"}
    return None


def first_payment(case, agreed: float) -> float:
    """What the carrier actually pays. The short-pay profile leaves out the deductible."""
    if case.neg["profile"] == "short_pay" and not case.neg.get("balance_requested"):
        ded = case.extraction.get("deductible") or 0
        return round(agreed - ded, 2) if ded and agreed > ded else round(agreed * 0.9, 2)
    return round(agreed, 2)


def forum_award(case) -> tuple[float, str]:
    """Simulated Arbitration Forums decision, driven by the evidence strength the agent assessed."""
    base = case.neg["demand"]
    if case.decision["likelihood_pct"] >= 55:
        return round(base, 2), "Arbitrator found the responding carrier's insured 100% liable on the evidence submitted."
    return round(base * 0.5, 2), "Arbitrator found liability shared 50/50: the evidence did not establish either version."
