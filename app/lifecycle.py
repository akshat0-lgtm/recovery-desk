"""Stages 4-7: send, negotiate and follow up, escalate, collect and reconcile.

The agent owns every open demand. It acts on each event (carrier message, silence, short payment) and
only hands a case to the user for: a settlement below the threshold, an arbitration filing to review,
a litigation referral to review, or something the carrier needs that is not in the file.
"""
from __future__ import annotations

import threading
import traceback
from datetime import timedelta

from sqlalchemy import select

from . import clock, db, documents, rules, sim
from .adapters import get_payments
from .agent import prompts
from .agent.llm import ProviderError
from .agent.negotiate import arbitration_tools, litigation_tools, negotiate_tools
from .agent.runner import AgentFailed, run_agent
from .pdftext import file_for_prompt

ACTIVE = {"with_carrier", "awaiting_payment", "in_arbitration"}
LOCK = threading.Lock()


def _date(day: int) -> str:
    return (clock.today() + timedelta(days=day - clock.day_no())).isoformat()


def _msg(case_id: str, day: int, direction: str, kind: str, text: str, channel: str | None = None, amount: float | None = None) -> None:
    c = db.get_case(case_id)
    msgs = list(c.messages or [])
    msgs.append({"day": day, "date": _date(day), "dir": direction, "kind": kind, "text": text, "channel": channel or c.channel, "amount": amount})
    db.update_case(case_id, messages=msgs)
    db.add_event(case_id, 5 if direction != "system" else 7, "carrier" if direction == "in" else "tool",
                 "carrier_message" if direction == "in" else f"sent_{kind}", {"text": text[:600], "amount": amount, "date": _date(day)})


def _pages(case_id: str) -> list[dict]:
    with db.session() as s:
        return [{"page_no": p.page_no, "text": p.text, "scanned": len(p.text) < 25, "doc_type": p.doc_type}
                for p in s.scalars(select(db.Page).where(db.Page.case_id == case_id).order_by(db.Page.page_no)).all()]


def _schedule_next(neg: dict, from_day: int) -> None:
    steps = sim.STEPS[neg["profile"]]
    if neg["step"] < len(steps):
        neg["next"] = {"day": from_day + steps[neg["step"]], "kind": "carrier"}
    else:
        neg["next"] = {"day": from_day + sim.SILENCE_GAP, "kind": "silence"}


def _status(case_id: str, stage: int, status: str, note: str = "", **fields) -> None:
    db.update_case(case_id, stage=stage, status=status, **fields)
    db.add_event(case_id, stage, "status", status, {"note": note} if note else {})


# ============================================================================ stage 4: send
def send_demand(case_id: str, approved_by_user: bool = False) -> None:
    c = db.get_case(case_id)
    dec, dem = c.decision, c.demand
    channel = dec.get("channel") or "email"
    day = clock.day_no()
    neg = {"demand": round(dec["money"]["demand"], 2), "original": round(dec["money"]["demand"], 2), "sent_day": day,
           "profile": sim.profile_for(c.claim_no), "step": 0, "silences": 0, "agreed": None, "paid": 0.0, "next": None,
           "last_offer": None, "balance_requested": False}
    _schedule_next(neg, day)
    target = (dec.get("carrier") or {}).get("name")
    how = "E-Subro Hub (simulated)" if channel == "hub" else "email to the carrier's subrogation inbox (simulated)"
    db.update_case(case_id, channel=channel, neg=neg, action=None)
    _msg(case_id, day, "out", "demand", f"Demand issued to {target} via {how}.\n\n{dem.get('negotiation_message', '')}\n\n"
         f"Demand: ${neg['demand']:,.2f}. Response due {_date(day + 30)}. Attachments: demand letter, evidence bundle.", channel, neg["demand"])
    _status(case_id, 5, "with_carrier", ("Approved by user and sent" if approved_by_user else "Sent automatically (under the auto-send limit)") + f" via {channel}")


# ============================================================================ clock
def tick(days: int) -> None:
    """Move the desk clock forward day by day, firing carrier moves, follow-ups, payments and forum decisions."""
    if not LOCK.acquire(blocking=False):
        return
    try:
        db.set_setting("advancing", True)
        for _ in range(days):
            day = clock.day_no() + 1
            db.set_setting("sim_day", day)
            with db.session() as s:
                ids = [c.id for c in s.scalars(select(db.Case).where(db.Case.status.in_(ACTIVE))).all()]
            for cid in ids:
                c = db.get_case(cid)
                n = (c.neg or {}).get("next")
                if n and n["day"] <= day:
                    try:
                        _fire(cid, day)
                    except (AgentFailed, ProviderError) as e:
                        _fail(cid, str(e))
                    except Exception as e:
                        traceback.print_exc()
                        _fail(cid, f"Unexpected error: {e}")
    finally:
        db.set_setting("advancing", False)
        LOCK.release()


def _fail(case_id: str, msg: str) -> None:
    c = db.get_case(case_id)
    db.update_case(case_id, error=msg, status="failed", action={"kind": "check", "text": f"The agent could not finish: {msg} Use Retry."})
    db.add_event(case_id, c.stage, "error", "failed", {"message": msg})


def _fire(case_id: str, day: int) -> None:
    c = db.get_case(case_id)
    neg = dict(c.neg)
    kind = neg["next"]["kind"]
    neg["next"] = None
    if kind == "carrier":
        m = sim.carrier_message(c)
        neg["step"] += 1
        if m.get("offer") is not None:
            neg["last_offer"] = m["offer"]
        db.update_case(case_id, neg=neg)
        _msg(case_id, day, "in", m["kind"], m["text"], amount=m.get("offer"))
        negotiate(case_id, {"kind": "carrier_message", "carrier_kind": m["kind"], "text": m["text"], "offer": m.get("offer"),
                            "requested": m.get("requested"), "day": day})
    elif kind == "silence":
        neg["silences"] += 1
        db.update_case(case_id, neg=neg)
        since = day - neg["sent_day"]
        text = f"No reply from the carrier {since} days after the demand was sent (no-reply count {neg['silences']})."
        db.add_event(case_id, 5, "system", "silence", {"text": text, "date": _date(day)})
        negotiate(case_id, {"kind": "silence", "text": text, "offer": None, "day": day})
    elif kind == "payment":
        amt = neg.pop("pay_amount")
        db.update_case(case_id, neg=neg)
        payer = (c.decision.get("carrier") or {}).get("name") or "Carrier"
        with db.session() as s:
            p = db.Payment(received_on=_date(day), payer=payer, amount=amt, reference=f"{c.extraction.get('other_party_claim_number') or ''} / {c.claim_no}")
            s.add(p)
            s.flush()
            pid = p.id
        db.add_event(case_id, 7, "system", "payment_received", {"payer": payer, "amount": amt, "date": _date(day), "source": "bank feed (simulated)"})
        reconcile(pid, day)
    elif kind == "forum":
        award, why = sim.forum_award(c)
        esc = dict(c.escalation)
        esc["award"], esc["decision_text"] = award, why
        neg["agreed"] = award
        neg["pay_amount"] = award
        neg["next"] = {"day": day + sim.PAYMENT_LAG, "kind": "payment"}
        db.update_case(case_id, escalation=esc, neg=neg)
        _msg(case_id, day, "in", "award", f"Arbitration Forums decision (simulated): {why} Award ${award:,.2f}.", channel="forum", amount=award)
        _status(case_id, 7, "awaiting_payment", f"Award ${award:,.2f}; payment due within {sim.PAYMENT_LAG} days")


# ============================================================================ stage 5: negotiate
def negotiate(case_id: str, event: dict) -> None:
    c = db.get_case(case_id)
    pages = _pages(case_id)
    _status(case_id, 5, "agent_working", "Agent is handling: " + event["kind"].replace("_", " "))
    store: dict = {}
    neg = dict(c.neg)
    neg["last_event"] = event
    db.update_case(case_id, neg=neg)
    settle_pct = clock.get("settle_pct")
    ctx = {"extraction": c.extraction, "decision": c.decision, "neg": neg, "event": event, "n_pages": len(pages), "settle_pct": settle_pct}
    history = "\n\n".join(f"[{m['date']}] {m['dir'].upper()} {m['kind']}: {m['text']}" for m in (c.messages or [])[-10:])
    labels = "\n".join(f"page {p['page_no']}: {p['doc_type'] or 'unlabelled'}" for p in pages)
    arb_ok, arb_why = rules.arbitration_available(c.decision.get("carrier"), c.decision["money"]["company_paid"])
    user = (f"CASE {c.claim_no}\nDesk date: {_date(event['day'])}. Days since demand sent: {event['day'] - neg['sent_day']}. No-reply count: {neg['silences']}.\n"
            f"Current demand: ${neg['demand']:,.2f} (original ${neg['original']:,.2f}). Settlement threshold: {settle_pct}% = ${neg['demand'] * settle_pct / 100:,.2f}.\n"
            f"Arbitration available: {arb_ok} ({arb_why}).\n"
            f"Insured fault {c.decision['insured_fault_pct']}%, likelihood {c.decision['likelihood_pct']}%.\n"
            f"Rental days {c.extraction.get('rental_days')}, repair days {c.extraction.get('repair_days')}.\n"
            f"Evidence found: {'; '.join(e['finding'] for e in (c.detection or {}).get('evidence', []) if e.get('verified'))}\n\n"
            f"PAGE LABELS\n{labels}\n\nCORRESPONDENCE SO FAR\n{history}\n\n"
            f"NEW EVENT ({event['kind']})\n{event['text']}\n\nCLAIM FILE\n\n{file_for_prompt(pages, 40_000)}")
    run_agent(case_id, 5, prompts.NEGOTIATE, user, negotiate_tools(ctx, store))
    _apply(case_id, event, store, neg)


def _apply(case_id: str, event: dict, store: dict, neg: dict) -> None:
    day = event["day"]
    act = store["act"]
    for cn in store.get("concessions", []):
        _msg(case_id, day, "out", "concession", f"Conceded ${cn['amount']:,.2f} on {cn['item']}: {cn['reason']}", amount=cn["amount"])
    t = act["type"]
    if t == "accept":
        neg["agreed"] = act["amount"]
        c = db.get_case(case_id)
        c.neg = neg
        neg["pay_amount"] = sim.first_payment(c, act["amount"]) if event["kind"] != "short_payment" else None
        if event["kind"] == "short_payment":  # accepting what was already paid closes the file
            db.update_case(case_id, neg=neg)
            _msg(case_id, day, "out", "accept", f"Accepted ${act['amount']:,.2f} as full settlement. {act.get('note', '')}", amount=act["amount"])
            return _close_recovered(case_id, day)
        neg["next"] = {"day": day + sim.PAYMENT_LAG, "kind": "payment"}
        db.update_case(case_id, neg=neg)
        _msg(case_id, day, "out", "accept", f"Accepted ${act['amount']:,.2f}. {act.get('note', '')}".strip(), amount=act["amount"])
        _status(case_id, 7, "awaiting_payment", f"Settled at ${act['amount']:,.2f}; payment due within {sim.PAYMENT_LAG} days")
    elif t == "settle_decision":
        db.update_case(case_id, neg=neg)
        pct = act["amount"] / neg["demand"] * 100
        arb_ok, _ = rules.arbitration_available(db.get_case(case_id).decision.get("carrier"), db.get_case(case_id).decision["money"]["company_paid"])
        _status(case_id, 5, "needs_you", f"Offer of ${act['amount']:,.2f} is below your threshold",
                action={"kind": "settle", "amount": act["amount"], "pct": round(pct, 1), "alt": "arbitration" if arb_ok else "litigation",
                        "text": f"The carrier offers ${act['amount']:,.2f}, {pct:.0f}% of the ${neg['demand']:,.2f} demand. That is below your settlement threshold. {act.get('note', '')}".strip()})
    elif t in ("counter", "message", "documents"):
        if t == "counter":
            _msg(case_id, day, "out", "counter", act["message"], amount=act["amount"])
        elif t == "message":
            _msg(case_id, day, "out", act["purpose"], act["message"])
        else:
            _msg(case_id, day, "out", "documents", f"Sent {act['label']} (file pages {', '.join(map(str, act['pages']))}). {act['message']}".strip())
        if t == "message" and act["purpose"] == "balance_request" and neg["profile"] == "short_pay" and not neg.get("balance_requested"):
            neg["balance_requested"] = True
            neg["pay_amount"] = round((neg["agreed"] or neg["demand"]) - neg["paid"], 2)
            neg["next"] = {"day": day + 12, "kind": "payment"}
            db.update_case(case_id, neg=neg)
            return _status(case_id, 7, "awaiting_payment", "Balance requested from the carrier")
        _schedule_next(neg, day)
        db.update_case(case_id, neg=neg)
        _status(case_id, 5, "with_carrier", "Waiting for the carrier")
    elif t == "user_request":
        db.update_case(case_id, neg=neg)
        _status(case_id, 5, "needs_you", "The carrier needs something that is not in the file",
                action={"kind": "request", "item": act["item"], "text": f"The carrier asked for {act['item']}. It is not in the claim file. {act.get('why', '')}".strip()})
    elif t == "escalate":
        db.update_case(case_id, neg=neg)
        build_packet(case_id, act["route"], act["reason"])


# ============================================================================ stage 6: escalation packets
def build_packet(case_id: str, route: str, reason: str) -> None:
    c = db.get_case(case_id)
    neg = dict(c.neg or {})
    neg["escalating"] = {"route": route, "reason": reason}
    db.update_case(case_id, neg=neg)
    pages = _pages(case_id)
    _status(case_id, 6, "agent_working", f"Preparing {'arbitration filing' if route == 'arbitration' else 'litigation referral'}")
    store: dict = {}
    labels = "\n".join(f"page {p['page_no']}: {p['doc_type'] or 'unlabelled'}" for p in pages)
    history = "\n\n".join(f"[{m['date']}] {m['dir'].upper()} {m['kind']}: {m['text']}" for m in (c.messages or [])[-12:])
    user = (f"CASE {c.claim_no}\nReason for escalation: {reason}\nDecision: {c.decision}\nDetection: {c.detection}\n\n"
            f"PAGE LABELS\n{labels}\n\nCORRESPONDENCE\n{history or 'none'}\n\nCLAIM FILE\n\n{file_for_prompt(pages, 40_000)}")
    if route == "arbitration":
        run_agent(case_id, 6, prompts.ARBITRATION, user, arbitration_tools(len(pages), store))
    else:
        run_agent(case_id, 6, prompts.LITIGATION, user, litigation_tools(len(pages), store))
    packet = store["packet"]
    m = c.decision["money"]
    dl = rules.deadline(c.extraction["loss_state"], __import__("datetime").date.fromisoformat(c.extraction["loss_date"]), clock.today())
    packet.update({"reason": reason, "damages": m, "demand": (c.neg or {}).get("demand", m["demand"]), "deadline": dl,
                   "last_offer": (c.neg or {}).get("last_offer"), "prepared_on": clock.today().isoformat()})
    pdf = documents.escalation_pdf(c.extraction, c.decision, c.detection, packet, clock.today())
    with db.session() as s:
        s.add(db.File(case_id=case_id, kind=route, filename=f"{route}-{c.claim_no}.pdf", content=pdf))
    text = (f"Requires arbitration. {reason} The filing is drafted for review; Arbitration Forums decides on the written evidence."
            if route == "arbitration" else
            f"Requires litigation. {reason} A referral memo for counsel is drafted. Deadline to sue: {dl['expires']} ({dl['days_left']} days).")
    _status(case_id, 6, "needs_you", text, escalation=packet,
            action={"kind": route, "text": text, "last_offer": packet["last_offer"]})


# ============================================================================ stage 7: collect and reconcile
def reconcile(payment_id: int, day: int) -> None:
    with db.session() as s:
        p = s.get(db.Payment, payment_id)
        match = None
        for c in s.scalars(select(db.Case).where(db.Case.claim_no.isnot(None))).all():
            if c.claim_no and c.claim_no in p.reference:
                match = c
                break
        if not match:
            p.status = "unmatched"
            return
        p.case_id = match.id
        amt, cid = p.amount, match.id
    c = db.get_case(cid)
    neg = dict(c.neg)
    neg["paid"] = round(neg.get("paid", 0) + amt, 2)
    agreed = neg.get("agreed") or neg["demand"]
    rec = dict(c.recovery or {"payments": []})
    rec["payments"] = rec.get("payments", []) + [{"id": payment_id, "date": _date(day), "amount": amt}]
    db.update_case(cid, neg=neg, recovery=rec)
    if neg["paid"] >= agreed - 1:
        with db.session() as s:
            s.get(db.Payment, payment_id).status = "matched"
        db.add_event(cid, 7, "tool", "reconcile", {"args": {"payment": payment_id}, "result": {"matched": c.claim_no, "paid": neg["paid"], "agreed": agreed}})
        return _close_recovered(cid, day)
    with db.session() as s:
        s.get(db.Payment, payment_id).status = "short"
    short = round(agreed - neg["paid"], 2)
    db.add_event(cid, 7, "tool", "reconcile", {"args": {"payment": payment_id}, "result": {"matched": c.claim_no, "paid": neg["paid"], "agreed": agreed, "short_by": short}})
    negotiate(cid, {"kind": "short_payment", "day": day, "offer": neg["paid"],
                    "text": f"Payment received: ${amt:,.2f}. Total paid ${neg['paid']:,.2f} against the agreed ${agreed:,.2f}: short by ${short:,.2f}."})


def _close_recovered(case_id: str, day: int) -> None:
    c = db.get_case(case_id)
    ex, m = c.extraction, c.decision["money"]
    got = c.neg["paid"]
    refund = rules.deductible_refund(ex["loss_state"], m["deductible"], m["total_damages"], got)
    pay = get_payments()
    booked = pay.book_recovery(c.claim_no, got)
    refunded = pay.refund_deductible(c.claim_no, refund, ex.get("insured_name") or "insured")
    with db.session() as s:
        for p in s.scalars(select(db.Payment).where(db.Payment.case_id == case_id)).all():
            p.status = "matched"
    rec = dict(c.recovery or {})
    rec.update({"recovered": got, "recovered_on": _date(day), "deductible_refund": refund,
                "refund_rule": "full deductible first" if ex["loss_state"] in rules.FULL_FIRST_STATES else "pro rata: deductible ÷ total damages × recovery",
                "booking": booked, "refund": refunded, "cycle_days": day - c.neg["sent_day"]})
    db.update_case(case_id, recovery=rec, action=None)
    db.add_event(case_id, 7, "tool", "book_recovery", {"args": {"claim": c.claim_no}, "result": {
        "recovered": got, "deductible_refund_to_insured": refund, "ledger": "queued to finance (stub)"}})
    _status(case_id, 7, "recovered", f"Recovered ${got:,.2f}. Deductible refund ${refund:,.2f} queued to finance.")


# ============================================================================ user decisions
def user_settle(case_id: str) -> None:
    c = db.get_case(case_id)
    amt = c.action["amount"]
    neg = dict(c.neg)
    neg["agreed"] = amt
    c.neg = neg
    neg["pay_amount"] = sim.first_payment(c, amt)
    neg["next"] = {"day": clock.day_no() + sim.PAYMENT_LAG, "kind": "payment"}
    db.update_case(case_id, neg=neg, action=None)
    db.add_event(case_id, 5, "human", "settled", {"note": f"User accepted ${amt:,.2f}"})
    _msg(case_id, clock.day_no(), "out", "accept", f"Accepted ${amt:,.2f} in full settlement.", amount=amt)
    _status(case_id, 7, "awaiting_payment", f"Settled at ${amt:,.2f}")


def user_escalate(case_id: str) -> None:
    c = db.get_case(case_id)
    route = c.action.get("alt", "arbitration")
    db.add_event(case_id, 5, "human", "declined_offer", {"note": f"User declined the offer; {route}"})
    db.update_case(case_id, action=None)
    build_packet(case_id, route, f"The carrier's best offer (${c.action['amount']:,.2f}) is below what we will accept.")


def user_file_arbitration(case_id: str) -> None:
    c = db.get_case(case_id)
    neg = dict(c.neg or {"demand": c.decision["money"]["demand"], "sent_day": clock.day_no(), "profile": "deny", "step": 0, "silences": 0, "paid": 0.0})
    neg["next"] = {"day": clock.day_no() + sim.FORUM_DAYS, "kind": "forum"}
    db.update_case(case_id, neg=neg, action=None)
    db.add_event(case_id, 6, "human", "arbitration_filed", {"note": "User approved; filing submitted to Arbitration Forums (simulated)"})
    _msg(case_id, clock.day_no(), "out", "arbitration_filing", "Arbitration filing submitted with contentions, evidence index and damages worksheet.", channel="forum")
    _status(case_id, 6, "in_arbitration", f"Filed. Decision expected in about {sim.FORUM_DAYS} days")


def user_refer_counsel(case_id: str) -> None:
    db.add_event(case_id, 6, "human", "referred_to_counsel", {"note": "User referred the case to counsel"})
    _status(case_id, 6, "with_counsel", "Referred to counsel with the agent's memo", action=None)


def user_settle_last_offer(case_id: str) -> None:
    c = db.get_case(case_id)
    offer = (c.escalation or {}).get("last_offer") or (c.neg or {}).get("last_offer")
    if not offer:
        raise ValueError("There is no offer on the table to accept.")
    db.update_case(case_id, action={"kind": "settle", "amount": offer})
    user_settle(case_id)


def user_close(case_id: str) -> None:
    db.add_event(case_id, db.get_case(case_id).stage, "human", "closed", {"note": "User closed the file with no further recovery"})
    _status(case_id, db.get_case(case_id).stage, "closed", "Closed by user", action=None)


def user_provide(case_id: str, note: str, filename: str | None, content: bytes | None) -> None:
    c = db.get_case(case_id)
    item = c.action.get("item", "requested documents")
    if content:
        with db.session() as s:
            s.add(db.File(case_id=case_id, kind="user_doc", filename=filename or "document", content=content))
    db.add_event(case_id, 5, "human", "provided", {"note": f"User supplied {item}. {note}".strip()})
    day = clock.day_no()
    _msg(case_id, day, "out", "documents", f"Sent {item}{' (' + filename + ')' if filename else ''}. {note}".strip())
    neg = dict(c.neg)
    _schedule_next(neg, day)
    db.update_case(case_id, neg=neg, action=None)
    _status(case_id, 5, "with_carrier", "Documents sent; waiting for the carrier")


def user_cannot_provide(case_id: str, note: str) -> None:
    c = db.get_case(case_id)
    item = c.action.get("item", "the requested item")
    db.add_event(case_id, 5, "human", "cannot_provide", {"note": note})
    db.update_case(case_id, action=None)
    negotiate(case_id, {"kind": "user_reply", "day": clock.day_no(), "offer": None,
                        "text": f"The user cannot supply {item}. Their note: {note or 'none'}. Reply to the carrier without it, using what the file has."})


def retry(case_id: str) -> None:
    """Retry after a model failure: re-run the step the case was on."""
    c = db.get_case(case_id)
    db.update_case(case_id, error=None, action=None)
    neg = c.neg or {}
    if c.stage <= 3:
        from .pipeline import run_case
        return run_case(case_id, c.stage)
    if c.stage == 6 and neg.get("escalating"):
        return build_packet(case_id, neg["escalating"]["route"], neg["escalating"]["reason"])
    if neg.get("last_event"):
        return negotiate(case_id, neg["last_event"])
    _status(case_id, c.stage, "with_carrier", "Retrying")
