"""HTTP API and static frontend for Recovery Desk."""
from __future__ import annotations

import asyncio
import base64
import json
import secrets
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, select

from . import clock, db, lifecycle, rules
from .agent.llm import ProviderError
from .agent.runner import AgentFailed
from .config import settings
from .pipeline import RUNNING, recover_interrupted, run_case

ROOT = Path(__file__).resolve().parent
SAMPLES = ROOT.parent / "samples"


@asynccontextmanager
async def lifespan(_app):
    db.init_db()
    recover_interrupted()
    db.set_setting("advancing", False)
    yield


app = FastAPI(title="Recovery Desk", version="2.0", lifespan=lifespan)


# ------------------------------------------------------------------ basic sign-in
@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if not settings.app_password or request.url.path == "/api/health":
        return await call_next(request)
    header = request.headers.get("authorization", "")
    if header.startswith("Basic "):
        try:
            _user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
            if secrets.compare_digest(pw, settings.app_password):
                return await call_next(request)
        except Exception:
            pass
    return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Recovery Desk"'})


# ------------------------------------------------------------------ helpers
BUCKETS = {
    "reading": {"received", "extracting", "detecting", "validating", "building"},
    "negotiating": {"with_carrier", "agent_working"},
    "awaiting_payment": {"awaiting_payment"},
    "in_arbitration": {"in_arbitration"},
    "needs_you": {"needs_you", "awaiting_approval", "needs_review", "failed"},
    "recovered": {"recovered"},
    "closed": {"parked", "closed", "with_counsel", "rejected"},
}


def _summary(c: db.Case) -> dict:
    dec = c.decision or {}
    m = dec.get("money") or {}
    neg = c.neg or {}
    return {
        "id": c.id, "claim_no": c.claim_no, "title": c.title, "status": c.status, "stage": c.stage,
        "decision": dec.get("decision"), "demand": neg.get("demand", m.get("demand")), "expected": m.get("expected_recovery"),
        "recovered": (c.recovery or {}).get("recovered"), "action": c.action,
        "carrier": (dec.get("carrier") or {}).get("name"), "channel": c.channel,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        "agent_only": dec.get("decision") == "pursue" and not dec.get("manual_desk_would_pursue"),
    }


def _get(case_id: str) -> db.Case:
    c = db.get_case(case_id)
    if not c:
        raise HTTPException(404, "Case not found")
    return c


def _safe(fn, case_id: str, *args):
    """Run a lifecycle step in the background; record model or code failures on the case."""
    try:
        fn(case_id, *args)
    except (AgentFailed, ProviderError) as e:
        lifecycle._fail(case_id, str(e))
    except Exception as e:
        traceback.print_exc()
        lifecycle._fail(case_id, f"Unexpected error: {e}")


def _create_case(filename: str, content: bytes, bg: BackgroundTasks) -> dict:
    if not content.startswith(b"%PDF"):
        raise HTTPException(400, "That file is not a PDF.")
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"PDF is larger than {settings.max_upload_mb} MB.")
    with db.session() as s:
        c = db.Case(title=filename)
        s.add(c)
        s.flush()
        s.add(db.File(case_id=c.id, kind="upload", filename=filename, content=content))
        cid = c.id
    db.add_event(cid, 0, "status", "received", {"note": f"Uploaded {filename} ({len(content) // 1024} KB)"})
    bg.add_task(run_case, cid, 0)
    return {"id": cid}


# ------------------------------------------------------------------ home, settings, clock
@app.get("/api/health")
def health():
    return {"ok": True, "provider": settings.llm_provider, "model": settings.llm_model, "tool_mode": settings.llm_tool_mode,
            "key_set": bool(settings.llm_api_key), "desk_date": clock.today().isoformat()}


@app.get("/api/config")
def config():
    return {"provider": settings.llm_provider, "model": settings.llm_model, "tool_mode": settings.llm_tool_mode,
            "states": {k: v.name for k, v in rules.STATES.items()}, "samples": sorted(p.stem for p in SAMPLES.glob("*.pdf")),
            **clock.all_settings()}


@app.get("/api/home")
def home():
    with db.session() as s:
        cases = s.scalars(select(db.Case).order_by(db.Case.updated_at.desc())).all()
        payments = s.scalars(select(db.Payment).order_by(db.Payment.id.desc()).limit(20)).all()
        pay_rows = [{"date": p.received_on, "payer": p.payer, "amount": p.amount, "reference": p.reference, "status": p.status} for p in payments]
    rows = [_summary(c) for c in cases]
    buckets = {k: [r for r in rows if r["status"] in v] for k, v in BUCKETS.items()}
    order = {"settle": 0, "arbitration": 1, "litigation": 2, "request": 3, "approve": 4, "check": 5}
    needs = sorted(buckets["needs_you"], key=lambda r: order.get((r["action"] or {}).get("kind", "check"), 9))
    return {
        "settings": clock.all_settings(), "advancing": bool(db.get_setting("advancing", False)),
        "needs_you": needs,
        "counts": {k: len(v) for k, v in buckets.items()},
        "totals": {
            "recovered": sum(r["recovered"] or 0 for r in rows),
            "in_play": sum(r["demand"] or 0 for r in rows if r["status"] in BUCKETS["negotiating"] | BUCKETS["awaiting_payment"] | BUCKETS["in_arbitration"]),
            "files": len(rows),
        },
        "pipeline": rows, "payments": pay_rows,
    }


@app.put("/api/settings")
async def put_settings(request: Request):
    body = await request.json()
    if "settle_pct" in body:
        v = int(body["settle_pct"])
        if not 50 <= v <= 100:
            raise HTTPException(400, "Settlement threshold must be between 50% and 100%.")
        db.set_setting("settle_pct", v)
    if "auto_send_limit" in body:
        v = int(body["auto_send_limit"])
        if v < 0:
            raise HTTPException(400, "Auto-send limit cannot be negative.")
        db.set_setting("auto_send_limit", v)
    return clock.all_settings()


@app.post("/api/clock/advance")
def advance(bg: BackgroundTasks, days: int = 7):
    if not 1 <= days <= 90:
        raise HTTPException(400, "Advance between 1 and 90 days.")
    if db.get_setting("advancing", False):
        raise HTTPException(409, "The clock is already moving.")
    db.set_setting("advancing", True)
    bg.add_task(lifecycle.tick, days)
    return {"advancing": True}


# ------------------------------------------------------------------ cases
@app.post("/api/cases")
async def upload(file: UploadFile, bg: BackgroundTasks):
    return _create_case(file.filename or "claim.pdf", await file.read(), bg)


@app.post("/api/samples/{name}")
def from_sample(name: str, bg: BackgroundTasks):
    if name == "all":
        ids = [_create_case(p.name, p.read_bytes(), bg)["id"] for p in sorted(SAMPLES.glob("*.pdf"))]
        return {"ids": ids}
    p = SAMPLES / f"{Path(name).name}.pdf"
    if not p.exists():
        raise HTTPException(404, "No such sample")
    return _create_case(p.name, p.read_bytes(), bg)


@app.get("/api/cases")
def list_cases():
    with db.session() as s:
        return [_summary(c) for c in s.scalars(select(db.Case).order_by(db.Case.created_at.desc())).all()]


@app.get("/api/cases/{case_id}")
def get_case(case_id: str):
    c = _get(case_id)
    with db.session() as s:
        pages = s.scalars(select(db.Page).where(db.Page.case_id == case_id).order_by(db.Page.page_no)).all()
        files = s.scalars(select(db.File.kind).where(db.File.case_id == case_id)).all()
    return {**_summary(c), "extraction": c.extraction, "detection": c.detection, "decision": c.decision, "demand": c.demand,
            "review": c.review or [], "error": c.error, "model": c.model, "neg": c.neg, "messages": c.messages or [],
            "escalation": c.escalation, "recovery": c.recovery, "settle_pct": clock.get("settle_pct"),
            "pages": [{"page_no": p.page_no, "doc_type": p.doc_type, "chars": len(p.text), "text": p.text} for p in pages],
            "files": sorted(set(files))}


@app.get("/api/cases/{case_id}/events")
def events(case_id: str, after: int = 0):
    return db.events_after(case_id, after)


@app.get("/api/cases/{case_id}/stream")
async def stream(case_id: str, request: Request, after: int = 0):
    """Server-sent events: every audit event as it is written."""
    _get(case_id)
    last = int(request.headers.get("last-event-id") or after or 0)

    async def gen():
        nonlocal last
        idle = 0
        while True:
            if await request.is_disconnected():
                return
            rows = await asyncio.to_thread(db.events_after, case_id, last)
            for e in rows:
                last = e["id"]
                yield f"id: {e['id']}\nevent: agent\ndata: {json.dumps(e, default=str)}\n\n"
            idle = 0 if rows else idle + 1
            if idle and idle % 20 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.6)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/cases/{case_id}/rerun")
def rerun(case_id: str, bg: BackgroundTasks, from_stage: int = 0):
    c = _get(case_id)
    if c.status in RUNNING:
        raise HTTPException(409, "This case is already running.")
    if c.stage >= 4 and c.status not in ("failed",):
        raise HTTPException(409, "The demand is already out. Reruns apply to stages 0 to 3.")
    need = {1: c.extraction, 2: c.detection, 3: c.decision}
    for st in range(1, from_stage + 1):
        if st in need and not need[st]:
            raise HTTPException(400, f"Stage {st - 1} has no result yet; rerun from an earlier stage.")
    if from_stage == 0:
        with db.session() as s:
            s.execute(delete(db.Page).where(db.Page.case_id == case_id))
    db.update_case(case_id, messages=[], neg=None, escalation=None, recovery=None, channel=None, action=None)
    db.add_event(case_id, from_stage, "human", "rerun", {"from_stage": from_stage, "model": settings.llm_model})
    bg.add_task(run_case, case_id, from_stage)
    return {"ok": True}


# ------------------------------------------------------------------ user decisions (the only places a person acts)
ACTIONS = {
    # action kind -> allowed verbs
    "approve": {"send", "reject"},
    "settle": {"accept", "decline"},
    "arbitration": {"file", "settle_offer", "close"},
    "litigation": {"refer", "settle_offer", "close"},
    "request": {"provide", "cannot_provide"},
    "check": {"retry", "close"},
}


@app.post("/api/cases/{case_id}/do/{verb}")
async def do(case_id: str, verb: str, bg: BackgroundTasks, request: Request):
    c = _get(case_id)
    kind = (c.action or {}).get("kind")
    if c.status == "failed":
        kind = "check"
    if not kind or verb not in ACTIONS.get(kind, set()):
        raise HTTPException(409, f"'{verb}' is not available for this case right now.")
    note, fname, content = "", None, None
    ctype = request.headers.get("content-type", "")
    if "multipart/form-data" in ctype:
        form = await request.form()
        note = str(form.get("note") or "")
        f = form.get("file")
        if f is not None and hasattr(f, "read"):
            fname, content = f.filename, await f.read()
    elif "json" in ctype:
        note = str((await request.json()).get("note") or "")
    if verb == "send":
        db.add_event(case_id, 3, "human", "approved", {"note": note})
        bg.add_task(_safe, lifecycle.send_demand, case_id, True)
    elif verb == "reject":
        db.add_event(case_id, 3, "human", "rejected", {"note": note})
        db.update_case(case_id, status="rejected", action=None)
    elif verb == "accept":
        bg.add_task(_safe, lifecycle.user_settle, case_id)
    elif verb == "decline":
        db.update_case(case_id, status="agent_working")
        bg.add_task(_safe, lifecycle.user_escalate, case_id)
    elif verb == "file":
        bg.add_task(_safe, lifecycle.user_file_arbitration, case_id)
    elif verb == "refer":
        bg.add_task(_safe, lifecycle.user_refer_counsel, case_id)
    elif verb == "settle_offer":
        try:
            lifecycle.user_settle_last_offer(case_id)
        except ValueError as e:
            raise HTTPException(400, str(e))
    elif verb == "close":
        bg.add_task(_safe, lifecycle.user_close, case_id)
    elif verb == "provide":
        bg.add_task(_safe, lifecycle.user_provide, case_id, note, fname, content)
    elif verb == "cannot_provide":
        db.update_case(case_id, status="agent_working")
        bg.add_task(_safe, lifecycle.user_cannot_provide, case_id, note)
    elif verb == "retry":
        db.update_case(case_id, status="agent_working", action=None)
        bg.add_task(_safe, lifecycle.retry, case_id)
    return {"ok": True}


@app.delete("/api/cases/{case_id}")
def remove(case_id: str):
    _get(case_id)
    with db.session() as s:
        for model in (db.Event, db.Page, db.File):
            s.execute(delete(model).where(model.case_id == case_id))
        s.execute(delete(db.Case).where(db.Case.id == case_id))
    return {"ok": True}


@app.post("/api/reset")
def reset():
    """Demo reset: remove all cases, payments and the simulated clock."""
    if db.get_setting("advancing", False):
        raise HTTPException(409, "Wait for the clock to stop.")
    with db.session() as s:
        for model in (db.Event, db.Page, db.File, db.Payment, db.Case):
            s.execute(delete(model))
    db.set_setting("sim_day", 0)
    return {"ok": True}


@app.get("/api/cases/{case_id}/files/{kind}")
def download(case_id: str, kind: str, download: bool = False):
    with db.session() as s:
        f = s.scalars(select(db.File).where(db.File.case_id == case_id, db.File.kind == kind).order_by(db.File.id.desc())).first()
        if not f:
            raise HTTPException(404, "File not found")
        disp = "attachment" if download else "inline"
        mt = "application/pdf" if f.content.startswith(b"%PDF") else "application/octet-stream"
        return Response(f.content, media_type=mt, headers={"Content-Disposition": f'{disp}; filename="{f.filename}"'})


@app.get("/api/cases/{case_id}/hub-payload")
def payload(case_id: str):
    c = _get(case_id)
    if not c.demand:
        raise HTTPException(404, "No demand yet")
    return JSONResponse(c.demand["hub_payload"])


# ------------------------------------------------------------------ frontend
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/cases/{case_id}")
def case_page(case_id: str):
    return FileResponse(ROOT / "static" / "index.html")
