"""End-to-end tests with a scripted fake model. No API key needed.

The fake plays only the model's side (which tool to call, with what arguments). Everything else is real:
PDF parsing, tools, guardrails, database, documents, the simulated carrier, clock, bank feed and forum, and the API.
Run: pytest -q
"""
import io
import os
import re
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["DESK_DATE"] = "2026-09-30"
os.environ["LLM_API_KEY"] = "test"

from fastapi.testclient import TestClient  # noqa: E402

from app.agent import llm  # noqa: E402
from app.agent.llm import ToolCall, Turn, _first_json, plain_schema  # noqa: E402
from app.main import app  # noqa: E402


def extraction(**over):
    base = {
        "pages": [{"page": i, "doc_type": "other"} for i in range(1, 7)],
        "claim_number": "NG-25-10412", "insured_name": "Priya Raman", "loss_date": "2025-11-03", "loss_state": "CA",
        "loss_location": "Los Angeles", "coverage": "collision", "loss_description": "Rear-ended at a red light.",
        "other_party_name": "Marcus Webb", "other_party_carrier": "Harborline Insurance", "other_party_claim_number": "HL-7730192",
        "police_report_number": "25-110331", "adjuster_fault_insured_pct": 0, "deductible": 500,
        "line_items": [{"category": "towing", "description": "Metro Towing", "amount": 185, "page": 6},
                       {"category": "repair", "description": "Westside Collision", "amount": 6840, "page": 6},
                       {"category": "rental", "description": "Pacific Car Rental", "amount": 1120, "page": 6}],
        "ledger_total_paid": 8145, "rental_days": 16, "repair_days": 15,
        "citations": [{"field": "claim_number", "page": 1, "quote": "NG-25-10412"}], "missing": [],
    }
    base.update(over)
    return base


def detection(party, carrier, quote):
    return {"third_party_liable": True, "liable_party": party, "liable_party_carrier": carrier, "basis": "They caused it.",
            "evidence": [{"finding": "Evidence", "quote": quote}]}


ARG = ("Our insured was stopped when your insured struck the vehicle. The police report records the other driver's fault, "
       "and the adjuster's notes document the admission and the damage that followed from the collision.")


def demand_turns(pages):
    return [[("record_demand", {"liability_argument": ARG, "evidence": [{"label": "File", "pages": pages}], "negotiation_message": "Please accept."})]]


def neg_policy(user: str):
    """Scripted negotiator: reads the event like the model would and picks one action (a tuple = retry after a block)."""
    kind = re.search(r"NEW EVENT \((\w+)\)", user).group(1)
    event = user.split("NEW EVENT", 1)[1].split("CLAIM FILE", 1)[0]
    demand = float(re.search(r"Current demand: \$([\d,.]+) ", user).group(1).replace(",", ""))
    threshold = float(re.search(r"= \$([\d,]+\.\d\d)", user).group(1).replace(",", ""))
    arb = "Arbitration available: True" in user
    m = re.search(r"(?:pay|offer|to|on) \$([\d,]+(?:\.\d\d)?)", event)
    offer = float(m.group(1).replace(",", "")) if m else None
    if kind == "short_payment":
        return [("send_message", {"purpose": "balance_request", "message": "Your payment was short of the agreed amount. Please remit the balance."})]
    if kind == "silence":
        n = int(re.search(r"no-reply count (\d+)", event).group(1))
        if n >= 4:
            return [("escalate", {"route": "arbitration" if arb else "litigation", "reason": "No response after a final notice."})]
        return [("send_message", {"purpose": "followup", "message": "Following up on our demand. Please confirm receipt and your position."})]
    if "please send" in event.lower():
        return [("request_from_user", {"item": "color photographs of the damage", "why": "Not in the claim file."})]
    if "deny" in event.lower():
        return ([("escalate", {"route": "litigation", "reason": "Denied."})],
                [("escalate", {"route": "arbitration" if arb else "litigation", "reason": "Liability denied."})])
    if "rental period" in event:
        return [("send_message", {"purpose": "rebuttal", "message": "The rental matches the repair period on the shop invoice, including the documented parts delay."})]
    if offer is not None and (offer >= threshold - 1 or "final" in event.lower()):
        return [("accept_offer", {"amount": offer})]
    if offer is not None:
        return [("send_counter", {"amount": round(demand * 0.95, 2), "message": "The police citation and video establish liability. We counter at this amount."})]
    return [("send_message", {"purpose": "info", "message": "Thank you, noted on our file."})]


class Fake:
    scripts: dict = {}

    def __init__(self, s, system, user, tools):
        names = [t.name for t in tools]
        rec = [n for n in names if n.startswith("record_")]
        self.key = rec[0] if rec else "negotiate"
        if self.key == "negotiate":
            out = neg_policy(user)
            self.turns = [[c] for c in out] if isinstance(out, list) else [[c] for turn in out for c in turn]
        else:
            self.turns = list(Fake.scripts[self.key])

    def send(self):
        calls = self.turns.pop(0) if self.turns else []
        return Turn(text="scripted", tool_calls=[ToolCall(f"c{i}", n, a) for i, (n, a) in enumerate(calls)])

    def add_tool_results(self, results):
        pass

    def add_user(self, text):
        pass


llm.conversation_factory = Fake
client = TestClient(app)
client.__enter__()

ARB_FILING = [[("record_filing", {"contentions": ARG + " " + ARG, "liability_requested_pct": 100,
                                  "evidence_index": [{"label": "Notes", "pages": [2], "proves": "fault"}],
                                  "anticipated_defenses": [{"defense": "Sudden stop", "response": "No evidence of it."}]})]]
LIT_MEMO = [[("record_referral", {"summary": ARG, "defendant": "The other driver", "why_litigation": "Arbitration not available.",
                                  "evidence_index": [{"label": "Police report", "pages": [3]}], "risks": ["Collectability"]})]]


def case(cid):
    return client.get(f"/api/cases/{cid}").json()


def advance(days):
    r = client.post(f"/api/clock/advance?days={days}")
    assert r.status_code == 200, r.text


def events(cid):
    return client.get(f"/api/cases/{cid}/events").json()


def start(sample, ex, det, decision, pages=(3, 6)):
    Fake.scripts = {"record_extraction": [[("record_extraction", ex)]], "record_detection": [[("record_detection", det)]],
                    "record_decision": decision, "record_demand": demand_turns(list(pages)),
                    "record_filing": ARB_FILING, "record_referral": LIT_MEMO}
    return client.post(f"/api/samples/{sample}").json()["id"]


def pursue(fault=0, likelihood=90):
    return [[("get_state_rules", {}), ("check_carrier", {"carrier": "x"})],
            [("record_decision", {"decision": "pursue", "reason": "Clear.", "insured_fault_pct": fault, "likelihood_pct": likelihood})]]


def test_request_from_user_then_paid_and_reconciled():
    client.post("/api/reset")
    cid = start("rear-end-california", extraction(), detection("Marcus Webb", "Harborline Insurance", "OV admits he looked down at his phone"), pursue())
    c = case(cid)
    assert c["status"] == "with_carrier" and c["channel"] == "hub", (c["status"], c["error"])  # auto-sent under the $25k limit
    advance(14)
    c = case(cid)
    assert c["status"] == "needs_you" and c["action"]["kind"] == "request", (c["status"], c["error"])
    assert any(r["id"] == cid for r in client.get("/api/home").json()["needs_you"])
    r = client.post(f"/api/cases/{cid}/do/provide", files={"file": ("photos.pdf", io.BytesIO(b"%PDF-1.4 photos"), "application/pdf")}, data={"note": "Photos attached"})
    assert r.status_code == 200, r.text
    assert case(cid)["status"] == "with_carrier"
    advance(12)
    assert case(cid)["status"] == "awaiting_payment"
    advance(10)
    c = case(cid)
    assert c["status"] == "recovered", (c["status"], c["error"])
    assert c["recovery"]["recovered"] == 8645 and c["recovery"]["deductible_refund"] == 500


def test_short_payment_chased_then_recovered():
    cid = start("parked-car-georgia",
                extraction(claim_number="NG-25-10877", loss_state="GA", loss_date="2025-12-12", other_party_carrier="Keystone Auto Casualty",
                           line_items=[{"category": "repair", "description": "Body shop", "amount": 1260, "page": 3}], ledger_total_paid=1260,
                           pages=[{"page": i, "doc_type": "other"} for i in range(1, 4)], citations=[]),
                detection("Kelsey Tran", "Keystone Auto Casualty", "admitted backing into IV"), pursue(), pages=(2, 3))
    advance(16)
    advance(10)
    c = case(cid)
    assert c["status"] == "awaiting_payment", (c["status"], c["error"])
    assert any(m["kind"] == "balance_request" for m in c["messages"])
    advance(12)
    c = case(cid)
    assert c["status"] == "recovered" and c["recovery"]["recovered"] == 1760


def test_low_final_offer_goes_to_user_then_litigation_referral():
    cid = start("truck-total-loss-texas",
                extraction(claim_number="NG-26-00144", loss_state="TX", loss_date="2026-02-03", other_party_carrier="Summit Road Insurance",
                           deductible=1000, line_items=[{"category": "total_loss_acv", "description": "ACV", "amount": 104000, "page": 4},
                                                        {"category": "towing", "description": "Tow", "amount": 650, "page": 4},
                                                        {"category": "storage", "description": "Storage", "amount": 1200, "page": 4},
                                                        {"category": "rental", "description": "Rental", "amount": 2100, "page": 4}],
                           ledger_total_paid=107950, pages=[{"page": i, "doc_type": "other"} for i in range(1, 5)], citations=[]),
                detection("Lone Star Freight LLC", "Summit Road Insurance", "PR cites truck driver for unsafe lane change"), pursue(likelihood=85), pages=(2, 3))
    assert case(cid)["status"] == "awaiting_approval"
    client.post(f"/api/cases/{cid}/do/send")
    advance(21)
    advance(14)
    c = case(cid)
    assert c["action"] and c["action"]["kind"] == "settle" and c["action"]["alt"] == "litigation", (c["status"], c["action"], c["error"])
    client.post(f"/api/cases/{cid}/do/decline")
    c = case(cid)
    assert c["action"]["kind"] == "litigation" and "litigation" in c["files"], (c["status"], c["error"])
    client.post(f"/api/cases/{cid}/do/refer")
    assert case(cid)["status"] == "with_counsel"


def test_denial_blocked_from_litigation_then_arbitration_award():
    cid = start("lane-change-florida",
                extraction(claim_number="NG-25-11304", loss_state="FL", loss_date="2025-09-07", other_party_carrier="Bluewater Mutual",
                           line_items=[{"category": "repair", "description": "Repair", "amount": 4380, "page": 3},
                                       {"category": "rental", "description": "Rental", "amount": 420, "page": 3}],
                           ledger_total_paid=4800, pages=[{"page": i, "doc_type": "other"} for i in range(1, 4)], citations=[]),
                detection("Olivia Park", "Bluewater Mutual", "Both drivers claim the other merged"), pursue(likelihood=40), pages=(2, 3))
    advance(25)
    c = case(cid)
    assert c["action"] and c["action"]["kind"] == "arbitration", (c["status"], c["error"])
    assert any("Choose arbitration" in str(e["payload"].get("result")) for e in events(cid) if e["kind"] == "guard")
    client.post(f"/api/cases/{cid}/do/file")
    assert case(cid)["status"] == "in_arbitration"
    advance(30)
    advance(10)
    c = case(cid)
    assert c["status"] == "recovered" and c["escalation"]["award"] > 0, (c["status"], c["error"])


def test_silence_follow_ups_then_arbitration():
    cid = start("rear-end-florida-no-reply",
                extraction(claim_number="NG-25-11966", loss_state="FL", loss_date="2025-10-05", other_party_carrier="Summit Road Insurance",
                           line_items=[{"category": "repair", "description": "Repair", "amount": 7650, "page": 4}], ledger_total_paid=7650,
                           pages=[{"page": i, "doc_type": "other"} for i in range(1, 5)], citations=[]),
                detection("Sofia Mendes", "Summit Road Insurance", "PR cites OV for careless driving"), pursue(), pages=(3, 4))
    advance(60)
    c = case(cid)
    assert len([m for m in c["messages"] if m["kind"] == "followup"]) == 3 and c["action"]["kind"] == "arbitration", (c["status"], c["error"])


def test_uninsured_goes_straight_to_litigation():
    cid = start("uninsured-driver-georgia",
                extraction(claim_number="NG-25-11801", loss_state="GA", loss_date="2025-07-14", other_party_carrier=None, other_party_claim_number=None,
                           line_items=[{"category": "repair", "description": "Repair", "amount": 9800, "page": 4},
                                       {"category": "rental", "description": "Rental", "amount": 840, "page": 4}], ledger_total_paid=10640,
                           pages=[{"page": i, "doc_type": "other"} for i in range(1, 5)], citations=[]),
                {"third_party_liable": True, "liable_party": "Travis Kemp", "liable_party_carrier": None, "basis": "Rear-ended.",
                 "evidence": [{"finding": "No insurance", "quote": "OV had no insurance"}]},
                [[("record_decision", {"decision": "pursue", "reason": "x", "insured_fault_pct": 0, "likelihood_pct": 85})],
                 [("record_decision", {"decision": "litigation", "reason": "Uninsured driver; claim against the individual.", "insured_fault_pct": 0, "likelihood_pct": 85})]])
    c = case(cid)
    assert c["action"] and c["action"]["kind"] == "litigation", (c["status"], c["error"])
    assert any("no insurer on file" in str(e["payload"].get("result")) for e in events(cid) if e["kind"] == "guard")


def test_contributory_state_parks_and_settings_slider():
    cid = start("intersection-maryland",
                extraction(claim_number="NG-25-11020", loss_state="MD", loss_date="2025-10-18", adjuster_fault_insured_pct=20,
                           line_items=[{"category": "repair", "description": "Body shop", "amount": 8120, "page": 4},
                                       {"category": "rental", "description": "Rental", "amount": 640, "page": 4}],
                           ledger_total_paid=8760, citations=[], pages=[{"page": i, "doc_type": "other"} for i in range(1, 5)]),
                detection("Greg Tolliver", "Pinecrest General", "OV failed to yield on left turn"),
                [[("record_decision", {"decision": "pursue", "reason": "x", "insured_fault_pct": 20, "likelihood_pct": 80})],
                 [("record_decision", {"decision": "park", "reason": "Contributory negligence bars recovery.", "insured_fault_pct": 20, "likelihood_pct": 80})]])
    assert case(cid)["status"] == "parked"
    assert client.put("/api/settings", json={"settle_pct": 92}).json()["settle_pct"] == 92
    assert client.put("/api/settings", json={"settle_pct": 20}).status_code == 400
    client.put("/api/settings", json={"settle_pct": 85})


def test_invented_quote_fails_cleanly_and_shows_in_needs_you():
    Fake.scripts = {"record_extraction": [[("record_extraction", extraction())]],
                    "record_detection": [[("record_detection", {"third_party_liable": True, "liable_party": "X",
                                                                "evidence": [{"finding": "made up", "quote": "the driver was drunk and speeding wildly"}]})]]}
    cid = client.post("/api/samples/rear-end-california").json()["id"]
    c = case(cid)
    assert c["status"] == "failed" and c["action"]["kind"] == "check"
    assert any("word for word" in e["payload"]["result"] for e in events(cid) if e["kind"] == "guard")


def test_parsers():
    assert _first_json('Sure:\n```json\n{"tool": "x", "arguments": {"a": 1}}\n```')["tool"] == "x"
    assert plain_schema({"type": ["string", "null"], "items": {"type": ["number", "null"]}}) == {"type": "string", "items": {"type": "number"}}
