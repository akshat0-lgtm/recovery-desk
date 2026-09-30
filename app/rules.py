"""Business rules the agent cannot override: state law, deadlines, carriers, money limits.

Sources (as of Sep 2026):
- Negligence rules: https://www.recordinglaw.com/us-laws/car-accident/
- Property-damage limitation periods: https://www.consumershield.com/injuries-accidents/car-accidents/statute-of-limitations
- $100,000 compulsory arbitration limit: Arbitration Forums rules,
  https://home.arbfile.org/training/reference-guides/arbitration-forums,-inc-rules
Extend STATES before using the product outside these six states.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

AF_CAP = 100_000          # compulsory Arbitration Forums limit on company-paid damages
VALUE_FLOOR = 150         # minimum expected recovery worth an automated demand
MIN_LIKELIHOOD = 25       # below this the case is too weak for a demand
URGENT_DAYS = 120         # fewer days than this before the deadline: protective suit (litigation)
PARK_REVIEW_DEMAND = 5000 # a file worth this much cannot be parked without a person
LEDGER_TOLERANCE = 1.0    # dollars


@dataclass(frozen=True)
class StateRule:
    name: str
    rule: str          # pure | mod51 | mod50 | contrib
    limit_years: int
    text: str


STATES: dict[str, StateRule] = {
    "CA": StateRule("California", "pure", 3, "Pure comparative: recovery reduced by the insured's fault share, never barred"),
    "NY": StateRule("New York", "pure", 3, "Pure comparative: recovery reduced by the insured's fault share, never barred"),
    "TX": StateRule("Texas", "mod51", 2, "Modified comparative, 51% bar: barred if the insured is 51% or more at fault"),
    "FL": StateRule("Florida", "mod51", 2, "Modified comparative, 51% bar. 2-year limit for losses after 24 Mar 2023, 4 years before"),
    "GA": StateRule("Georgia", "mod50", 4, "Modified comparative, 50% bar: barred if the insured is 50% or more at fault"),
    "MD": StateRule("Maryland", "contrib", 3, "Contributory negligence: any fault on the insured bars recovery"),
}

# Stand-in for the Arbitration Forums member list. Replace with the real registry
# (company codes matter: AF routes demands by code).
CARRIERS: dict[str, dict] = {
    "harborline insurance": {"name": "Harborline Insurance", "code": "HRB01", "af_member": True},
    "keystone auto casualty": {"name": "Keystone Auto Casualty", "code": "KAC02", "af_member": True},
    "pinecrest general": {"name": "Pinecrest General", "code": "PCG03", "af_member": True},
    "summit road insurance": {"name": "Summit Road Insurance", "code": "SRI04", "af_member": True},
    "bluewater mutual": {"name": "Bluewater Mutual", "code": "BWM05", "af_member": True},
    "cedar plains insurance": {"name": "Cedar Plains Insurance", "code": None, "af_member": False},
}

OUR_COMPANY = {"name": "Northgate Mutual (fictional)", "code": "NGM00"}


def limitation_years(state: str, loss: date) -> int:
    if state == "FL" and loss < date(2023, 3, 24):
        return 4
    return STATES[state].limit_years


def deadline(state: str, loss: date, today: date) -> dict:
    years = limitation_years(state, loss)
    try:
        expires = loss.replace(year=loss.year + years)
    except ValueError:  # 29 Feb
        expires = loss.replace(year=loss.year + years, day=28)
    left = (expires - today).days
    status = "expired" if left <= 0 else "urgent" if left < URGENT_DAYS else "open"
    return {"limitation_years": years, "expires": expires.isoformat(), "days_left": left, "status": status}


def recoverable_share(state: str, insured_fault_pct: float) -> float:
    f = max(0.0, min(100.0, insured_fault_pct)) / 100
    rule = STATES[state].rule
    if rule == "contrib":
        return 0.0 if f > 0 else 1.0
    if rule == "mod51":
        return 0.0 if f >= 0.51 else 1 - f
    if rule == "mod50":
        return 0.0 if f >= 0.5 else 1 - f
    return 1 - f


def find_carrier(name: str | None) -> dict | None:
    if not name:
        return None
    key = " ".join(name.lower().replace(",", " ").split())
    if key in CARRIERS:
        return CARRIERS[key]
    for k, v in CARRIERS.items():  # tolerate "Harborline Ins." style variants
        if key.startswith(k.split()[0]) and k.split()[0] in key:
            return v
    return None


def arbitration_available(carrier: dict | None, company_paid: float) -> tuple[bool, str]:
    """Compulsory AF arbitration needs both carriers to be signatories and paid damages within the limit."""
    if not carrier:
        return False, "no insurer on file for the liable party"
    if not carrier.get("af_member"):
        return False, f"{carrier['name']} is not an Arbitration Forums signatory"
    if company_paid > AF_CAP:
        return False, f"company-paid damages exceed the ${AF_CAP:,} arbitration limit"
    return True, "both carriers are Arbitration Forums signatories and the claim is within the limit"


# Deductible refund. Most states: pro rata (deductible / total damages x recovery), e.g. New York.
# Wyoming and Montana: full deductible first. Source: MWL deductible reimbursement survey,
# https://www.mwl-law.com/wp-content/uploads/2018/02/DEDUCTIBLE-REIMBURSEMENT-LAWS.pdf
FULL_FIRST_STATES = {"WY", "MT"}


def deductible_refund(state: str, deductible: float, total_damages: float, recovered: float) -> float:
    if deductible <= 0 or recovered <= 0:
        return 0.0
    if state in FULL_FIRST_STATES:
        return round(min(deductible, recovered), 2)
    return round(min(deductible, deductible / total_damages * recovered), 2)
