"""Stage 5 (negotiate and follow up) and stage 6 (arbitration filing, litigation referral) tools.

The model picks one action per event. Code enforces: the settlement threshold, offers it can't exceed,
page references that exist, concessions capped per line, and arbitration only where it is available.
"""
from __future__ import annotations

from .. import rules
from .runner import Blocked, Tool
from .stages import AMOUNT_RE, _num


def negotiate_tools(ctx: dict, store: dict) -> list[Tool]:
    """ctx: extraction, decision, neg, event, n_pages, settle_pct."""
    ex, dec, neg, ev = ctx["extraction"], ctx["decision"], ctx["neg"], ctx["event"]
    threshold = round(neg["demand"] * ctx["settle_pct"] / 100, 2)
    acted = {"done": False}

    def once(name):
        if acted["done"]:
            raise Blocked("you already took this turn's action")

    def finish(act: dict, result: dict) -> dict:
        acted["done"] = True
        store["act"] = act
        return result

    def accept_offer(a):
        once("accept_offer")
        amt = round(_num(a.get("amount"), 0) or 0, 2)
        offered = ev.get("offer")
        if offered is None:
            raise Blocked("the carrier has not offered or committed to an amount in this event")
        if amt > offered + 1:
            raise Blocked(f"the carrier offered ${offered:,.2f}; you cannot accept more than that")
        if amt >= threshold - 0.5:
            return finish({"type": "accept", "amount": amt, "note": str(a.get("note") or "")},
                          {"result": "accepted", "threshold": threshold})
        return finish({"type": "settle_decision", "amount": amt, "note": str(a.get("note") or "")},
                      {"result": f"below the settlement threshold of ${threshold:,.2f}: sent to the user to decide"})

    def send_counter(a):
        once("send_counter")
        amt = round(_num(a.get("amount"), 0) or 0, 2)
        if amt < threshold - 0.5 or amt > neg["demand"] + 0.5:
            raise Blocked(f"a counter must be between the settlement threshold ${threshold:,.2f} and the demand ${neg['demand']:,.2f}")
        msg = str(a.get("message") or "").strip()
        if len(msg) < 30:
            raise Blocked("write the counter message, citing the evidence")
        return finish({"type": "counter", "amount": amt, "message": msg}, {"sent": True})

    def send_message(a):
        once("send_message")
        msg = str(a.get("message") or "").strip()
        if len(msg) < 30:
            raise Blocked("the message is empty or too short")
        purpose = str(a.get("purpose") or "info")
        return finish({"type": "message", "purpose": purpose, "message": msg}, {"sent": True})

    def send_documents(a):
        once("send_documents")
        pages = sorted({int(_num(p, 0) or 0) for p in (a.get("pages") or [])})
        bad = [p for p in pages if not 1 <= p <= ctx["n_pages"]]
        if not pages or bad:
            raise Blocked(f"give page numbers that exist in the file (1 to {ctx['n_pages']})")
        msg = str(a.get("message") or "").strip()
        return finish({"type": "documents", "pages": pages, "label": str(a.get("label") or "Documents"), "message": msg}, {"sent": True})

    def request_from_user(a):
        once("request_from_user")
        item = str(a.get("item") or "").strip()
        if not item:
            raise Blocked("say what the user needs to supply")
        return finish({"type": "user_request", "item": item, "why": str(a.get("why") or "")}, {"raised": True})

    def concede_line_item(a):
        cats = {"rental": "rental", "towing": "towing", "storage": "storage"}
        cat = cats.get(str(a.get("item") or ""))
        if not cat:
            raise Blocked("only rental, towing or storage charges can be conceded")
        line = sum(i["amount"] for i in ex["line_items"] if i["category"] == cat)
        cap = line * dec["money"]["recoverable_share_pct"] / 100
        amt = round(_num(a.get("amount"), 0) or 0, 2)
        if amt <= 0 or amt > cap + 0.5:
            raise Blocked(f"you can concede at most ${cap:,.2f} on {cat}")
        neg["demand"] = round(neg["demand"] - amt, 2)
        store.setdefault("concessions", []).append({"item": cat, "amount": amt, "reason": str(a.get("reason") or "")})
        return {"new_demand": neg["demand"]}

    def escalate(a):
        once("escalate")
        route = str(a.get("route") or "")
        ok, why = rules.arbitration_available(dec.get("carrier"), dec["money"]["company_paid"])
        if route == "arbitration" and not ok:
            raise Blocked(f"arbitration is not available: {why}. Choose litigation")
        if route == "litigation" and ok:
            raise Blocked("arbitration is available and compulsory between these carriers for this amount. Choose arbitration")
        if route not in ("arbitration", "litigation"):
            raise Blocked("route must be arbitration or litigation")
        return finish({"type": "escalate", "route": route, "reason": str(a.get("reason") or "")}, {"escalated": route})

    S = {"type": "object"}
    return [
        Tool("accept_offer", f"Accept the carrier's offer or payment commitment. At or above ${threshold:,.2f} it is accepted; below it goes to the user.",
             {**S, "properties": {"amount": {"type": "number"}, "note": {"type": "string"}}, "required": ["amount"]}, accept_offer, terminal=True),
        Tool("send_counter", f"Counter-offer with a message. Amount between ${threshold:,.2f} and ${neg['demand']:,.2f}.",
             {**S, "properties": {"amount": {"type": "number"}, "message": {"type": "string"}}, "required": ["amount", "message"]}, send_counter, terminal=True),
        Tool("send_message", "Send a message to the carrier.",
             {**S, "properties": {"purpose": {"type": "string", "enum": ["acknowledgment_check", "followup", "final_notice", "rebuttal", "balance_request", "info"]},
                                  "message": {"type": "string"}}, "required": ["purpose", "message"]}, send_message, terminal=True),
        Tool("send_documents", "Send pages from the claim file the carrier asked for, with a short cover message.",
             {**S, "properties": {"label": {"type": "string"}, "pages": {"type": "array", "items": {"type": "integer"}}, "message": {"type": "string"}},
              "required": ["label", "pages"]}, send_documents, terminal=True),
        Tool("request_from_user", "Ask the user for something the carrier needs that is not in the claim file.",
             {**S, "properties": {"item": {"type": "string"}, "why": {"type": "string"}}, "required": ["item"]}, request_from_user, terminal=True),
        Tool("concede_line_item", "Reduce the demand for a rental, towing or storage charge the file does not support. Not an action on its own.",
             {**S, "properties": {"item": {"type": "string", "enum": ["rental", "towing", "storage"]}, "amount": {"type": "number"}, "reason": {"type": "string"}},
              "required": ["item", "amount", "reason"]}, concede_line_item),
        Tool("escalate", "Stop negotiating and prepare arbitration or litigation for the user.",
             {**S, "properties": {"route": {"type": "string", "enum": ["arbitration", "litigation"]}, "reason": {"type": "string"}},
              "required": ["route", "reason"]}, escalate, terminal=True),
    ]


def _exhibits(raw, n_pages) -> list[dict]:
    out = []
    for e in raw or []:
        pgs = sorted({int(_num(p, 0) or 0) for p in (e.get("pages") or [])})
        if not pgs or any(not 1 <= p <= n_pages for p in pgs):
            raise Blocked(f"exhibit '{e.get('label')}' must cite pages 1 to {n_pages}")
        out.append({"label": str(e.get("label") or "Exhibit"), "pages": pgs, "proves": str(e.get("proves") or "")})
    if not out:
        raise Blocked("list at least one exhibit")
    return out


def arbitration_tools(n_pages: int, store: dict) -> list[Tool]:
    def record_filing(a):
        text = str(a.get("contentions") or "").strip()
        if len(text) < 200:
            raise Blocked("contentions are too short; write 4 to 8 sentences citing exhibits")
        if AMOUNT_RE.search(text):
            raise Blocked("remove dollar amounts from contentions; the system fills damages")
        store["packet"] = {
            "type": "arbitration", "contentions": text, "evidence_index": _exhibits(a.get("evidence_index"), n_pages),
            "anticipated_defenses": [{"defense": str(d.get("defense") or ""), "response": str(d.get("response") or "")} for d in (a.get("anticipated_defenses") or [])][:5],
            "liability_requested_pct": max(0, min(100, _num(a.get("liability_requested_pct"), 100) or 100)),
        }
        return {"accepted": True}

    return [Tool("record_filing", "Save the arbitration filing for review.",
                 {"type": "object", "properties": {
                     "contentions": {"type": "string"}, "liability_requested_pct": {"type": "number"},
                     "evidence_index": {"type": "array", "items": {"type": "object", "properties": {
                         "label": {"type": "string"}, "pages": {"type": "array", "items": {"type": "integer"}}, "proves": {"type": "string"}},
                         "required": ["label", "pages", "proves"]}},
                     "anticipated_defenses": {"type": "array", "items": {"type": "object", "properties": {
                         "defense": {"type": "string"}, "response": {"type": "string"}}, "required": ["defense", "response"]}}},
                  "required": ["contentions", "liability_requested_pct", "evidence_index"]},
                 record_filing, terminal=True)]


def litigation_tools(n_pages: int, store: dict) -> list[Tool]:
    def record_referral(a):
        summary = str(a.get("summary") or "").strip()
        if len(summary) < 150:
            raise Blocked("summary is too short; write 3 to 6 sentences")
        if AMOUNT_RE.search(summary):
            raise Blocked("remove dollar amounts from the summary; the system fills damages")
        store["packet"] = {
            "type": "litigation", "summary": summary, "defendant": str(a.get("defendant") or ""),
            "why_litigation": str(a.get("why_litigation") or ""), "evidence_index": _exhibits(a.get("evidence_index"), n_pages),
            "risks": [str(r) for r in (a.get("risks") or [])][:5],
        }
        return {"accepted": True}

    return [Tool("record_referral", "Save the litigation referral memo for counsel.",
                 {"type": "object", "properties": {
                     "summary": {"type": "string"}, "defendant": {"type": "string"}, "why_litigation": {"type": "string"},
                     "evidence_index": {"type": "array", "items": {"type": "object", "properties": {
                         "label": {"type": "string"}, "pages": {"type": "array", "items": {"type": "integer"}}, "proves": {"type": "string"}},
                         "required": ["label", "pages", "proves"]}},
                     "risks": {"type": "array", "items": {"type": "string"}}},
                  "required": ["summary", "defendant", "why_litigation", "evidence_index"]},
                 record_referral, terminal=True)]
