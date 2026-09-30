"""Generates synthetic closed auto claim files as multi-document PDFs for demos and tests.

Each file mimics a claims-system export: FNOL report, adjuster notes, police report, estimate,
invoices and a payment ledger, one document per page. All people and carriers are fictional.
Run: python samples/make_samples.py
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

OUT = Path(__file__).resolve().parent
ss = getSampleStyleSheet()
H = ParagraphStyle("h", parent=ss["Normal"], fontName="Helvetica-Bold", fontSize=13, leading=17, spaceAfter=6)
B = ParagraphStyle("b", parent=ss["Normal"], fontName="Helvetica", fontSize=10, leading=14)
M = ParagraphStyle("m", parent=ss["Normal"], fontName="Courier", fontSize=9, leading=12.5)
S = ParagraphStyle("s", parent=B, fontSize=8, textColor=colors.HexColor("#666666"))


def kv(rows):
    t = Table([[Paragraph(f"<b>{k}</b>", B), Paragraph(str(v), B)] for k, v in rows], colWidths=[2.0 * inch, 4.6 * inch])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd"))]))
    return t


def grid(rows, widths):
    t = Table(rows, colWidths=[w * inch for w in widths])
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9.5), ("FONT", (0, 1), (-1, -1), "Helvetica", 9.5),
                           ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.black), ("ALIGN", (-1, 0), (-1, -1), "RIGHT")]))
    return t


def header(title, claim):
    return [Paragraph("NORTHGATE MUTUAL INSURANCE (fictional) · Claims file export", S), Spacer(1, 4),
            Paragraph(title, H), Paragraph(f"Claim {claim}", S), Spacer(1, 8)]


def notes(lines):
    return [Paragraph(l.replace("&", "&amp;").replace("<", "&lt;"), M) for l in lines]


def build(name, claim, pages):
    doc = SimpleDocTemplate(str(OUT / f"{name}.pdf"), pagesize=LETTER, leftMargin=0.8 * inch, rightMargin=0.8 * inch,
                            topMargin=0.7 * inch, bottomMargin=0.7 * inch, title=f"Claim file {claim}")
    story = []
    for i, (title, flow) in enumerate(pages):
        if i:
            story.append(PageBreak())
        story += header(title, claim) + flow
    doc.build(story)


def ledger(rows, ded, total):
    data = [["Date", "Payee", "Category", "Amount"]] + rows + [["", "", "Total paid by Northgate", total]]
    return [grid(data, [1.0, 2.6, 1.8, 1.2]), Spacer(1, 8), Paragraph(f"Insured deductible applied: {ded}. Deductible not included in total paid.", B)]


# ---------------------------------------------------------------- 1. clear rear-end, California -> pursue
build("rear-end-california", "NG-25-10412", [
    ("First Notice of Loss", [kv([
        ("Claim number", "NG-25-10412"), ("Policy", "NGM-CA-4471902"), ("Insured", "Priya Raman"),
        ("Insured vehicle", "2021 Honda Civic, CA plate 8KXR221"), ("Date of loss", "11/03/2025, approx. 5:40 pm"),
        ("Location", "Lincoln Blvd at Venice Blvd, Los Angeles, CA"), ("Coverage", "Collision, $500 deductible"),
        ("Reported by", "Insured, via mobile app, 11/03/2025"),
        ("Description", "Insured was stopped at a red light when the vehicle behind failed to stop and struck the rear of the insured vehicle."),
        ("Other party", "Marcus Webb, 2019 Toyota Camry"), ("Other party insurer", "Harborline Insurance, claim HL-7730192")])]),
    ("Adjuster Notes", notes([
        "11/03/25 FNOL rec'd via app. IV stopped at red, Lincoln/Venice. Rear-ended by OV (2019 Camry, Marcus Webb).",
        "11/04/25 Called OV driver. OV admits he looked down at his phone and did not see traffic stop.",
        "11/04/25 PR requested. LAPD report #25-110331 obtained. OV driver cited for VC 21703 (following too closely).",
        "11/05/25 IV towed to DRP (Westside Collision). Tow $185.",
        "11/07/25 DRP estimate $6,840 reviewed and approved. Rental authorized.",
        "11/21/25 Repairs complete. Final invoice $6,840. Rental 16 days @ $70 = $1,120.",
        "11/22/25 Liab: IV 0% / OV 100% per PR and OV statement. Paid in full less $500 ded.",
        "11/22/25 File closed. Subro potential noted, not referred.",
    ])),
    ("Police Report (summary page)", [kv([
        ("Agency", "Los Angeles Police Department"), ("Report number", "25-110331"), ("Date / time", "11/03/2025 17:42"),
        ("Vehicle 1", "2021 Honda Civic, driver Priya Raman, stopped for red signal"),
        ("Vehicle 2", "2019 Toyota Camry, driver Marcus Webb"),
        ("Officer narrative", "V1 was stopped for a red signal. V2 failed to stop and struck V1 from behind. D2 stated he was distracted. "
                              "D2 cited for violation of CVC 21703, following too closely."),
        ("Citations", "Driver 2: CVC 21703")])]),
    ("Repair Estimate and Final Invoice", [kv([("Shop", "Westside Collision (direct repair program)"), ("Vehicle", "2021 Honda Civic"),
        ("Repair dates", "11/06/2025 to 11/21/2025 (15 days)")]), Spacer(1, 8),
        grid([["Operation", "Amount"], ["Rear bumper cover, replace and refinish", "$1,420.00"], ["Rear body panel, repair", "$1,180.00"],
              ["Trunk lid, replace and refinish", "$1,960.00"], ["Rear sensors, recalibration (ADAS)", "$640.00"],
              ["Parts and materials, other", "$1,040.00"], ["Labor, remaining", "$600.00"], ["Final invoice total", "$6,840.00"]], [5.0, 1.6])]),
    ("Rental and Towing Invoices", [kv([("Rental company", "Pacific Car Rental"), ("Rental period", "11/05/2025 to 11/21/2025, 16 days"),
        ("Daily rate", "$70.00"), ("Rental total", "$1,120.00")]), Spacer(1, 10),
        kv([("Tow company", "Metro Towing"), ("Date", "11/05/2025"), ("Tow total", "$185.00")])]),
    ("Payment Ledger", ledger([["11/05/2025", "Metro Towing", "Towing", "$185.00"], ["11/22/2025", "Westside Collision", "Repair", "$6,840.00"],
                               ["11/22/2025", "Pacific Car Rental", "Rental", "$1,120.00"]], "$500.00", "$8,145.00")),
])

# ---------------------------------------------------------------- 2. Maryland, insured partly at fault -> park
build("intersection-maryland", "NG-25-11020", [
    ("First Notice of Loss", [kv([
        ("Claim number", "NG-25-11020"), ("Insured", "Aisha Bello"), ("Insured vehicle", "2020 Subaru Outback"),
        ("Date of loss", "10/18/2025"), ("Location", "Route 40 at Rolling Rd, Catonsville, MD"), ("Coverage", "Collision, $500 deductible"),
        ("Description", "Collision in the intersection as the other vehicle turned left across the insured's path."),
        ("Other party", "Greg Tolliver, 2017 Ford F-150"), ("Other party insurer", "Pinecrest General, claim PG-208841")])]),
    ("Adjuster Notes", notes([
        "10/18/25 Intersection loss, Rt 40 / Rolling Rd. OV failed to yield on left turn.",
        "10/22/25 EDR download from IV shows IV at 52 mph in a 40 zone, 12 over limit at impact.",
        "10/23/25 Liab set: IV 20% / OV 80% given IV speed.",
        "10/30/25 Repairs paid $8,120. Rental 8 days $640. Closed.",
    ])),
    ("Police Report (summary page)", [kv([("Agency", "Baltimore County Police"), ("Report number", "25-40771"),
        ("Narrative", "V2 turning left failed to yield to V1. No citations issued."), ("Citations", "None")])]),
    ("Payment Ledger", ledger([["10/30/2025", "Catonsville Auto Body", "Repair", "$8,120.00"], ["10/30/2025", "Enterprise", "Rental", "$640.00"]],
                              "$500.00", "$8,760.00")),
])

# ---------------------------------------------------------------- 3. Texas, time-barred -> park
build("red-light-texas-2023", "NG-23-05530", [
    ("First Notice of Loss", [kv([
        ("Claim number", "NG-23-05530"), ("Insured", "Chloe Nguyen"), ("Date of loss", "06/02/2023"),
        ("Location", "Lamar Blvd at 5th St, Austin, TX"), ("Coverage", "Collision, $500 deductible"),
        ("Description", "Other driver ran a red light and struck the insured broadside."),
        ("Other party", "Ray Dominguez"), ("Other party insurer", "Summit Road Insurance, claim SRI-44120")])]),
    ("Adjuster Notes", notes([
        "06/02/23 OV ran red at Lamar/5th, T-boned IV. PR cites OV. 1 independent witness statement.",
        "06/05/23 Est $11,250 approved. Rental 12 days $780. Tow $160.",
        "06/30/23 Paid. Liab 100% OV. Closed. No subro referral on file.",
    ])),
    ("Police Report (summary page)", [kv([("Agency", "Austin Police Department"), ("Report number", "23-20114"),
        ("Narrative", "Witness states V2 entered on red. D2 cited for disregarding traffic signal."), ("Citations", "Driver 2: disregard red signal")])]),
    ("Payment Ledger", ledger([["06/05/2023", "Capital Tow", "Towing", "$160.00"], ["06/30/2023", "Lamar Collision", "Repair", "$11,250.00"],
                               ["06/30/2023", "Hertz", "Rental", "$780.00"]], "$500.00", "$12,190.00")),
])

# ---------------------------------------------------------------- 4. Florida lane change, conflicting -> route
build("lane-change-florida", "NG-25-11304", [
    ("First Notice of Loss", [kv([
        ("Claim number", "NG-25-11304"), ("Insured", "Jordan Blake"), ("Date of loss", "09/07/2025"),
        ("Location", "US-1 northbound, Miami, FL"), ("Coverage", "Collision, $500 deductible"),
        ("Description", "Vehicles made contact while changing lanes."),
        ("Other party", "Olivia Park"), ("Other party insurer", "Bluewater Mutual, claim BWM-903311")])]),
    ("Adjuster Notes", notes([
        "09/07/25 Lane change contact on US-1. Both drivers claim the other merged into their lane.",
        "09/08/25 No police report filed. No witnesses located. No video.",
        "09/10/25 Photos show damage to IV right front and OV left rear quarter.",
        "09/12/25 Liab unclear. Adjuster call IV 0% / OV 100% based on damage pattern, disputed by OV.",
        "09/25/25 Paid $4,380 repair, rental 6 days $420. Closed.",
    ])),
    ("Payment Ledger", ledger([["09/25/2025", "Biscayne Body Works", "Repair", "$4,380.00"], ["09/25/2025", "Avis", "Rental", "$420.00"]],
                              "$500.00", "$4,800.00")),
])

# ---------------------------------------------------------------- 5. Hail, New York -> park (no third party)
build("hail-new-york", "NG-26-00590", [
    ("First Notice of Loss", [kv([
        ("Claim number", "NG-26-00590"), ("Insured", "Hannah Lee"), ("Date of loss", "05/11/2026"),
        ("Location", "Rochester, NY"), ("Coverage", "Comprehensive, $250 deductible"),
        ("Description", "Hailstorm damaged roof and hood while vehicle was parked at the insured's home."),
        ("Other party", "None")])]),
    ("Adjuster Notes", notes(["05/11/26 Hailstorm, Rochester. Roof + hood dents. No other party.", "05/15/26 PDR estimate $5,210. Paid. Closed."])),
    ("Payment Ledger", ledger([["05/15/2026", "Dent Pros Rochester", "Repair", "$5,210.00"]], "$250.00", "$5,210.00")),
])

# ---------------------------------------------------------------- 6. Truck total loss over $100k, Texas -> route
build("truck-total-loss-texas", "NG-26-00144", [
    ("First Notice of Loss", [kv([
        ("Claim number", "NG-26-00144"), ("Insured", "Rebecca Stein"), ("Insured vehicle", "2025 Range Rover"),
        ("Date of loss", "02/03/2026"), ("Location", "I-35 southbound, San Marcos, TX"), ("Coverage", "Collision, $1,000 deductible"),
        ("Description", "Tractor-trailer changed lanes into the insured vehicle."),
        ("Other party", "Lone Star Freight LLC (tractor-trailer)"), ("Other party insurer", "Summit Road Insurance, claim SRI-60114")])]),
    ("Adjuster Notes", notes([
        "02/03/26 Tractor-trailer merged into IV on I-35. PR cites truck driver for unsafe lane change. Truck dashcam obtained.",
        "02/10/26 IV total loss. ACV $104,000. Storage 30 days $1,200. Tow $650. Rental 30 days $2,100.",
        "03/10/26 Settled with insured. Salvage sold. Liab 100% OV. Closed.",
    ])),
    ("Police Report (summary page)", [kv([("Agency", "Texas DPS"), ("Report number", "26-3301"),
        ("Narrative", "Commercial vehicle changed lanes without clearance and struck V1. Driver of commercial vehicle cited, unsafe lane change.")])]),
    ("Payment Ledger", ledger([["02/04/2026", "I-35 Towing", "Towing", "$650.00"], ["03/10/2026", "Rebecca Stein / lienholder", "Total loss ACV", "$104,000.00"],
                               ["03/10/2026", "Copart", "Storage", "$1,200.00"], ["03/10/2026", "Enterprise", "Rental", "$2,100.00"]],
                              "$1,000.00", "$107,950.00")),
])

# ---------------------------------------------------------------- 7. Parked car, Georgia, small -> carrier short-pays
build("parked-car-georgia", "NG-25-10877", [
    ("First Notice of Loss", [kv([
        ("Claim number", "NG-25-10877"), ("Insured", "Daniel Ortiz"), ("Insured vehicle", "2019 Mazda CX-5"),
        ("Date of loss", "12/12/2025"), ("Location", "Kroger parking lot, Ponce de Leon Ave, Atlanta, GA"), ("Coverage", "Collision, $500 deductible"),
        ("Description", "Insured's vehicle was parked and unoccupied when another driver backed into it."),
        ("Other party", "Kelsey Tran, 2016 Nissan Altima"), ("Other party insurer", "Keystone Auto Casualty, claim KAC-551207")])]),
    ("Adjuster Notes", notes([
        "12/12/25 IV parked, unoccupied, Kroger lot. OV driver left a note on the windshield with name and insurance and admitted backing into IV.",
        "12/13/25 Store security video confirms OV reversing into IV.",
        "12/15/25 Photo estimate $1,260 approved. Repairs 3 days.",
        "12/19/25 Paid less $500 ded. Liab 100% OV. Closed. Small amount, not referred.",
    ])),
    ("Payment Ledger", ledger([["12/19/2025", "Peachtree Auto Body", "Repair", "$1,260.00"]], "$500.00", "$1,260.00")),
])

# ---------------------------------------------------------------- 8. Shared fault, New York, rental dispute
build("rental-dispute-new-york", "NG-26-00210", [
    ("First Notice of Loss", [kv([
        ("Claim number", "NG-26-00210"), ("Insured", "Grace Kim"), ("Date of loss", "03/18/2026"),
        ("Location", "Cross Bronx Expressway, Bronx, NY"), ("Coverage", "Collision, $500 deductible"),
        ("Description", "Both vehicles moved into the center lane; the other vehicle struck the insured's left side."),
        ("Other party", "Luis Romero"), ("Other party insurer", "Harborline Insurance, claim HL-7801144")])]),
    ("Adjuster Notes", notes([
        "03/18/26 Both vehicles moved into center lane. Independent witness says OV signalled late and moved first.",
        "03/20/26 Liab set: IV 25% / OV 75% (IV also changed lanes).",
        "03/22/26 Est $5,900. Parts on backorder (door shell), shop waited 9 days for parts.",
        "04/09/26 Repairs complete after 18 days in shop including parts delay. Rental 18 days @ $70 = $1,260.",
        "04/10/26 Paid. Closed.",
    ])),
    ("Police Report (summary page)", [kv([("Agency", "NYPD"), ("Report number", "26-8830"),
        ("Narrative", "Both vehicles changing into center lane. Witness states V2 signalled late. No citations issued.")])]),
    ("Repair Invoice", [kv([("Shop", "Bronx Collision Center"), ("In shop", "03/22/2026 to 04/09/2026 (18 days)"),
        ("Note", "Door shell backordered 9 days; parts delay documented on invoice."), ("Invoice total", "$5,900.00")])]),
    ("Payment Ledger", ledger([["04/10/2026", "Bronx Collision Center", "Repair", "$5,900.00"], ["04/10/2026", "Hertz", "Rental", "$1,260.00"]],
                              "$500.00", "$7,160.00")),
])

# ---------------------------------------------------------------- 9. Red light, total loss, California -> counter then settle
build("red-light-total-loss-california", "NG-25-11622", [
    ("First Notice of Loss", [kv([
        ("Claim number", "NG-25-11622"), ("Insured", "Samuel Okafor"), ("Insured vehicle", "2022 Toyota RAV4"),
        ("Date of loss", "12/29/2025"), ("Location", "Mission St at 16th St, San Francisco, CA"), ("Coverage", "Collision, $500 deductible"),
        ("Description", "Other driver entered the intersection against a red light and struck the insured broadside. Total loss."),
        ("Other party", "Evan Marsh"), ("Other party insurer", "Bluewater Mutual, claim BWM-911870")])]),
    ("Adjuster Notes", notes([
        "12/29/25 T-bone at Mission/16th. IV dashcam video shows OV entering on red. PR cites OV. One independent witness.",
        "01/05/26 IV declared total loss. ACV $38,500. Storage 14 days @ $40 = $560. Tow $240. Rental 20 days @ $70 = $1,400.",
        "01/20/26 Settled with insured. Salvage sold. Liab 100% OV. Closed.",
    ])),
    ("Police Report (summary page)", [kv([("Agency", "San Francisco Police Department"), ("Report number", "25-771205"),
        ("Narrative", "V2 entered the intersection against a steady red signal and struck V1. Independent witness confirms. D2 cited, CVC 21453(a).")])]),
    ("Payment Ledger", ledger([["12/30/2025", "City Tow", "Towing", "$240.00"], ["01/20/2026", "Samuel Okafor / lienholder", "Total loss ACV", "$38,500.00"],
                               ["01/20/2026", "Copart", "Storage", "$560.00"], ["01/20/2026", "Enterprise", "Rental", "$1,400.00"]],
                              "$500.00", "$40,700.00")),
])

# ---------------------------------------------------------------- 10. Rear-end, Florida, carrier never answers
build("rear-end-florida-no-reply", "NG-25-11966", [
    ("First Notice of Loss", [kv([
        ("Claim number", "NG-25-11966"), ("Insured", "Ethan Brooks"), ("Date of loss", "10/05/2025"),
        ("Location", "I-4 eastbound, Orlando, FL"), ("Coverage", "Collision, $500 deductible"),
        ("Description", "Insured was stopped in traffic and was rear-ended."),
        ("Other party", "Sofia Mendes"), ("Other party insurer", "Summit Road Insurance, claim SRI-58812")])]),
    ("Adjuster Notes", notes([
        "10/05/25 IV stopped in traffic on I-4, rear-ended by OV. PR cites OV for careless driving.",
        "10/08/25 Est $7,650 approved. Rental 14 days $980. Tow $150.",
        "10/28/25 Paid. Liab 100% OV. Closed.",
    ])),
    ("Police Report (summary page)", [kv([("Agency", "Florida Highway Patrol"), ("Report number", "25-19920"),
        ("Narrative", "V2 failed to stop and struck V1, which was stopped in traffic. D2 cited for careless driving.")])]),
    ("Payment Ledger", ledger([["10/08/2025", "Orange Tow", "Towing", "$150.00"], ["10/28/2025", "Orlando Collision", "Repair", "$7,650.00"],
                               ["10/28/2025", "Budget", "Rental", "$980.00"]], "$500.00", "$8,780.00")),
])

# ---------------------------------------------------------------- 11. Uninsured driver, Georgia -> litigation
build("uninsured-driver-georgia", "NG-25-11801", [
    ("First Notice of Loss", [kv([
        ("Claim number", "NG-25-11801"), ("Insured", "Kevin Adams"), ("Date of loss", "07/14/2025"),
        ("Location", "Peachtree St, Atlanta, GA"), ("Coverage", "Collision, $500 deductible"),
        ("Description", "Insured was rear-ended at a stop light by an uninsured driver."),
        ("Other party", "Travis Kemp, 2011 Chevrolet Malibu"), ("Other party insurer", "None. Driver uninsured per police report.")])]),
    ("Adjuster Notes", notes([
        "07/14/25 OV rear-ended IV at light. PR: OV had no insurance, cited for no proof of insurance and following too closely.",
        "07/18/25 Est $9,800 approved. Rental 12 days $840.",
        "08/06/25 Paid. Liab 100% OV. OV owns home at listed address per public records. Closed.",
    ])),
    ("Police Report (summary page)", [kv([("Agency", "Atlanta Police Department"), ("Report number", "25-6620"),
        ("Narrative", "V2 struck V1 from behind at a red light. D2 unable to show proof of insurance. D2 cited for following too closely and no insurance.")])]),
    ("Payment Ledger", ledger([["08/06/2025", "Midtown Body Shop", "Repair", "$9,800.00"], ["08/06/2025", "Enterprise", "Rental", "$840.00"]],
                              "$500.00", "$10,640.00")),
])

if __name__ == "__main__":
    print("Samples written to", OUT)
