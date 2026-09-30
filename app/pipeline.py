"""Runs a case through stages 0-3 and records every status change.

Stages:  0 intake -> 1 detect -> 2 validate -> 3 build -> 4 send (auto under the limit, else approval)
Exits:   needs_review (intake gaps), parked, litigation referral (stage 6), failed (rerunnable)
Stages 4-7 continue in lifecycle.py.
"""
from __future__ import annotations

import traceback

from sqlalchemy import delete, select

from . import clock, db, documents, lifecycle, rules
from .agent import prompts, stages
from .agent.llm import ProviderError
from .agent.runner import AgentFailed, run_agent
from .config import settings
from .pdftext import extract_pages, file_for_prompt

RUNNING = {"extracting", "detecting", "validating", "building", "agent_working"}


def _set(case_id: str, **fields) -> None:
    with db.session() as s:
        c = s.get(db.Case, case_id)
        for k, v in fields.items():
            setattr(c, k, v)


def _status(case_id: str, stage: int, status: str, note: str = "") -> None:
    _set(case_id, status=status, stage=stage)
    db.add_event(case_id, stage, "status", status, {"note": note} if note else {})


def _load(case_id: str):
    with db.session() as s:
        c = s.get(db.Case, case_id)
        up = s.scalars(select(db.File).where(db.File.case_id == case_id, db.File.kind == "upload")).first()
        pages = s.scalars(select(db.Page).where(db.Page.case_id == case_id).order_by(db.Page.page_no)).all()
        return c, up, [{"page_no": p.page_no, "text": p.text, "scanned": len(p.text) < 25, "doc_type": p.doc_type} for p in pages]


def run_case(case_id: str, from_stage: int = 0) -> None:
    try:
        _run(case_id, from_stage)
    except (AgentFailed, ProviderError) as e:
        _fail(case_id, str(e))
    except Exception as e:  # keep the server alive, record what broke
        traceback.print_exc()
        _fail(case_id, f"Unexpected error: {e}")


def _fail(case_id: str, msg: str) -> None:
    with db.session() as s:
        stage = s.get(db.Case, case_id).stage
    _set(case_id, status="failed", error=msg, action={"kind": "check", "text": f"The agent could not finish: {msg} Use Retry."})
    db.add_event(case_id, stage, "error", "failed", {"message": msg})


def _run(case_id: str, from_stage: int) -> None:
    today = clock.today()
    _set(case_id, error=None, action=None, model=f"{settings.llm_provider}:{settings.llm_model}")
    case, upload, pages = _load(case_id)

    # ---- Stage 0: intake
    if from_stage <= 0:
        _status(case_id, 0, "extracting")
        if not pages:
            pages = extract_pages(upload.content)
            with db.session() as s:
                s.execute(delete(db.Page).where(db.Page.case_id == case_id))
                for p in pages:
                    s.add(db.Page(case_id=case_id, page_no=p["page_no"], text=p["text"]))
            db.add_event(case_id, 0, "tool", "read_pdf", {"args": {"file": upload.filename},
                         "result": {"pages": len(pages), "pages_without_text": [p["page_no"] for p in pages if p["scanned"]]}})
        store: dict = {}
        run_agent(case_id, 0, prompts.INTAKE, "CLAIM FILE\n\n" + file_for_prompt(pages), stages.intake_tools(pages, store))
        ex = store["extraction"]
        with db.session() as s:
            for p in s.scalars(select(db.Page).where(db.Page.case_id == case_id)).all():
                p.doc_type = store["labels"].get(p.page_no)
        _set(case_id, extraction=ex, review=store["review"], claim_no=ex["claim_number"],
             title=f"{ex.get('insured_name') or 'Unknown'} · {ex['loss_state']} · {ex['loss_date']}")
        if ex["loss_state"] not in rules.STATES:
            _set(case_id, action={"kind": "check", "text": f"Loss state {ex['loss_state']} is not configured. Add it to the rules, then retry."})
            _status(case_id, 0, "needs_review", f"State {ex['loss_state']} is not configured")
            return
    case, upload, pages = _load(case_id)
    ex = case.extraction

    # ---- Stage 1: detect
    if from_stage <= 1:
        _status(case_id, 1, "detecting")
        store = {}
        user = f"EXTRACTED FACTS\n{_facts(ex)}\n\nCLAIM FILE\n\n{file_for_prompt(pages)}"
        run_agent(case_id, 1, prompts.DETECT, user, stages.detect_tools(pages, ex, store))
        _set(case_id, detection=store["detection"])
        if not store["detection"]["third_party_liable"]:
            dec = {"decision": "park", "reason": "No liable third party: " + (store["detection"].get("reason") or ""),
                   "decided_by": "code (detection found no one to recover from)"}
            _set(case_id, decision=dec)
            db.add_event(case_id, 2, "tool", "skip_validation", {"args": {}, "result": dec})
            _status(case_id, 2, "parked", dec["reason"])
            return
    case, upload, pages = _load(case_id)
    det = case.detection

    # ---- Stage 2: validate
    if from_stage <= 2:
        _status(case_id, 2, "validating")
        store = {}
        user = f"EXTRACTED FACTS\n{_facts(ex)}\n\nDETECTION\n{det}\n\nCLAIM FILE\n\n{file_for_prompt(pages)}"
        run_agent(case_id, 2, prompts.VALIDATE, user, stages.validate_tools(ex, det, today, store))
        dec = store["decision"]
        _set(case_id, decision=dec)
        if dec["decision"] == "park":
            _status(case_id, 2, "parked", dec["reason"])
            return
        if dec["decision"] == "litigation":
            _status(case_id, 2, "validating", "No demand possible: " + dec["reason"])
            lifecycle.build_packet(case_id, "litigation", dec["reason"])
            return
    case, upload, pages = _load(case_id)
    dec = case.decision

    # ---- Stage 3: build
    _status(case_id, 3, "building")
    store = {}
    labelled = "\n".join(f"page {p['page_no']}: {p['doc_type'] or 'unlabelled'}" for p in pages)
    user = (f"EXTRACTED FACTS\n{_facts(ex)}\n\nDETECTION\n{det}\n\nDECISION\n{dec}\n\nPAGE LABELS\n{labelled}\n\n"
            f"CLAIM FILE\n\n{file_for_prompt(pages)}")
    run_agent(case_id, 3, prompts.BUILD, user, stages.build_tools(len(pages), store))
    dem = store["demand"]
    letter = documents.letter_pdf(ex, dec, dem, det, today)
    bundle = documents.bundle_pdf(upload.content, ex, dem)
    dem["hub_payload"] = documents.hub_payload(ex, dec, dem, det)
    with db.session() as s:
        s.execute(delete(db.File).where(db.File.case_id == case_id, db.File.kind.in_(["letter", "bundle"])))
        s.add(db.File(case_id=case_id, kind="letter", filename=f"demand-{ex['claim_number']}.pdf", content=letter))
        s.add(db.File(case_id=case_id, kind="bundle", filename=f"evidence-{ex['claim_number']}.pdf", content=bundle))
    _set(case_id, demand=dem)
    db.add_event(case_id, 3, "tool", "assemble_package", {"args": {}, "result": {
        "letter_pages": "generated", "bundle_exhibits": len(dem["exhibits"]), "demand_amount": dec["money"]["demand"]}})
    limit = clock.get("auto_send_limit")
    if limit and dec["money"]["demand"] <= limit:
        lifecycle.send_demand(case_id)
    else:
        _set(case_id, action={"kind": "approve", "text": f"Demand of ${dec['money']['demand']:,.2f} is above your auto-send limit of ${limit:,.0f}. Review and send."})
        _status(case_id, 3, "awaiting_approval", "Demand is above the auto-send limit; waiting for approval")


def _facts(ex: dict) -> str:
    keep = {k: v for k, v in ex.items() if k not in ("citations",)}
    return "\n".join(f"{k}: {v}" for k, v in keep.items())


def recover_interrupted() -> None:
    """On startup, mark runs that were cut off by a restart as failed so they can be rerun."""
    with db.session() as s:
        for c in s.scalars(select(db.Case).where(db.Case.status.in_(RUNNING))).all():
            c.status, c.error = "failed", "Server restarted during the run. Use Retry."
            c.action = {"kind": "check", "text": "The server restarted while the agent was working. Use Retry."}
