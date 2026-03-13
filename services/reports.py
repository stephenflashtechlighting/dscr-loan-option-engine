from __future__ import annotations
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime
from pathlib import Path
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)
from models import Deal, LoanScenario
from services.calculations import (
    monthly_total_payment, total_cash_to_close, dscr,
    prepay_flexibility_score, hold_period_alignment_score,
    estimated_prepay_cost, cash_to_close_breakdown, loan_amount, monthly_ti,
)
from services.ranking import rank_scenarios, explain_recommendation
from config import EXPORT_DIR

DARK = colors.HexColor("#1e293b")
ACCENT = colors.HexColor("#2563eb")
LIGHT_ROW = colors.HexColor("#f1f5f9")
MID_ROW = colors.HexColor("#e2e8f0")
WHITE = colors.white


def scenarios_to_dataframe(deal: Deal, scenarios: list[LoanScenario]) -> pd.DataFrame:
    rows = []
    for s in scenarios:
        rows.append({
            "ID": s.id,
            "Lender": s.lender_name,
            "Program": s.program_name,
            "Rate %": s.rate_percent,
            "Points %": s.points_percent,
            "IO Months": s.interest_only_months,
            "Prepay Months": s.prepay_months,
            "Monthly P&I": round(monthly_total_payment(deal, s) - monthly_ti(deal), 2),
            "Monthly Total": round(monthly_total_payment(deal, s), 2),
            "Cash to Close": round(total_cash_to_close(deal, s), 2),
            "DSCR": round(dscr(deal, s), 3),
            "Flexibility": round(prepay_flexibility_score(s), 1),
            "Hold Fit": round(hold_period_alignment_score(deal, s), 1),
            "Est. Prepay Cost": round(estimated_prepay_cost(deal, s), 2),
            "Source": s.source_type,
            "Notes": s.notes,
        })
    return pd.DataFrame(rows)


def dataframe_to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def build_decision_memo_text(deal: Deal, scenarios: list[LoanScenario]) -> str:
    ranked = rank_scenarios(deal, scenarios, deal.objective_mode)
    lines = [
        "=" * 60,
        "DSCR LOAN OPTION ENGINE — DECISION MEMO",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "=" * 60,
        "",
        f"Deal:            {deal.deal_name}",
        f"Address:         {deal.property_address or 'Not provided'}",
        f"Purchase price:  ${deal.purchase_price:,.0f}",
        f"Loan amount:     ${loan_amount(deal.purchase_price, deal.down_payment_percent):,.0f}",
        f"Monthly rent:    ${deal.monthly_rent:,.0f}",
        f"Hold period:     {deal.hold_months} months",
        f"Refi probability:{deal.refinance_probability * 100:.0f}%",
        f"Objective mode:  {deal.objective_mode}",
        "",
    ]
    if ranked:
        top = ranked[0]
        s = top["scenario"]
        lines += [
            "TOP RECOMMENDATION",
            "-" * 40,
            f"  {s.lender_name} / {s.program_name}",
            f"  Rate:          {s.rate_percent:.3f}%  ({s.points_percent:.3f} pts)",
            f"  Monthly total: ${top['monthly_total']:,.2f}",
            f"  Cash to close: ${top['cash_to_close']:,.2f}",
            f"  DSCR:          {top['dscr']:.3f}",
            f"  Flexibility:   {top['flexibility']:.0f}/100",
            f"  Hold fit:      {top['hold_fit']:.0f}/100",
            "",
        ]
    lines.append("SCENARIO RANKING")
    lines.append("-" * 40)
    for idx, item in enumerate(ranked, start=1):
        s = item["scenario"]
        lines.append(
            f"  {idx}. {s.lender_name} | {s.program_name} | "
            f"{s.rate_percent:.3f}% | ${item['monthly_total']:,.0f}/mo | "
            f"${item['cash_to_close']:,.0f} CTC | DSCR {item['dscr']:.3f} | "
            f"score {item['score']:.2f}"
        )
    lines += [
        "",
        "-" * 60,
        "This memo is a decision-support artifact.",
        "It does not constitute lending, legal, or financial advice.",
    ]
    return "\n".join(lines)


def build_decision_memo_pdf(
    deal: Deal,
    scenarios: list[LoanScenario],
    output_path: str | Path | None = None,
) -> Path:
    ranked = rank_scenarios(deal, scenarios, deal.objective_mode)
    out = Path(output_path) if output_path else EXPORT_DIR / f"memo_deal_{deal.id or 'x'}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Title2", parent=styles["Title"], fontSize=16, textColor=DARK, spaceAfter=4)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=11, textColor=ACCENT, spaceBefore=12, spaceAfter=4)
    body = ParagraphStyle("Body", parent=styles["BodyText"], fontSize=9, leading=14)
    small = ParagraphStyle("Small", parent=styles["BodyText"], fontSize=8, textColor=colors.grey)
    bold_body = ParagraphStyle("BoldBody", parent=body, fontName="Helvetica-Bold")

    doc = SimpleDocTemplate(
        str(out), pagesize=LETTER,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch
    )
    story = []

    # Header
    story.append(Paragraph("DSCR Loan Option Engine", title_style))
    story.append(Paragraph("Decision Memo — Confidential", small))
    story.append(Paragraph(f"Generated {datetime.now().strftime('%B %d, %Y at %H:%M')}", small))
    story.append(HRFlowable(width="100%", thickness=1.5, color=ACCENT, spaceAfter=10))

    # Deal summary
    story.append(Paragraph("Deal Summary", h2))
    deal_data = [
        ["Deal name", deal.deal_name, "Hold period", f"{deal.hold_months} months"],
        ["Address", deal.property_address or "—", "Refi probability", f"{deal.refinance_probability*100:.0f}%"],
        ["Purchase price", f"${deal.purchase_price:,.0f}", "Monthly rent", f"${deal.monthly_rent:,.0f}"],
        ["Loan amount", f"${loan_amount(deal.purchase_price, deal.down_payment_percent):,.0f}", "Objective mode", deal.objective_mode],
    ]
    dt = Table(deal_data, colWidths=[1.2 * inch, 2.0 * inch, 1.4 * inch, 1.6 * inch])
    dt.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 0), (0, -1), DARK),
        ("TEXTCOLOR", (2, 0), (2, -1), DARK),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [WHITE, LIGHT_ROW]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(dt)
    story.append(Spacer(1, 12))

    # Top recommendation
    if ranked:
        top = ranked[0]
        s = top["scenario"]
        story.append(Paragraph("Top Recommendation", h2))
        story.append(Paragraph(
            f"<b>{s.lender_name} / {s.program_name}</b> — "
            f"Rate {s.rate_percent:.3f}% ({s.points_percent:.3f} pts) | "
            f"Monthly ${top['monthly_total']:,.2f} | "
            f"Cash to close ${top['cash_to_close']:,.2f} | "
            f"DSCR {top['dscr']:.3f}",
            body
        ))
        # Plain text explanation (strip markdown bold)
        explanation = explain_recommendation(ranked, deal.objective_mode)
        explanation_clean = explanation.replace("**", "")
        story.append(Spacer(1, 4))
        story.append(Paragraph(explanation_clean, body))
        story.append(Spacer(1, 12))

    # Scenario comparison table
    story.append(Paragraph("Scenario Comparison", h2))
    headers = ["Rank", "Lender", "Program", "Rate %", "Pts", "Prepay", "Monthly", "Cash CTC", "DSCR", "Flex", "Score"]
    table_data = [headers]
    for idx, item in enumerate(ranked, start=1):
        s = item["scenario"]
        prepay_label = "None" if s.prepay_months == 0 else f"{s.prepay_months}mo"
        table_data.append([
            str(idx),
            s.lender_name[:18],
            s.program_name[:14],
            f"{s.rate_percent:.3f}",
            f"{s.points_percent:.2f}",
            prepay_label,
            f"${item['monthly_total']:,.0f}",
            f"${item['cash_to_close']:,.0f}",
            f"{item['dscr']:.3f}",
            f"{item['flexibility']:.0f}",
            f"{item['score']:.1f}",
        ])
    col_widths = [0.35, 1.35, 1.1, 0.55, 0.4, 0.5, 0.7, 0.75, 0.5, 0.4, 0.5]
    col_widths = [w * inch for w in col_widths]
    ct = Table(table_data, colWidths=col_widths, repeatRows=1)
    ct.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), DARK),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, LIGHT_ROW]),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        # Highlight rank 1
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#dbeafe")),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
    ]))
    story.append(ct)
    story.append(Spacer(1, 12))

    # Cash-to-close breakdown for top scenario
    if ranked:
        story.append(Paragraph(f"Cash-to-Close Breakdown — {ranked[0]['scenario'].lender_name}", h2))
        breakdown = cash_to_close_breakdown(deal, ranked[0]["scenario"])
        bd_data = [["Item", "Amount"]]
        for label, amount in breakdown.items():
            bd_data.append([label, f"{'–' if amount < 0 else ''}${abs(amount):,.0f}"])
        total = sum(breakdown.values())
        bd_data.append(["TOTAL CASH TO CLOSE", f"${total:,.0f}"])
        bt = Table(bd_data, colWidths=[3.5 * inch, 1.5 * inch])
        bt.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("BACKGROUND", (0, 0), (-1, 0), DARK),
            ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
            ("BACKGROUND", (0, -1), (-1, -1), MID_ROW),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2), [WHITE, LIGHT_ROW]),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(bt)
        story.append(Spacer(1, 12))

    # Disclaimer
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey, spaceBefore=8))
    story.append(Paragraph(
        "This memo is a decision-support artifact generated by the DSCR Loan Option Engine. "
        "It does not constitute lending, legal, financial, or compliance advice. "
        "All estimates should be verified against final lender disclosures before closing.",
        small
    ))

    doc.build(story)
    return out
