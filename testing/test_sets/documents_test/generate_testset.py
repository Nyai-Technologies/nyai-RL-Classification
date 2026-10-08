import csv, json, os, random, subprocess, shutil, glob
from xml.sax.saxutils import escape
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle

random.seed(42)
OUT = "/home/claude/rl_testset"
FILES = f"{OUT}/files"
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(FILES)

ss = getSampleStyleSheet()
H = ParagraphStyle("h", parent=ss["Title"], fontSize=16, spaceAfter=10)
H2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=11.5, spaceBefore=8, spaceAfter=4)
P = ParagraphStyle("p", parent=ss["Normal"], fontSize=10, leading=14, alignment=TA_JUSTIFY, spaceAfter=6)
C = ParagraphStyle("c", parent=P, alignment=TA_CENTER)
SM = ParagraphStyle("sm", parent=P, fontSize=8.5, leading=11)

COS = ["Sahyadri Infotech", "Kaveri Analytics", "Deccan Cloudworks", "Narmada Fintech Solutions", "Indus Legal Systems",
       "Godavari Logistics", "Vindhya Data Labs", "Malabar Health Tech", "Aravalli Consulting", "Konkan Retail Ventures",
       "Shivneri Engineering", "Tapti Manufacturing", "Brahmaputra Energy", "Nilgiri Software", "Chambal Agritech"]
CITIES = [("Pune", "Maharashtra"), ("Mumbai", "Maharashtra"), ("Bengaluru", "Karnataka"), ("Hyderabad", "Telangana"),
          ("Gurugram", "Haryana"), ("Chennai", "Tamil Nadu"), ("Gwalior", "Madhya Pradesh"), ("Ahmedabad", "Gujarat")]
PEOPLE = ["Rohan Kulkarni", "Priya Deshpande", "Anil Mehta", "Sneha Iyer", "Vikram Rathore", "Neha Joshi", "Arjun Nair",
          "Kavita Sharma", "Siddharth Rao", "Meera Pillai", "Rahul Verma", "Pooja Bhatt", "Karan Malhotra", "Ishita Sen"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def p(t, s=P): return Paragraph(escape(t), s)
def date(): return f"{random.randint(1, 28)} {random.choice(MONTHS)} {random.choice([2024, 2025, 2026])}"
FORCE = {}
def co():
    if FORCE.get("cos"): return FORCE["cos"].pop(0)
    return random.choice(COS)
def two_cos():
    if FORCE.get("cos") and len(FORCE["cos"]) >= 2: return FORCE["cos"].pop(0), FORCE["cos"].pop(0)
    a, b = random.sample(COS, 2); return a, b
def addr():
    c, s = random.choice(CITIES); return f"{random.randint(11, 480)}, {random.choice(['MG Road', 'Baner Road', 'Hinjewadi Phase 2', 'Andheri East', 'Whitefield', 'Banjara Hills', 'Sector 44', 'Anna Salai'])}, {c}, {s}"
def inr(lo, hi): return f"INR {random.randint(lo, hi):,}"
def cin(): return f"U{random.randint(10000, 99999)}MH{random.randint(2010, 2024)}PTC{random.randint(100000, 999999)}"


def tbl(rows, widths=None):
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
                           ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return t


def sign_block(a, b):
    return [Spacer(1, 10), p("IN WITNESS WHEREOF, the parties have executed this document through their authorised representatives."),
            tbl([["For " + a, "For " + b], ["Name: " + random.choice(PEOPLE), "Name: " + random.choice(PEOPLE)],
                 ["Designation: Director", "Designation: Authorised Signatory"], ["Date: " + date(), "Date: " + date()]], [85 * mm, 85 * mm])]


# ---------------------------------------------------------------- MSA
def msa(cover=False):
    a, b = two_cos(); d = date(); city = random.choice(CITIES)[0]
    s = []
    if cover:
        s += [Spacer(1, 120), p(f"{a} Private Limited", C), Spacer(1, 10), p("CONFIDENTIAL", C), p(f"Prepared for: {b} Private Limited", C),
              p(f"Document Version {random.randint(2, 5)}.{random.randint(0, 9)}  |  Legal Review Copy", C), PageBreak(),
              Paragraph("TABLE OF CONTENTS", H2)]
        for i, t in enumerate(["Definitions", "Scope", "Term and Termination", "Fees and Payment", "Confidentiality",
                               "Intellectual Property", "Warranties", "Indemnity", "Limitation of Liability", "Data Protection",
                               "Governing Law and Dispute Resolution", "General Provisions", "Annexure A - Template SOW"], 1):
            s.append(p(f"{i}. {t} ............................................. {i + 2}"))
        s.append(PageBreak())
    s += [Paragraph("MASTER SERVICES AGREEMENT", H),
          p(f"This Master Services Agreement (the \"Agreement\") is entered into on {d} at {city} by and between {a} Private Limited, a company incorporated under the Companies Act, 2013, having its registered office at {addr()} (the \"Client\"), and {b} Private Limited, having its registered office at {addr()} (the \"Service Provider\")."),
          p("WHEREAS the Client wishes to engage the Service Provider from time to time to provide certain services, and the parties wish to set out the general terms and conditions that will govern all such engagements. The specific services, deliverables, timelines and fees for each engagement shall be set out in one or more Statements of Work executed under this Agreement."),
          Paragraph("1. Definitions", H2),
          p("\"Statement of Work\" or \"SOW\" means a document executed by both parties that references this Agreement and describes specific services. \"Confidential Information\" means all non-public information disclosed by either party. \"Deliverables\" means the work product specified in an SOW."),
          Paragraph("2. Scope and Order of Precedence", H2),
          p("This Agreement does not by itself oblige the Client to purchase any services. Each SOW shall be governed by this Agreement. In case of conflict, the terms of this Agreement shall prevail over any SOW unless the SOW expressly states otherwise with reference to the specific clause being varied."),
          Paragraph("3. Term and Termination", H2),
          p(f"This Agreement shall remain in force for a period of {random.choice([3, 5])} years from the Effective Date and shall automatically renew for successive one-year terms unless either party gives {random.choice([60, 90])} days' written notice. Either party may terminate this Agreement for material breach that remains uncured for thirty (30) days after written notice. Termination of this Agreement shall not affect any SOW then in progress unless the parties agree otherwise."),
          Paragraph("4. Fees, Invoicing and Payment", H2),
          p(f"Fees shall be as specified in each SOW. The Service Provider shall invoice monthly in arrears and the Client shall pay undisputed amounts within {random.choice([30, 45, 60])} days of receipt of a valid GST-compliant invoice. Applicable taxes shall be borne as per law. Disputed amounts shall be notified within fifteen days."),
          Paragraph("5. Confidentiality", H2),
          p("Each party shall protect the other party's Confidential Information using at least the same degree of care it uses for its own information, and in no event less than reasonable care. These obligations survive termination for five (5) years."),
          Paragraph("6. Intellectual Property", H2),
          p("All pre-existing intellectual property remains with its owner. Upon full payment, Deliverables created specifically for the Client under an SOW shall vest in the Client, subject to the Service Provider's retained rights in its tools, frameworks and know-how."),
          Paragraph("7. Warranties", H2),
          p("The Service Provider warrants that the services shall be performed in a professional and workmanlike manner by qualified personnel and in accordance with applicable law."),
          Paragraph("8. Indemnification", H2),
          p("Each party shall indemnify the other against third-party claims arising from its gross negligence, wilful misconduct, or infringement of third-party intellectual property rights."),
          Paragraph("9. Limitation of Liability", H2),
          p(f"Except for breach of confidentiality and indemnity obligations, neither party's aggregate liability shall exceed the fees paid under the relevant SOW in the {random.choice([6, 12])} months preceding the claim. Neither party shall be liable for indirect or consequential losses."),
          Paragraph("10. Data Protection", H2),
          p("Where the Service Provider processes personal data on behalf of the Client, it shall act as a Data Processor and comply with the Digital Personal Data Protection Act, 2023 and the rules thereunder, and shall implement reasonable security safeguards."),
          Paragraph("11. Governing Law and Dispute Resolution", H2),
          p(f"This Agreement shall be governed by the laws of India. Disputes shall be referred to arbitration by a sole arbitrator under the Arbitration and Conciliation Act, 1996, seated at {city}. Courts at {city} shall have exclusive jurisdiction."),
          Paragraph("12. General", H2),
          p("This Agreement, together with all SOWs, constitutes the entire agreement between the parties. Amendments must be in writing. Neither party may assign this Agreement without prior written consent, except to an affiliate.")]
    return s + sign_block(a, b)


# ---------------------------------------------------------------- SOW
def sow():
    a, b = two_cos(); n = FORCE.get("sow_no") or random.randint(1, 40); proj = random.choice(["Data Platform Migration", "Compliance Dashboard Build",
        "Mobile App Revamp", "Document AI Pipeline", "ERP Integration", "Customer Support Chatbot", "Cloud Cost Optimisation"])
    weeks = random.randint(8, 30); team = []
    total = 0
    for r in random.sample(["Project Manager", "Solution Architect", "Senior Engineer", "ML Engineer", "QA Analyst", "Business Analyst", "DevOps Engineer", "UI/UX Designer"], 5):
        c = random.randint(1, 6); team.append([r, str(c), random.choice(["Onsite", "Offshore", "Hybrid"])])
    ms = [["#", "Milestone", "Due (week)", "Payment %"]]
    pcts = [20, 30, 30, 20]
    for i, (m, pc) in enumerate(zip(["Requirements sign-off", "Design approval", "UAT completion", "Go-live and handover"], pcts), 1):
        ms.append([str(i), m, str(int(weeks * i / 4)), f"{pc}%"])
    value = random.randint(15, 180) * 100000
    return [Paragraph(f"STATEMENT OF WORK No. SOW-{n:03d}", H),
            p(f"Project: {proj}"),
            p(f"This Statement of Work (\"SOW\") is issued under and governed by the Master Services Agreement dated {date()} (the \"MSA\") between {a} Private Limited (\"Client\") and {b} Private Limited (\"Service Provider\"). Capitalised terms not defined here have the meaning given in the MSA."),
            Paragraph("1. Project Overview", H2),
            p(f"The Client requires the Service Provider to deliver the {proj} project over an estimated duration of {weeks} weeks commencing {date()}."),
            Paragraph("2. Scope of Services", H2),
            p("In scope: requirement workshops, solution design, development, testing, deployment support and knowledge transfer. Out of scope: hardware procurement, third-party licence costs, and production support beyond the 30-day warranty period."),
            Paragraph("3. Deliverables and Milestones", H2), tbl(ms, [12 * mm, 80 * mm, 30 * mm, 30 * mm]),
            Paragraph("4. Team Composition", H2), tbl([["Role", "Count (FTE)", "Location"]] + team, [70 * mm, 35 * mm, 45 * mm]),
            Paragraph("5. Fees and Payment Schedule", H2),
            tbl([["Item", "Amount"], ["Total SOW Value (fixed price)", f"INR {value:,}"], ["Billing", "Against milestones as per Section 3"],
                 ["Payment terms", "As per MSA"]], [80 * mm, 70 * mm]),
            Paragraph("6. Assumptions", H2),
            p("The Client will provide timely access to systems, data and stakeholders. Delays caused by dependencies outside the Service Provider's control shall extend timelines proportionately."),
            Paragraph("7. Acceptance Criteria", H2),
            p("Each deliverable is deemed accepted if the Client does not report material defects within ten (10) business days of delivery."),
            Paragraph("8. Change Management", H2),
            p("Any change to scope, timeline or fees requires a written Change Request signed by both parties.")] + sign_block(a, b)


# ---------------------------------------------------------------- MoM
def mom():
    c = co(); attendees = random.sample(PEOPLE, random.randint(4, 7)); title = random.choice(["Project Steering Committee",
        "Weekly Delivery Sync", "Quarterly Business Review", "Compliance Review Meeting", "Vendor Governance Call", "Board Committee Meeting"])
    actions = [["#", "Action Item", "Owner", "Due Date"]]
    for i, a in enumerate(random.sample(["Share revised project plan", "Close open audit observations", "Finalise vendor shortlist",
        "Circulate DPDP consent notice draft", "Fix UAT defects (P1/P2)", "Prepare budget variance note", "Schedule security review",
        "Update risk register"], 4), 1):
        actions.append([str(i), a, random.choice(attendees), date()])
    return [Paragraph("MINUTES OF MEETING", H),
            tbl([["Meeting", title], ["Organisation", c + " Private Limited"], ["Date", date()], ["Time", f"{random.randint(9, 17)}:00 - {random.randint(10, 18)}:30 IST"],
                 ["Venue", random.choice(["Conference Room 3B", "Microsoft Teams", "Google Meet", "Board Room, " + random.choice(CITIES)[0]])],
                 ["Chairperson", attendees[0]], ["Minutes recorded by", attendees[-1]]], [45 * mm, 110 * mm]),
            Paragraph("Attendees", H2), p(", ".join(attendees)),
            Paragraph("Apologies / Absent", H2), p(random.choice(PEOPLE)),
            Paragraph("Agenda", H2), p("1. Review of action items from previous meeting  2. Status update  3. Risks and issues  4. Decisions required  5. Any other business"),
            Paragraph("1. Review of Previous Action Items", H2),
            p(f"Of the {random.randint(4, 9)} open action items from the previous meeting, {random.randint(2, 4)} were closed. Remaining items were carried forward with revised dates."),
            Paragraph("2. Status Update", H2),
            p(f"{attendees[1]} reported that the current phase is {random.randint(55, 95)}% complete. The team flagged a dependency on data access from the client IT team which is impacting timelines by approximately one week."),
            Paragraph("3. Risks and Issues", H2),
            p("The committee discussed resource attrition and agreed to add one backup resource. A compliance query on consent records was raised and will be addressed by the legal team."),
            Paragraph("4. Decisions Taken", H2),
            p("It was decided that the go-live date will be retained, subject to closure of P1 defects. The revised budget proposal was approved in principle."),
            Paragraph("5. Action Items", H2), tbl(actions, [10 * mm, 80 * mm, 35 * mm, 30 * mm]),
            Paragraph("Next Meeting", H2), p(f"The next meeting is scheduled for {date()}. The meeting ended with a vote of thanks to the Chair.")]


# ---------------------------------------------------------------- AoA
def aoa(cover=False, company=None):
    c = company or co(); s = []
    if cover:
        s += [Spacer(1, 150), p("INCORPORATION DOCUMENTS", C), p(f"{c.upper()} PRIVATE LIMITED", C), p(f"CIN: {cin()}", C),
              p("Certified True Copy", C), PageBreak()]
    s += [p("THE COMPANIES ACT, 2013", C), p("COMPANY LIMITED BY SHARES", C), p("(Incorporated under the Companies Act, 2013)", C),
          Paragraph(f"ARTICLES OF ASSOCIATION OF {c.upper()} PRIVATE LIMITED", H),
          p("The regulations contained in Table F of Schedule I to the Companies Act, 2013 shall apply to the Company except in so far as they are inconsistent with these Articles."),
          Paragraph("Interpretation", H2), p("In these Articles, \"the Act\" means the Companies Act, 2013; \"the Board\" means the Board of Directors of the Company; \"Seal\" means the common seal of the Company, if any."),
          Paragraph("Private Company", H2), p("The Company is a private company within the meaning of Section 2(68) of the Act and accordingly: (a) restricts the right to transfer its shares; (b) limits the number of its members to two hundred; (c) prohibits any invitation to the public to subscribe for any securities of the Company."),
          Paragraph("Share Capital and Variation of Rights", H2), p("Subject to the provisions of the Act and these Articles, the shares in the capital of the Company shall be under the control of the Board who may issue, allot or otherwise dispose of the same to such persons, on such terms and conditions as they think fit."),
          Paragraph("Lien", H2), p("The Company shall have a first and paramount lien on every share (not being a fully paid share) for all monies, whether presently payable or not, called or payable at a fixed time in respect of that share."),
          Paragraph("Calls on Shares", H2), p("The Board may from time to time make calls upon the members in respect of any monies unpaid on their shares, provided that no call shall exceed one-fourth of the nominal value of the share."),
          Paragraph("Transfer and Transmission of Shares", H2), p("The instrument of transfer of any share shall be executed by or on behalf of both the transferor and transferee. The Board may decline to register the transfer of a share on which the Company has a lien. On the death of a member, the survivors or legal representatives shall be the only persons recognised by the Company as having any title to the shares."),
          Paragraph("Alteration of Capital", H2), p("The Company may, by ordinary resolution, increase the share capital, consolidate and divide its share capital, or sub-divide its existing shares."),
          Paragraph("General Meetings", H2), p("All general meetings other than the annual general meeting shall be called extraordinary general meetings. Two members personally present shall be the quorum for a general meeting."),
          Paragraph("Voting Rights", H2), p("On a show of hands every member present in person shall have one vote, and on a poll the voting rights of members shall be in proportion to their share in the paid-up equity share capital."),
          Paragraph("Board of Directors", H2), p(f"The number of directors shall not be less than two and not more than fifteen. The first directors of the Company shall be {', '.join(random.sample(PEOPLE, 2))}."),
          Paragraph("Proceedings of the Board", H2), p("The Board may meet for the conduct of business, adjourn and otherwise regulate its meetings as it thinks fit. Questions arising at any meeting shall be decided by a majority of votes."),
          Paragraph("Dividends and Reserve", H2), p("The Company in general meeting may declare dividends, but no dividend shall exceed the amount recommended by the Board."),
          Paragraph("Accounts, Winding Up and Indemnity", H2), p("The books of account shall be kept at the registered office. In a winding up, the liquidator may, with the sanction of a special resolution, divide amongst the members the assets of the Company in specie. Every officer shall be indemnified out of the assets of the Company against liability incurred in defending proceedings in which judgment is given in their favour."),
          Paragraph("Subscribers", H2),
          tbl([["Name, address and occupation of subscriber", "Signature", "Witness"]] + [[f"{x}, {random.choice(CITIES)[0]}, Business", "Sd/-", "Sd/-"] for x in random.sample(PEOPLE, 2)], [90 * mm, 30 * mm, 40 * mm])]
    return s


# ---------------------------------------------------------------- MoA
def moa(company=None):
    c = company or co(); city, state = random.choice(CITIES)
    auth = random.choice([10, 15, 25, 50]) * 100000
    obj = random.choice(["to carry on the business of developing, licensing and maintaining software products, artificial intelligence solutions and data analytics services",
                         "to carry on the business of manufacturing, trading and distribution of engineering components and industrial equipment",
                         "to carry on the business of providing logistics, warehousing and supply chain management services",
                         "to carry on the business of providing legal technology, compliance management and regulatory advisory platforms"])
    subs = random.sample(PEOPLE, 2)
    return [p("THE COMPANIES ACT, 2013", C), p("COMPANY LIMITED BY SHARES", C),
            Paragraph(f"MEMORANDUM OF ASSOCIATION OF {c.upper()} PRIVATE LIMITED", H),
            Paragraph("I. Name Clause", H2), p(f"The name of the Company is {c} Private Limited."),
            Paragraph("II. Registered Office Clause", H2), p(f"The registered office of the Company will be situated in the State of {state}."),
            Paragraph("III. Objects Clause", H2),
            p(f"(A) The objects to be pursued by the Company on its incorporation are: 1. {obj[0].upper() + obj[1:]}, in India and abroad."),
            p("(B) Matters which are necessary for furtherance of the objects specified in clause III(A) are: 1. To acquire, purchase, take on lease or otherwise any movable or immovable property. 2. To borrow or raise money in such manner as the Company shall think fit. 3. To enter into partnership or any arrangement for sharing profits with any person or company. 4. To open bank accounts and to draw, accept and negotiate instruments."),
            Paragraph("IV. Liability Clause", H2), p("The liability of the member(s) is limited and this liability is limited to the amount unpaid, if any, on the shares held by them."),
            Paragraph("V. Capital Clause", H2), p(f"The Authorised Share Capital of the Company is Rs. {auth:,} (Rupees {auth // 100000} Lakh only) divided into {auth // 10:,} equity shares of Rs. 10 each."),
            Paragraph("VI. Subscription Clause", H2),
            p("We, the several persons, whose names and addresses are subscribed, are desirous of being formed into a company in pursuance of this memorandum of association, and we respectively agree to take the number of shares in the capital of the company set against our respective names."),
            tbl([["Subscriber Details", "No. of shares taken", "DIN/PAN", "Signature"]] + [[f"{x}, {city}", f"{random.randint(1, 5) * 5000:,}", f"ABCPD{random.randint(1000, 9999)}K", "Sd/-"] for x in subs], [65 * mm, 35 * mm, 35 * mm, 25 * mm]),
            p(f"Signed before me: {random.choice(PEOPLE)}, Chartered Accountant, Membership No. {random.randint(100000, 199999)}"),
            p(f"Place: {city}    Date: {date()}")]


# ---------------------------------------------------------------- Balance sheet
def bs():
    c = co(); fy = FORCE.get("fy") or random.choice([2025, 2026]); f = lambda: random.randint(50, 9000) / 10
    eq = {"Share capital": f(), "Reserves and surplus": f()}
    ncl = {"Long-term borrowings": f(), "Deferred tax liabilities (net)": f()}
    cl = {"Trade payables": f(), "Other current liabilities": f(), "Short-term provisions": f()}
    tot = sum(eq.values()) + sum(ncl.values()) + sum(cl.values())
    nca = {"Property, plant and equipment": f(), "Intangible assets": f(), "Non-current investments": f()}
    ca_ = {"Inventories": f(), "Trade receivables": f(), "Short-term loans and advances": f()}
    cash = round(tot - sum(nca.values()) - sum(ca_.values()), 1)
    if cash < 10:  # rebalance
        ca_["Trade receivables"] += 20 - cash; cash = 20.0
    ca_["Cash and cash equivalents"] = cash
    py = lambda v: f"{v * random.uniform(0.8, 1.1):,.1f}"
    rows = [["Particulars", "Note", f"As at 31 March {fy}", f"As at 31 March {fy - 1}"], ["I. EQUITY AND LIABILITIES", "", "", ""], ["(1) Shareholders' funds", "", "", ""]]
    n = 1
    for grp, label in [(eq, None), (ncl, "(2) Non-current liabilities"), (cl, "(3) Current liabilities")]:
        if label: rows.append([label, "", "", ""])
        for k, v in grp.items():
            rows.append([k, str(n), f"{v:,.1f}", py(v)]); n += 1
    rows.append(["TOTAL", "", f"{tot:,.1f}", ""])
    rows += [["II. ASSETS", "", "", ""], ["(1) Non-current assets", "", "", ""]]
    for k, v in nca.items(): rows.append([k, str(n), f"{v:,.1f}", py(v)]); n += 1
    rows.append(["(2) Current assets", "", "", ""])
    for k, v in ca_.items(): rows.append([k, str(n), f"{v:,.1f}", py(v)]); n += 1
    rows.append(["TOTAL", "", f"{tot:,.1f}", ""])
    return [p(f"{c.upper()} PRIVATE LIMITED", C), p(f"CIN: {cin()}", C),
            Paragraph(f"BALANCE SHEET AS AT 31ST MARCH {fy}", H), p("(All amounts in INR lakhs, unless otherwise stated)", C),
            tbl(rows, [80 * mm, 15 * mm, 35 * mm, 35 * mm]), Spacer(1, 8),
            p("The accompanying notes form an integral part of the financial statements."),
            Paragraph("Significant Accounting Policies", H2),
            p("The financial statements have been prepared under the historical cost convention on an accrual basis in accordance with the Accounting Standards notified under Section 133 of the Companies Act, 2013. Property, plant and equipment are stated at cost less accumulated depreciation. Inventories are valued at lower of cost and net realisable value."),
            p(f"As per our report of even date. For {random.choice(['Joshi & Associates', 'Mehta Bansal & Co.', 'Rao Iyer LLP'])}, Chartered Accountants, FRN: {random.randint(100000, 199999)}W"),
            p(f"Partner: {random.choice(PEOPLE)}, M. No. {random.randint(100000, 199999)}.  For and on behalf of the Board of Directors: {random.choice(PEOPLE)}, Director (DIN {random.randint(10000000, 99999999)}). Place: {random.choice(CITIES)[0]}. Date: {date()}")]


# ---------------------------------------------------------------- Employment contract
def ec():
    c = co(); e = FORCE.get("person") or random.choice(PEOPLE); role = random.choice(["Software Engineer", "Senior Data Scientist", "Legal Associate", "Product Manager", "Compliance Analyst", "HR Executive"])
    ctc = random.randint(6, 45) * 100000
    basic = int(ctc * 0.4); hra = int(basic * 0.5); pf = int(basic * 0.12); spl = ctc - basic - hra - pf
    return [Paragraph("EMPLOYMENT AGREEMENT", H),
            p(f"This Employment Agreement is made on {date()} between {c} Private Limited, having its office at {addr()} (the \"Company\" or \"Employer\"), and {e}, residing at {addr()} (the \"Employee\")."),
            Paragraph("1. Appointment and Position", H2), p(f"The Company appoints the Employee as {role}, reporting to the {random.choice(['Engineering Manager', 'Head of Legal', 'Chief Technology Officer', 'Head of HR'])}. The Employee's date of joining shall be {date()}."),
            Paragraph("2. Probation", H2), p(f"The Employee shall be on probation for {random.choice([3, 6])} months, which may be extended at the Company's discretion. Confirmation shall be communicated in writing."),
            Paragraph("3. Compensation", H2), p("The Employee's annual Cost to Company (CTC) is set out below, subject to applicable tax deductions at source."),
            tbl([["Component", "Annual (INR)"], ["Basic salary", f"{basic:,}"], ["House rent allowance", f"{hra:,}"], ["Employer PF contribution", f"{pf:,}"], ["Special allowance", f"{spl:,}"], ["Total CTC", f"{ctc:,}"]], [80 * mm, 50 * mm]),
            Paragraph("4. Working Hours and Leave", H2), p("Normal working hours are 9:30 am to 6:30 pm, Monday to Friday. The Employee is entitled to 18 days of earned leave, 8 days of casual leave and public holidays as per the Company holiday calendar."),
            Paragraph("5. Duties", H2), p("The Employee shall devote full working time to the Company's business and shall not take up any other employment, paid or unpaid, without prior written consent."),
            Paragraph("6. Confidentiality and Intellectual Property", H2), p("The Employee shall keep confidential all information relating to the Company and its clients during and after employment. All work product created in the course of employment shall belong exclusively to the Company."),
            Paragraph("7. Non-Solicitation", H2), p("For twelve months after leaving, the Employee shall not solicit any employee or client of the Company."),
            Paragraph("8. Notice Period and Termination", H2), p(f"After confirmation, either party may terminate employment by giving {random.choice([30, 60, 90])} days' written notice or salary in lieu thereof. The Company may terminate without notice for misconduct."),
            Paragraph("9. Governing Law", H2), p(f"This Agreement shall be governed by the laws of India and subject to the jurisdiction of courts at {random.choice(CITIES)[0]}."),
            tbl([["For " + c + " Private Limited", "Employee"], ["Authorised Signatory", e], ["Date: " + date(), "Date: " + date()]], [85 * mm, 85 * mm])]


# ---------------------------------------------------------------- NDA
def nda():
    a, b = two_cos(); yrs = random.choice([2, 3, 5])
    return [Paragraph("MUTUAL NON-DISCLOSURE AGREEMENT", H),
            p(f"This Mutual Non-Disclosure Agreement (\"Agreement\") is entered into on {date()} between {a} Private Limited and {b} Private Limited (each a \"Party\" and together the \"Parties\")."),
            Paragraph("1. Purpose", H2), p(f"The Parties wish to exchange certain confidential information solely for the purpose of evaluating a potential business relationship concerning {random.choice(['a joint go-to-market partnership', 'a possible acquisition of a minority stake', 'a technology licensing arrangement', 'a proposed vendor engagement'])} (the \"Purpose\"). No obligation to enter into any further agreement is created hereby."),
            Paragraph("2. Confidential Information", H2), p("\"Confidential Information\" means any information disclosed by one Party (the \"Disclosing Party\") to the other (the \"Receiving Party\"), whether oral, written or electronic, that is marked confidential or would reasonably be understood to be confidential, including business plans, source code, customer lists and financial data."),
            Paragraph("3. Obligations of the Receiving Party", H2), p("The Receiving Party shall (a) use Confidential Information only for the Purpose; (b) not disclose it to any third party except to its employees and advisers who need to know and are bound by similar obligations; and (c) protect it with at least reasonable care."),
            Paragraph("4. Exclusions", H2), p("Obligations do not apply to information that is or becomes public without breach, was already known to the Receiving Party, is independently developed, or is required to be disclosed by law or court order, provided prompt notice is given."),
            Paragraph("5. Term", H2), p(f"This Agreement remains in force for {yrs} years from the date above, and confidentiality obligations survive for {yrs} years after expiry."),
            Paragraph("6. Return of Materials", H2), p("On written request, the Receiving Party shall return or destroy all Confidential Information and certify such destruction in writing."),
            Paragraph("7. No Licence; No Warranty", H2), p("Nothing in this Agreement grants any licence under any intellectual property right. All information is provided \"as is\"."),
            Paragraph("8. Remedies and Governing Law", H2), p("The Parties agree that breach may cause irreparable harm and that the Disclosing Party may seek injunctive relief. This Agreement is governed by the laws of India.")] + sign_block(a, b)


# ---------------------------------------------------------------- OTHER (not in RL)
def invoice():
    a, b = two_cos(); rows = [["#", "Description", "SAC", "Qty", "Rate (INR)", "Amount (INR)"]]; sub = 0
    for i, d in enumerate(random.sample(["Cloud hosting charges", "Software subscription - annual", "Professional services - implementation", "Support and maintenance", "Training workshop"], 3), 1):
        q = random.randint(1, 12); r = random.randint(5, 90) * 1000; sub += q * r
        rows.append([str(i), d, "998314", str(q), f"{r:,}", f"{q * r:,}"])
    g = int(sub * 0.09)
    rows += [["", "Sub-total", "", "", "", f"{sub:,}"], ["", "CGST @ 9%", "", "", "", f"{g:,}"], ["", "SGST @ 9%", "", "", "", f"{g:,}"], ["", "Grand Total", "", "", "", f"{sub + 2 * g:,}"]]
    return [Paragraph("TAX INVOICE", H), tbl([["Supplier", a + " Pvt. Ltd."], ["GSTIN", f"27ABCDE{random.randint(1000, 9999)}F1Z5"], ["Invoice No.", f"INV/{random.randint(2025, 2026)}/{random.randint(100, 999)}"],
            ["Invoice Date", date()], ["Bill To", b + " Pvt. Ltd., " + addr()], ["Place of Supply", "Maharashtra (27)"]], [45 * mm, 110 * mm]), Spacer(1, 8),
            tbl(rows, [8 * mm, 62 * mm, 18 * mm, 12 * mm, 25 * mm, 30 * mm]), Spacer(1, 6),
            p("Payment due within 30 days. Bank: HDFC Bank, A/c No. XXXXXX4417, IFSC HDFC0001234. This is a computer-generated invoice.")]


def purchase_order():
    a, b = two_cos(); rows = [["Item", "Specification", "Qty", "Unit Price (INR)", "Total (INR)"]]
    for it in random.sample(["Laptop - 16GB RAM", "Ergonomic chair", "27-inch monitor", "Network switch 24-port", "UPS 2kVA"], 3):
        q = random.randint(2, 40); u = random.randint(3, 90) * 1000; rows.append([it, "As per quotation", str(q), f"{u:,}", f"{q * u:,}"])
    return [Paragraph("PURCHASE ORDER", H), p(f"PO Number: PO-{random.randint(10000, 99999)}    Date: {date()}"),
            p(f"Buyer: {a} Private Limited, {addr()}"), p(f"Vendor: {b} Private Limited, {addr()}"),
            tbl(rows, [45 * mm, 40 * mm, 15 * mm, 30 * mm, 30 * mm]),
            Paragraph("Terms and Conditions", H2), p("Delivery within 21 days of PO date at the buyer's Pune office. Payment 45 days from receipt of goods and invoice. Goods are subject to inspection and acceptance. Warranty as per manufacturer terms."),
            p("Approved by: Procurement Head")]


def leave_policy():
    c = co()
    return [Paragraph(f"{c} - LEAVE POLICY", H), p(f"Policy No. HR-POL-0{random.randint(10, 40)}  |  Effective from {date()}  |  Owner: Human Resources"),
            Paragraph("1. Objective", H2), p("This policy sets out the types of leave available to employees and the process for applying for and approving leave."),
            Paragraph("2. Applicability", H2), p("This policy applies to all full-time employees on the rolls of the company in India, including those on probation."),
            Paragraph("3. Types of Leave", H2), tbl([["Leave type", "Entitlement per year", "Carry forward"], ["Earned leave", "18 days", "Up to 45 days"], ["Casual leave", "8 days", "No"], ["Sick leave", "8 days", "No"], ["Maternity leave", "26 weeks", "N/A"], ["Paternity leave", "10 days", "No"]], [55 * mm, 50 * mm, 45 * mm]),
            Paragraph("4. Application Process", H2), p("Leave must be applied for through the HRMS portal at least seven days in advance for planned leave. Unplanned leave must be intimated to the reporting manager by 10 am on the day of absence."),
            Paragraph("5. Encashment", H2), p("Earned leave balance beyond the carry-forward limit may be encashed at the end of the calendar year at the rate of basic salary.")]


# ---------------------------------------------------------------- build
def build(path, flow):
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm)
    doc.build(flow)


def to_scanned(path):
    tmp = path + "_img"; os.makedirs(tmp, exist_ok=True)
    subprocess.run(["pdftoppm", "-r", "110", "-gray", "-png", path, f"{tmp}/pg"], check=True)
    from PIL import Image, ImageFilter
    import img2pdf
    imgs = []
    for f in sorted(glob.glob(f"{tmp}/pg*.png")):
        im = Image.open(f).convert("L").rotate(random.uniform(-1.2, 1.2), fillcolor=255, expand=False).filter(ImageFilter.GaussianBlur(0.6))
        out = f.replace(".png", "_s.jpg"); im.save(out, quality=55); imgs.append(out)
    with open(path, "wb") as fh: fh.write(img2pdf.convert(imgs))
    shutil.rmtree(tmp)


GEN = {"MSA": msa, "SOW": sow, "MoM": mom, "AoA": aoa, "MoA": moa, "BS": bs, "EC": ec, "NDA": nda}

# (filename, generator, expected_label, case_type, filename_hint, notes)
cases = [
    # MSA
    ("MSA_Sahyadri_Kaveri_2025.pdf", msa, "MSA", "standard", "abbrev", ""),
    ("MSA-signed-final.pdf", msa, "MSA", "standard", "abbrev", ""),
    ("Master_Services_Agreement_v3.pdf", msa, "MSA", "standard", "full_name", "Name spelled out, no 'MSA' token"),
    ("Agreement_executed_copy.pdf", msa, "MSA", "neutral_name", "none", "Generic 'agreement' name - could be MSA/NDA/EC"),
    ("doc_0017.pdf", msa, "MSA", "neutral_name", "none", ""),
    # SOW
    ("SOW-001-03.pdf", sow, "SOW", "standard", "abbrev", "Same naming pattern as your ES sample"),
    ("SOW_DataPlatform_Phase2.pdf", sow, "SOW", "standard", "abbrev", ""),
    ("Statement_of_Work_ERP.pdf", sow, "SOW", "standard", "full_name", ""),
    ("Project_Scope_Document.pdf", sow, "SOW", "neutral_name", "none", ""),
    ("Document (3).pdf", sow, "SOW", "neutral_name", "none", ""),
    # MoM
    ("MoM_Steering_Committee_Aug.pdf", mom, "MoM", "standard", "abbrev", ""),
    ("MOM-weekly-sync-14.pdf", mom, "MoM", "standard", "abbrev", "Uppercase MOM - tests case-insensitive match"),
    ("Minutes_QBR_Q2.pdf", mom, "MoM", "standard", "full_name", ""),
    ("meeting_notes_final.pdf", mom, "MoM", "neutral_name", "none", ""),
    ("doc_0042.pdf", mom, "MoM", "neutral_name", "none", ""),
    # AoA
    ("AoA_Kaveri_Analytics.pdf", aoa, "AoA", "standard", "abbrev", ""),
    ("AOA-Narmada-Fintech.pdf", aoa, "AoA", "standard", "abbrev", ""),
    ("Articles_of_Association_Deccan.pdf", aoa, "AoA", "standard", "full_name", ""),
    ("Company_Constitution_Part2.pdf", aoa, "AoA", "neutral_name", "none", ""),
    ("scan_20260211.pdf", aoa, "AoA", "neutral_name", "none", "Digital text despite 'scan' in name"),
    # MoA
    ("MoA_Indus_Legal.pdf", moa, "MoA", "standard", "abbrev", ""),
    ("MOA_Godavari_Logistics.pdf", moa, "MoA", "standard", "abbrev", ""),
    ("Memorandum_of_Association_Vindhya.pdf", moa, "MoA", "standard", "full_name", ""),
    ("Company_Constitution_Part1.pdf", moa, "MoA", "neutral_name", "none", "Pairs with Part2 (AoA) - near-identical name"),
    ("doc_0063.pdf", moa, "MoA", "neutral_name", "none", ""),
    # BS
    ("BS_FY26_Sahyadri.pdf", bs, "BS", "standard", "abbrev", ""),
    ("BS-Audited-2025.pdf", bs, "BS", "standard", "abbrev", ""),
    ("BalanceSheet_FY2026.pdf", bs, "BS", "standard", "full_name", "'BalanceSheet' is one token - won't match 'BS'"),
    ("Financials_Annexure.pdf", bs, "BS", "neutral_name", "none", ""),
    ("doc_0081.pdf", bs, "BS", "neutral_name", "none", ""),
    # EC
    ("EC_Rohan_Kulkarni.pdf", ec, "EC", "standard", "abbrev", ""),
    ("EC-2026-0412.pdf", ec, "EC", "standard", "abbrev", ""),
    ("Employment_Agreement_Neha_Joshi.pdf", ec, "EC", "standard", "full_name", "Uses 'Agreement' not 'Contract'"),
    ("Offer_and_Terms_signed.pdf", ec, "EC", "neutral_name", "none", ""),
    ("doc_0099.pdf", ec, "EC", "neutral_name", "none", ""),
    # NDA
    ("NDA_Konkan_Aravalli.pdf", nda, "NDA", "standard", "abbrev", ""),
    ("Mutual-NDA-2025.pdf", nda, "NDA", "standard", "abbrev", ""),
    ("Non_Disclosure_Agreement_Brahmaputra.pdf", nda, "NDA", "standard", "full_name", ""),
    ("Confidentiality_Agreement_draft.pdf", nda, "NDA", "neutral_name", "none", "Could be confused with MSA confidentiality clause"),
    ("doc_0105.pdf", nda, "NDA", "neutral_name", "none", ""),
    # Hard cases
    ("SOW_final_v2.pdf", msa, "MSA", "misnamed", "misleading", "MSA content, filename says SOW - content must win"),
    ("MSA_Annexure_B.pdf", sow, "SOW", "misnamed", "misleading", "SOW content, filename says MSA - content must win"),
    ("MSA_Nilgiri_Tapti_Legal_Review.pdf", lambda: msa(cover=True), "MSA", "cover_page", "abbrev", "Page 1 = cover, page 2 = TOC; real text starts page 3"),
    ("Incorporation_Certified_Copy.pdf", lambda: aoa(cover=True), "AoA", "cover_page", "none", "Cover page first; no filename hint"),
    ("Incorporation_Docs_Chambal_Agritech.pdf", lambda: moa("Chambal Agritech") + [PageBreak()] + aoa(company="Chambal Agritech"), "MoA", "combined", "none", "MoA + AoA in one PDF; primary=MoA (first). Accept MoA, flag if multi-label"),
    ("Tax_Invoice_INV_2026_311.pdf", invoice, "OTHER", "out_of_rl", "none", "Not in RL - should be OTHER / no_match"),
    ("PO_Laptops_Q3.pdf", purchase_order, "OTHER", "out_of_rl", "misleading", "'PO' not in RL; content mentions payment/vendor like MSA/SOW"),
    ("HR_Leave_Policy_2026.pdf", leave_policy, "OTHER", "out_of_rl", "none", "HR doc - may get pulled toward EC"),
    ("MoM_scanned_board_meeting.pdf", mom, "MoM", "scanned", "abbrev", "Image-only PDF, no text layer. Needs OCR; else status=error"),
    ("scanned_doc_0007.pdf", bs, "BS", "scanned", "none", "Image-only PDF, no text layer, no filename hint. Needs OCR; else status=error"),
]
assert len(cases) == 50, len(cases)

gt = []
FORCES = {
    "MSA_Sahyadri_Kaveri_2025.pdf": {"cos": ["Sahyadri Infotech", "Kaveri Analytics"]},
    "SOW-001-03.pdf": {"sow_no": 1},
    "AoA_Kaveri_Analytics.pdf": {"cos": ["Kaveri Analytics"]},
    "AOA-Narmada-Fintech.pdf": {"cos": ["Narmada Fintech Solutions"]},
    "Articles_of_Association_Deccan.pdf": {"cos": ["Deccan Cloudworks"]},
    "MoA_Indus_Legal.pdf": {"cos": ["Indus Legal Systems"]},
    "MOA_Godavari_Logistics.pdf": {"cos": ["Godavari Logistics"]},
    "Memorandum_of_Association_Vindhya.pdf": {"cos": ["Vindhya Data Labs"]},
    "Company_Constitution_Part1.pdf": {"cos": ["Shivneri Engineering"]},
    "Company_Constitution_Part2.pdf": {"cos": ["Shivneri Engineering"]},
    "BS_FY26_Sahyadri.pdf": {"cos": ["Sahyadri Infotech"], "fy": 2026},
    "BS-Audited-2025.pdf": {"fy": 2025},
    "BalanceSheet_FY2026.pdf": {"fy": 2026},
    "EC_Rohan_Kulkarni.pdf": {"person": "Rohan Kulkarni"},
    "Employment_Agreement_Neha_Joshi.pdf": {"person": "Neha Joshi"},
    "NDA_Konkan_Aravalli.pdf": {"cos": ["Konkan Retail Ventures", "Aravalli Consulting"]},
    "Non_Disclosure_Agreement_Brahmaputra.pdf": {"cos": ["Brahmaputra Energy", "Nilgiri Software"]},
    "MSA_Nilgiri_Tapti_Legal_Review.pdf": {"cos": ["Nilgiri Software", "Tapti Manufacturing"]},
}
for fname, g, label, case, hint, notes in cases:
    FORCE.clear(); FORCE.update({k: (list(v) if isinstance(v, list) else v) for k, v in FORCES.get(fname, {}).items()})
    path = f"{FILES}/{fname}"
    build(path, g())
    if case == "scanned":
        to_scanned(path)
    gt.append({"file_name": fname, "expected_label": label, "case_type": case, "filename_hint": hint, "notes": notes})

with open(f"{OUT}/ground_truth.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["file_name", "expected_label", "case_type", "filename_hint", "notes"]); w.writeheader(); w.writerows(gt)
print("built", len(gt))
