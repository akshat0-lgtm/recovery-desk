"""Tools and guardrails for stages 0-3. Each builder returns the tools for one agent run.

The model decides; these functions check. Anything that raises `Blocked` goes back to the model.
"""
from __future__ import annotations

import re
from datetime import date

from .. import rules
from ..pdftext import quote_page, search
from .runner import Blocked, Tool

DOC_TYPES = ["fnol", "adjuster_notes", "police_report", "estimate", "repair_invoice", "rental_invoice",
             "tow_invoice", "storage_invoice", "payment_ledger", "photos", "statement", "correspondence", "other"]
LINE_CATEGORIES = ["repair", "total_loss_acv", "rental", "towing", "storage", "other"]


def _num(v, default=None):
    try:
        if v is None or v == "":
            return default
        return float(str(v).replace("$", "").replace(",", ""))
    except ValueError:
        return default


def _iso(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


# ============================================================================ Stage 0: intake
def intake_tools(pages: list[dict], store: dict) -> list[Tool]:
    n_pages = len(pages)

    def record_extraction(a: dict) -> dict:
        blocks, review = [], []
        claim = str(a.get("claim_number") or "").strip()
        loss = _iso(a.get("loss_date"))
        state = str(a.get("loss_state") or "").strip().upper()
        items = a.get("line_items") or []
        if not claim:
            blocks.append("claim_number is missing")
        if not loss:
            blocks.append("loss_date must be YYYY-MM-DD")
        if not state or len(state) != 2:
            blocks.append("loss_state must be a two-letter US state code")
        if not isinstance(items, list) or not items:
            blocks.append("line_items is empty; list every amount Northgate paid")
        clean_items = []
        for it in items if isinstance(items, list) else []:
            amt = _num(it.get("amount"))
            cat = str(it.get("category") or "other")
            pg = int(_num(it.get("page"), 0) or 0)
            if amt is None or amt < 0:
                blocks.append(f"line item '{it.get('description')}' has no valid amount")
                continue
            if pg and not 1 <= pg <= n_pages:
                blocks.append(f"line item '{it.get('description')}' cites page {pg}; the file has {n_pages} pages")
            if cat not in LINE_CATEGORIES:
                cat = "other"
            if "deductible" in str(it.get("description", "")).lower():
                blocks.append("the deductible is not a paid line item; put it in the deductible field")
            clean_items.append({"category": cat, "description": str(it.get("description") or cat), "amount": round(amt, 2), "page": pg or None})
        total = _num(a.get("ledger_total_paid"))
        paid = round(sum(i["amount"] for i in clean_items), 2)
        if total is not None and clean_items and abs(paid - total) > rules.LEDGER_TOLERANCE:
            blocks.append(f"line items add up to {paid:,.2f} but the ledger total is {total:,.2f}; recheck for a missing, duplicate or deductible line")
        doc_pages = a.get("pages") or []
        labels = {}
        for p in doc_pages if isinstance(doc_pages, list) else []:
            n = int(_num(p.get("page"), 0) or 0)
            if 1 <= n <= n_pages:
                t = str(p.get("doc_type") or "other")
                labels[n] = t if t in DOC_TYPES else "other"
        if len(labels) < n_pages:
            blocks.append(f"label every page: {n_pages - len(labels)} of {n_pages} pages have no doc_type")
        if blocks:
            raise Blocked("; ".join(blocks))

        # Soft checks become review tasks for a person rather than blocks.
        cites = []
        for c in a.get("citations") or []:
            pg = quote_page(pages, str(c.get("quote") or ""))
            cites.append({"field": c.get("field"), "quote": c.get("quote"), "page": pg or c.get("page"), "verified": pg is not None})
            if pg is None:
                review.append(f"Check field '{c.get('field')}': its quote was not found in the file")
        if state not in rules.STATES:
            review.append(f"Loss state {state} is not configured. Supported: {', '.join(rules.STATES)}")
        scanned = [p["page_no"] for p in pages if p["scanned"]]
        if scanned:
            review.append(f"Pages {', '.join(map(str, scanned))} have no text layer (scans or photos); OCR or check by eye")
        for m in a.get("missing") or []:
            review.append(f"Missing from file: {m}")
        fault = a.get("adjuster_fault_insured_pct")
        data = {
            "claim_number": claim, "insured_name": a.get("insured_name"), "loss_date": loss.isoformat(), "loss_state": state,
            "loss_location": a.get("loss_location"), "coverage": a.get("coverage"), "loss_description": a.get("loss_description"),
            "other_party_name": a.get("other_party_name"), "other_party_carrier": a.get("other_party_carrier"),
            "other_party_claim_number": a.get("other_party_claim_number"), "police_report_number": a.get("police_report_number"),
            "adjuster_fault_insured_pct": None if fault in (None, "") else _num(fault),
            "deductible": _num(a.get("deductible"), 0.0), "line_items": clean_items, "company_paid": paid,
            "ledger_total_paid": total, "rental_days": _num(a.get("rental_days")), "repair_days": _num(a.get("repair_days")),
            "citations": cites, "missing": [str(m) for m in (a.get("missing") or [])],
        }
        store["extraction"], store["labels"], store["review"] = data, labels, review
        return {"accepted": True, "company_paid": paid, "review_items": len(review)}

    schema = {
        "type": "object",
        "properties": {
            "pages": {"type": "array", "description": "Every page and its document type",
                      "items": {"type": "object", "properties": {"page": {"type": "integer"}, "doc_type": {"type": "string", "enum": DOC_TYPES}}, "required": ["page", "doc_type"]}},
            "claim_number": {"type": "string"}, "insured_name": {"type": "string"},
            "loss_date": {"type": "string", "description": "YYYY-MM-DD"}, "loss_state": {"type": "string", "description": "Two-letter code"},
            "loss_location": {"type": "string"}, "coverage": {"type": "string", "enum": ["collision", "comprehensive", "other"]},
            "loss_description": {"type": "string", "description": "One or two sentences"},
            "other_party_name": {"type": ["string", "null"]}, "other_party_carrier": {"type": ["string", "null"]},
            "other_party_claim_number": {"type": ["string", "null"]}, "police_report_number": {"type": ["string", "null"]},
            "adjuster_fault_insured_pct": {"type": ["number", "null"]},
            "deductible": {"type": "number"},
            "line_items": {"type": "array", "items": {"type": "object", "properties": {
                "category": {"type": "string", "enum": LINE_CATEGORIES}, "description": {"type": "string"},
                "amount": {"type": "number"}, "page": {"type": "integer"}}, "required": ["category", "description", "amount", "page"]}},
            "ledger_total_paid": {"type": ["number", "null"], "description": "Total paid per the payment ledger, if shown"},
            "rental_days": {"type": ["number", "null"]}, "repair_days": {"type": ["number", "null"]},
            "citations": {"type": "array", "items": {"type": "object", "properties": {
                "field": {"type": "string"}, "page": {"type": "integer"}, "quote": {"type": "string"}}, "required": ["field", "page", "quote"]}},
            "missing": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["pages", "claim_number", "loss_date", "loss_state", "coverage", "loss_description", "deductible", "line_items", "citations"],
    }
    return [Tool("record_extraction", "Save the structured claim data. Checked for required fields, page numbers and that line items add up to the ledger.", schema, record_extraction, terminal=True)]


# ============================================================================ Stage 1: detect
NO_PARTY_WORDS = re.compile(r"\b(hail|flood|deer|animal|windstorm|fallen tree|single[- ]vehicle|lost control)\b", re.I)


def detect_tools(pages: list[dict], extraction: dict, store: dict) -> list[Tool]:
    def search_file(a: dict) -> dict:
        return {"matches": search(pages, str(a.get("query") or ""))}

    def record_detection(a: dict) -> dict:
        liable = bool(a.get("third_party_liable"))
        ev = []
        for e in a.get("evidence") or []:
            pg = quote_page(pages, str(e.get("quote") or ""))
            ev.append({"finding": str(e.get("finding") or ""), "quote": str(e.get("quote") or ""), "page": pg, "verified": pg is not None})
        if liable:
            blocks = []
            if not str(a.get("liable_party") or "").strip():
                blocks.append("name the liable party")
            if not any(e["verified"] for e in ev):
                blocks.append("no evidence quote matches the file word for word; quote the file exactly")
            if blocks:
                raise Blocked("; ".join(blocks))
        elif not str(a.get("reason") or "").strip():
            raise Blocked("say why no third party is liable")
        warn = None
        if liable and NO_PARTY_WORDS.search(extraction.get("loss_description") or ""):
            warn = "Loss description suggests no other party; check this finding"
        store["detection"] = {
            "third_party_liable": liable, "liable_party": a.get("liable_party"), "liable_party_carrier": a.get("liable_party_carrier"),
            "basis": a.get("basis") or a.get("reason"), "reason": a.get("reason"), "evidence": ev, "warning": warn,
        }
        return {"accepted": True, "verified_quotes": sum(e["verified"] for e in ev)}

    return [
        Tool("search_file", "Find lines in the claim file containing a word or phrase. Returns page numbers and lines.",
             {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}, search_file),
        Tool("record_detection", "Save whether a third party is liable, who, their insurer, and the evidence with exact quotes.",
             {"type": "object", "properties": {
                 "third_party_liable": {"type": "boolean"},
                 "liable_party": {"type": ["string", "null"]}, "liable_party_carrier": {"type": ["string", "null"]},
                 "basis": {"type": "string", "description": "One or two sentences: why they are responsible"},
                 "reason": {"type": "string", "description": "If not liable: why there is no one to recover from"},
                 "evidence": {"type": "array", "items": {"type": "object", "properties": {
                     "finding": {"type": "string"}, "quote": {"type": "string"}}, "required": ["finding", "quote"]}}},
              "required": ["third_party_liable", "evidence"]},
             record_detection, terminal=True),
    ]


# ============================================================================ Stage 2: validate
def money(extraction: dict, fault_pct: float, likelihood_pct: float) -> dict:
    paid = float(extraction["company_paid"])
    ded = float(extraction.get("deductible") or 0)
    gross = paid + ded
    share = rules.recoverable_share(extraction["loss_state"], fault_pct)
    demand = gross * share
    return {"company_paid": round(paid, 2), "deductible": round(ded, 2), "total_damages": round(gross, 2),
            "recoverable_share_pct": round(share * 100), "barred_by_state_rule": share == 0,
            "demand": round(demand, 2), "expected_recovery": round(demand * likelihood_pct / 100, 2),
            "above_arbitration_limit": paid > rules.AF_CAP}


def validate_tools(extraction: dict, detection: dict, today: date, store: dict) -> list[Tool]:
    state = extraction["loss_state"]
    loss = date.fromisoformat(extraction["loss_date"])
    carrier_name = detection.get("liable_party_carrier") or extraction.get("other_party_carrier")

    def get_state_rules(a: dict) -> dict:
        r = rules.STATES[state]
        return {"state": r.name, "negligence_rule": r.text, **rules.deadline(state, loss, today), "desk_date": today.isoformat()}

    def check_carrier(a: dict) -> dict:
        c = rules.find_carrier(str(a.get("carrier") or carrier_name or ""))
        if not c:
            return {"on_file": False, "note": "No insurer on file for the liable party (uninsured, unknown or not in the registry)."}
        return {"on_file": True, "carrier": c["name"], "company_code": c["code"], "arbitration_forums_member": c["af_member"]}

    def calculate_recovery(a: dict) -> dict:
        f = max(0.0, min(100.0, _num(a.get("insured_fault_pct"), 0) or 0))
        p = max(0.0, min(100.0, _num(a.get("likelihood_pct"), 0) or 0))
        return money(extraction, f, p)

    def record_decision(a: dict) -> dict:
        d = str(a.get("decision") or "")
        f = max(0.0, min(100.0, _num(a.get("insured_fault_pct"), 0) or 0))
        p = max(0.0, min(100.0, _num(a.get("likelihood_pct"), 0) or 0))
        m = money(extraction, f, p)
        dl = rules.deadline(state, loss, today)
        car = rules.find_carrier(carrier_name)
        arb_ok, arb_why = rules.arbitration_available(car, m["company_paid"])
        blocks = []
        if d not in ("pursue", "park", "litigation"):
            blocks.append("decision must be pursue, park or litigation")
        if d == "pursue":
            if dl["status"] == "expired":
                blocks.append(f"the limitation period expired on {dl['expires']}; park it")
            if dl["status"] == "urgent":
                blocks.append(f"only {dl['days_left']} days remain before the deadline; a demand cycle is unsafe, choose litigation (protective suit)")
            if not car:
                blocks.append("no insurer on file: there is no carrier to send a demand to; choose litigation (claim against the individual)")
            if m["barred_by_state_rule"]:
                blocks.append(f"{rules.STATES[state].name}'s fault rule bars recovery at {f:.0f}% insured fault; park it")
            if m["expected_recovery"] < rules.VALUE_FLOOR:
                blocks.append(f"expected recovery ${m['expected_recovery']:,.0f} is under the ${rules.VALUE_FLOOR} floor; park it")
            if p < rules.MIN_LIKELIHOOD:
                blocks.append(f"likelihood {p:.0f}% is under the {rules.MIN_LIKELIHOOD}% minimum for a demand; park it")
        if d == "litigation":
            if dl["status"] == "expired":
                blocks.append(f"the limitation period expired on {dl['expires']}; suit is time-barred, park it")
            if m["barred_by_state_rule"]:
                blocks.append("the state fault rule bars recovery; park it")
            if car and dl["status"] == "open":
                blocks.append("an insurer is on file and the deadline is not close: send a demand first (pursue); litigation comes only if they refuse")
        if (d == "park" and car and dl["status"] != "expired" and not m["barred_by_state_rule"]
                and m["demand"] >= rules.PARK_REVIEW_DEMAND and p >= rules.MIN_LIKELIHOOD):
            blocks.append(f"a ${m['demand']:,.0f} demand at {p:.0f}% likelihood is recoverable; pursue it")
        if blocks:
            raise Blocked("; ".join(blocks) + ".")
        store["decision"] = {
            "decision": d, "reason": str(a.get("reason") or ""), "insured_fault_pct": f, "likelihood_pct": p,
            "fault_override_reason": a.get("fault_override_reason"),
            "weaknesses": [str(x) for x in (a.get("weaknesses") or [])][:5], "money": m, "deadline": dl,
            "carrier": car, "arbitration_available": arb_ok, "arbitration_note": arb_why,
            "channel": ("hub" if car and car.get("af_member") else "email") if d == "pursue" else None,
            "manual_desk_would_pursue": d == "pursue" and m["demand"] >= 3000 and p >= 60,
        }
        return {"accepted": True, "decision": d}

    return [
        Tool("get_state_rules", "The loss state's negligence rule, limitation period, deadline and days left from today's desk date.",
             {"type": "object", "properties": {}}, get_state_rules),
        Tool("check_carrier", "Look up the liable party's insurer: on file or not, company code, Arbitration Forums membership.",
             {"type": "object", "properties": {"carrier": {"type": "string"}}, "required": ["carrier"]}, check_carrier),
        Tool("calculate_recovery", "Compute total damages, recoverable share under the state rule, demand and expected recovery from the ledger.",
             {"type": "object", "properties": {"insured_fault_pct": {"type": "number"}, "likelihood_pct": {"type": "number"}},
              "required": ["insured_fault_pct", "likelihood_pct"]}, calculate_recovery),
        Tool("record_decision", "Save pursue, park or litigation. Guardrails check deadline, state rule, carrier and value.",
             {"type": "object", "properties": {
                 "decision": {"type": "string", "enum": ["pursue", "park", "litigation"]}, "reason": {"type": "string"},
                 "insured_fault_pct": {"type": "number"}, "likelihood_pct": {"type": "number"},
                 "fault_override_reason": {"type": ["string", "null"], "description": "Only if you changed the adjuster's fault split"},
                 "weaknesses": {"type": "array", "items": {"type": "string"}, "description": "What the other carrier will argue"}},
              "required": ["decision", "reason", "insured_fault_pct", "likelihood_pct"]},
             record_decision, terminal=True),
    ]


# ============================================================================ Stage 3: build demand
AMOUNT_RE = re.compile(r"\$\s?\d|\b\d{1,3}(,\d{3})+(\.\d\d)?\b|\b\d+\.\d\d\b")


def build_tools(n_pages: int, store: dict) -> list[Tool]:
    def record_demand(a: dict) -> dict:
        arg = str(a.get("liability_argument") or "").strip()
        blocks = []
        if len(arg) < 120:
            blocks.append("liability_argument is too short; write 2 to 4 sentences citing the evidence")
        if len(arg) > 1400:
            blocks.append("liability_argument is too long; keep it to 4 sentences")
        if AMOUNT_RE.search(arg):
            blocks.append("remove dollar amounts from liability_argument; the system inserts amounts from the ledger")
        exhibits = []
        for ex in a.get("evidence") or []:
            pgs = sorted({int(_num(p, 0) or 0) for p in (ex.get("pages") or [])})
            bad = [p for p in pgs if not 1 <= p <= n_pages]
            if bad:
                blocks.append(f"exhibit '{ex.get('label')}' cites pages {bad}; the file has {n_pages} pages")
            if pgs and not bad:
                exhibits.append({"label": str(ex.get("label") or "Exhibit"), "pages": pgs})
        if not exhibits:
            blocks.append("list at least one exhibit with its pages")
        if blocks:
            raise Blocked("; ".join(blocks))
        store["demand"] = {
            "liability_argument": arg, "exhibits": exhibits,
            "negotiation_message": str(a.get("negotiation_message") or "")[:600],
            "documents_to_obtain": [str(x) for x in (a.get("documents_to_obtain") or [])][:6],
        }
        return {"accepted": True, "exhibits": len(exhibits)}

    return [Tool("record_demand", "Save the fault argument, exhibits (labelled page groups), hub message and missing documents.",
                 {"type": "object", "properties": {
                     "liability_argument": {"type": "string"},
                     "evidence": {"type": "array", "items": {"type": "object", "properties": {
                         "label": {"type": "string"}, "pages": {"type": "array", "items": {"type": "integer"}}}, "required": ["label", "pages"]}},
                     "negotiation_message": {"type": "string"},
                     "documents_to_obtain": {"type": "array", "items": {"type": "string"}}},
                  "required": ["liability_argument", "evidence", "negotiation_message"]},
                 record_demand, terminal=True)]
