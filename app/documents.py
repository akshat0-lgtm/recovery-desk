"""Stage 3 outputs built by code: demand letter PDF, evidence bundle PDF, E-Subro Hub demand payload.

Every amount comes from the extracted ledger. The only model-written text is the liability argument
and the hub negotiation message.
"""
from __future__ import annotations

import io
from datetime import date, timedelta

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import rules

LABELS = {"repair": "Collision repair", "total_loss_acv": "Actual cash value (total loss)", "rental": "Rental",
          "towing": "Towing", "storage": "Storage", "other": "Other"}


def _styles():
    ss = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=ss["Normal"], fontName="Helvetica", fontSize=10, leading=14)
    small = ParagraphStyle("small", parent=body, fontSize=8.5, leading=11, textColor=colors.HexColor("#555555"))
    head = ParagraphStyle("head", parent=body, fontName="Helvetica-Bold", fontSize=12, leading=16)
    return body, small, head


def _esc(s) -> str:
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def letter_pdf(ex: dict, dec: dict, dem: dict, det: dict, today: date) -> bytes:
    body, small, head = _styles()
    m = dec["money"]
    carrier = (dec.get("carrier") or {}).get("name") or det.get("liable_party_carrier") or "Claims Department"
    due = today + timedelta(days=30)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER, leftMargin=0.9 * inch, rightMargin=0.9 * inch, topMargin=0.8 * inch, bottomMargin=0.8 * inch,
                            title=f"Subrogation demand {ex['claim_number']}")
    s = [Paragraph(f"{_esc(rules.OUR_COMPANY['name'])}<br/>Subrogation Unit", head), Spacer(1, 10),
         Paragraph(today.strftime("%B %d, %Y"), body), Spacer(1, 10)]
    ref = [["To:", f"{carrier}, Claims Department"], ["Re:", "Subrogation demand"],
           ["Our insured:", ex.get("insured_name") or ""], ["Your insured:", det.get("liable_party") or ex.get("other_party_name") or ""],
           ["Your claim no.:", ex.get("other_party_claim_number") or "unknown"], ["Our claim no.:", ex["claim_number"]],
           ["Date of loss:", date.fromisoformat(ex["loss_date"]).strftime("%B %d, %Y")],
           ["Loss state:", rules.STATES.get(ex["loss_state"]).name if ex["loss_state"] in rules.STATES else ex["loss_state"]]]
    if ex.get("police_report_number"):
        ref.append(["Police report:", ex["police_report_number"]])
    t = Table([[Paragraph(f"<b>{_esc(a)}</b>", body), Paragraph(_esc(b), body)] for a, b in ref], colWidths=[1.4 * inch, 5.2 * inch])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 1), ("TOPPADDING", (0, 0), (-1, -1), 1)]))
    s += [t, Spacer(1, 12), Paragraph(_esc(dem["liability_argument"]), body), Spacer(1, 8)]
    fault = dec["insured_fault_pct"]
    if fault:
        s.append(Paragraph(f"Our insured bears {fault:.0f}% responsibility. Under {_esc(rules.STATES[ex['loss_state']].name)}'s comparative "
                           f"negligence rule we demand {m['recoverable_share_pct']}% of our damages.", body))
    else:
        s.append(Paragraph("We hold your insured 100% responsible for this loss.", body))
    s += [Spacer(1, 10), Paragraph("We have paid the following on behalf of our insured:", body), Spacer(1, 4)]
    rows = [[LABELS.get(i["category"], "Other") + (f": {i['description']}" if i["category"] == "other" else ""), f"${i['amount']:,.2f}"] for i in ex["line_items"]]
    if m["deductible"]:
        rows.append(["Insured's deductible", f"${m['deductible']:,.2f}"])
    rows.append(["Total damages", f"${m['total_damages']:,.2f}"])
    if m["recoverable_share_pct"] < 100:
        rows.append([f"Demand at {m['recoverable_share_pct']}%", f"${m['demand']:,.2f}"])
    lt = Table(rows, colWidths=[4.6 * inch, 1.6 * inch])
    lt.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 10), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                            ("LINEABOVE", (0, -1 if m["recoverable_share_pct"] == 100 else -2), (-1, -1 if m["recoverable_share_pct"] == 100 else -2), 0.75, colors.black),
                            ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 10)]))
    s += [lt, Spacer(1, 10),
          Paragraph(f"We demand payment of <b>${m['demand']:,.2f}</b>, which includes our insured's deductible. Please accept liability "
                    f"and pay, or state your position in writing, by {due.strftime('%B %d, %Y')} (30 days). If we do not hear from you, "
                    f"we will file with Arbitration Forums under the automobile subrogation agreement.", body), Spacer(1, 10),
          Paragraph("<b>Enclosures</b>", body)]
    for e in dem["exhibits"]:
        s.append(Paragraph(f"Exhibit: {_esc(e['label'])} (file pages {', '.join(map(str, e['pages']))})", body))
    for d in dem.get("documents_to_obtain") or []:
        s.append(Paragraph(f"To follow: {_esc(d)}", body))
    s += [Spacer(1, 18), Paragraph("Recovery Desk agent, on behalf of Northgate Mutual", body), Spacer(1, 18),
          Paragraph("Approved by: ______________________________ (subrogation specialist)", body), Spacer(1, 14),
          Paragraph("Draft prepared by an AI agent. Amounts are taken from the payment ledger; not sent until approved.", small)]
    doc.build(s)
    return buf.getvalue()


def bundle_pdf(upload: bytes, ex: dict, dem: dict) -> bytes:
    body, small, head = _styles()
    cover = io.BytesIO()
    doc = SimpleDocTemplate(cover, pagesize=LETTER, title=f"Evidence bundle {ex['claim_number']}")
    s = [Paragraph(f"Evidence bundle, claim {_esc(ex['claim_number'])}", head), Spacer(1, 12)]
    start = 2
    rows = [["Exhibit", "File pages", "Bundle pages"]]
    for i, e in enumerate(dem["exhibits"], start=1):
        n = len(e["pages"])
        rows.append([f"{i}. {e['label']}", ", ".join(map(str, e["pages"])), f"{start}-{start + n - 1}" if n > 1 else str(start)])
        start += n
    t = Table(rows, colWidths=[3.4 * inch, 1.4 * inch, 1.4 * inch])
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 10), ("FONT", (0, 1), (-1, -1), "Helvetica", 10),
                           ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.black)]))
    s.append(t)
    doc.build(s)
    w = PdfWriter()
    for p in PdfReader(io.BytesIO(cover.getvalue())).pages:
        w.add_page(p)
    src = PdfReader(io.BytesIO(upload))
    for e in dem["exhibits"]:
        for pg in e["pages"]:
            if 1 <= pg <= len(src.pages):
                w.add_page(src.pages[pg - 1])
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def hub_payload(ex: dict, dec: dict, dem: dict, det: dict) -> dict:
    """Demand record shaped after E-Subro Hub's demand fields. Field names are ours: the real schema
    comes with Arbitration Forums membership or the claims-system integration."""
    m = dec["money"]
    car = dec.get("carrier") or {}
    return {
        "demanding_company": rules.OUR_COMPANY,
        "responding_company": {"name": car.get("name") or det.get("liable_party_carrier"), "code": car.get("code")},
        "demander_claim_number": ex["claim_number"],
        "responder_claim_number": ex.get("other_party_claim_number"),
        "demander_insured": ex.get("insured_name"),
        "responder_insured": det.get("liable_party") or ex.get("other_party_name"),
        "date_of_loss": ex["loss_date"], "loss_state": ex["loss_state"],
        "liability_requested_pct": round(100 - dec["insured_fault_pct"]),
        "damages": [{"type": i["category"], "description": i["description"], "amount": i["amount"]} for i in ex["line_items"]],
        "deductible": m["deductible"], "total_damages": m["total_damages"], "demand_amount": m["demand"],
        "negotiation_message": dem.get("negotiation_message"),
        "attachments": [{"label": e["label"], "file_pages": e["pages"]} for e in dem["exhibits"]],
        "arbitration_eligible": bool(car.get("af_member")) and not m["above_arbitration_limit"],
    }


def escalation_pdf(ex: dict, dec: dict, det: dict, packet: dict, today: date) -> bytes:
    """Arbitration filing (shaped like an inter-company arbitration submission) or a litigation referral memo.
    Pseudo documents for review: the real Arbitration Forums filing is made in its online system."""
    body, small, head = _styles()
    arb = packet["type"] == "arbitration"
    m = packet["damages"]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER, leftMargin=0.9 * inch, rightMargin=0.9 * inch, topMargin=0.8 * inch, bottomMargin=0.8 * inch,
                            title=("Arbitration filing " if arb else "Litigation referral ") + ex["claim_number"])
    title = "Inter-company arbitration filing: automobile subrogation" if arb else "Referral to counsel: subrogation litigation"
    s = [Paragraph(_esc(title), head), Paragraph("DRAFT prepared by the Recovery Desk agent for review. Not filed.", small), Spacer(1, 10)]
    car = (dec.get("carrier") or {})
    ref = [["Filing company", rules.OUR_COMPANY["name"]], ["Our claim no.", ex["claim_number"]],
           ["Responding company" if arb else "Defendant", (car.get("name") or det.get("liable_party_carrier") or "") if arb else packet.get("defendant", "")],
           ["Their claim no.", ex.get("other_party_claim_number") or "unknown"], ["Our insured", ex.get("insured_name") or ""],
           ["Their insured", det.get("liable_party") or ex.get("other_party_name") or ""], ["Date of loss", ex["loss_date"]],
           ["Loss state", ex["loss_state"]], ["Limitation deadline", f"{packet['deadline']['expires']} ({packet['deadline']['days_left']} days left)"]]
    if arb:
        ref.append(["Liability requested", f"{packet['liability_requested_pct']:.0f}%"])
    t = Table([[Paragraph(f"<b>{_esc(a)}</b>", body), Paragraph(_esc(b), body)] for a, b in ref], colWidths=[1.7 * inch, 4.9 * inch])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd"))]))
    s += [t, Spacer(1, 12)]
    if not arb:
        s += [Paragraph("<b>Why litigation</b>", body), Paragraph(_esc(packet.get("why_litigation")), body), Spacer(1, 8)]
    s += [Paragraph("<b>" + ("Contentions" if arb else "Summary") + "</b>", body),
          Paragraph(_esc(packet.get("contentions") if arb else packet.get("summary")), body), Spacer(1, 10),
          Paragraph("<b>Damages (from the payment ledger)</b>", body)]
    rows = [[LABELS.get(i["category"], "Other"), f"${i['amount']:,.2f}"] for i in ex["line_items"]]
    if m["deductible"]:
        rows.append(["Insured's deductible", f"${m['deductible']:,.2f}"])
    rows += [["Total damages", f"${m['total_damages']:,.2f}"], [f"Amount sought ({m['recoverable_share_pct']}% recoverable)", f"${packet['demand']:,.2f}"]]
    if packet.get("last_offer"):
        rows.append(["Other side's last offer", f"${packet['last_offer']:,.2f}"])
    dt = Table(rows, colWidths=[4.6 * inch, 1.6 * inch])
    dt.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 10), ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("LINEABOVE", (0, -2 if not packet.get("last_offer") else -3), (-1, -2 if not packet.get("last_offer") else -3), 0.75, colors.black)]))
    s += [dt, Spacer(1, 10), Paragraph("<b>Evidence index</b>", body)]
    er = [["Exhibit", "File pages", "What it proves"]] + [[Paragraph(_esc(e["label"]), body), ", ".join(map(str, e["pages"])), Paragraph(_esc(e.get("proves")), body)] for e in packet["evidence_index"]]
    et = Table(er, colWidths=[1.9 * inch, 0.9 * inch, 3.8 * inch])
    et.setStyle(TableStyle([("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9.5), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.black)]))
    s += [et, Spacer(1, 10)]
    if arb and packet.get("anticipated_defenses"):
        s.append(Paragraph("<b>Anticipated defenses and our response</b>", body))
        for d in packet["anticipated_defenses"]:
            s.append(Paragraph(f"<b>{_esc(d['defense'])}</b> {_esc(d['response'])}", body))
    if not arb and packet.get("risks"):
        s.append(Paragraph("<b>Risks for counsel</b>", body))
        for r in packet["risks"]:
            s.append(Paragraph(f"- {_esc(r)}", body))
    s += [Spacer(1, 12), Paragraph(f"Escalation reason: {_esc(packet.get('reason'))}", small),
          Paragraph(f"Prepared {today.isoformat()}. Amounts are from the payment ledger; text drafted by an AI agent for human review.", small)]
    doc.build(s)
    return buf.getvalue()
