import streamlit as st
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from db import get_deal, list_scenarios
from services.reports import scenarios_to_dataframe, dataframe_to_csv_bytes, build_decision_memo_pdf, build_decision_memo_text
from services.ranking import rank_scenarios, explain_recommendation
from services.calculations import breakeven_months, loan_amount, monthly_ti, cash_to_close_breakdown
from ui_components import section_title, metric_row, scenario_summary_card, styled_dataframe
from config import SCORING_WEIGHTS, OBJECTIVE_MODE_LABELS

st.title("📊 Comparison Dashboard")

active_id = st.session_state.get("active_deal_id")
if not active_id:
    st.warning("Load or create a deal first on the **Deal Intake** page.")
    st.stop()

deal = get_deal(active_id)
scenarios = list_scenarios(active_id)

if not scenarios:
    st.info("Add at least one scenario on the **Scenario Builder** page.")
    st.stop()

# ── Scoring weight override ───────────────────────────────────────────────────
with st.expander("⚙️ Override scoring weights (balanced / best_long_hold modes)"):
    st.caption("Adjust these sliders to tune the ranking policy. Changes apply to this session only.")
    mode_key = "best_long_hold" if deal.objective_mode == "best_long_hold" else "balanced"
    base_w = SCORING_WEIGHTS[mode_key]
    col1, col2, col3 = st.columns(3)
    with col1:
        w_dscr = st.slider("DSCR weight", 0.0, 100.0, float(base_w["dscr"]), step=1.0)
        w_flex = st.slider("Flexibility weight", 0.0, 2.0, float(base_w["flexibility"]), step=0.05)
    with col2:
        w_pmt = st.slider("Payment weight (neg)", -0.1, 0.0, float(base_w["payment"]), step=0.005, format="%.3f")
        w_cash = st.slider("Cash weight (neg)", -0.01, 0.0, float(base_w["cash"]), step=0.001, format="%.3f")
    with col3:
        w_hold = st.slider("Hold-fit weight", 0.0, 2.0, float(base_w.get("hold_fit", 0.45)), step=0.05)
        w_risk = st.slider("Prepay risk weight (neg)", -0.1, 0.0, float(base_w.get("prepay_risk", -0.04)), step=0.005, format="%.3f")

    weight_overrides = {
        "dscr": w_dscr, "flexibility": w_flex, "payment": w_pmt,
        "cash": w_cash, "hold_fit": w_hold, "prepay_risk": w_risk,
    }
    st.caption("ℹ️ These weights are a policy choice. There is no objectively correct set. Adjust to reflect your investment priorities.")

# ── Run ranking ───────────────────────────────────────────────────────────────
ranked = rank_scenarios(deal, scenarios, deal.objective_mode, weight_overrides)
df = scenarios_to_dataframe(deal, scenarios)
top = ranked[0]
runner_up = ranked[1] if len(ranked) > 1 else None

# ── Deal context ──────────────────────────────────────────────────────────────
section_title("Deal context", deal.deal_name)
metric_row([
    ("Purchase price", f"${deal.purchase_price:,.0f}", None),
    ("Loan amount", f"${loan_amount(deal.purchase_price, deal.down_payment_percent):,.0f}", None),
    ("Monthly TI", f"${monthly_ti(deal):,.0f}", None),
    ("Hold period", f"{deal.hold_months} mo", None),
    ("Objective mode", deal.objective_mode, None),
])

# ── Recommendation cards ──────────────────────────────────────────────────────
section_title("Recommendation", f"Scoring mode: {OBJECTIVE_MODE_LABELS.get(deal.objective_mode, deal.objective_mode)}")

col1, col2 = st.columns(2)
with col1:
    s = top["scenario"]
    scenario_summary_card(
        "🥇 Top recommendation",
        [
            ("Lender / Program", f"{s.lender_name} / {s.program_name}"),
            ("Rate", f"{s.rate_percent:.3f}% · {s.points_percent:.3f} pts"),
            ("Monthly total", f"${top['monthly_total']:,.2f}"),
            ("Cash to close", f"${top['cash_to_close']:,.2f}"),
            ("DSCR", f"{top['dscr']:.3f}"),
            ("Flexibility / Hold fit", f"{top['flexibility']:.0f} / {top['hold_fit']:.0f}"),
            ("Est. prepay exposure", f"${top['prepay_risk']:,.0f}"),
            ("Score", f"{top['score']:.2f}"),
        ],
        highlight=True,
    )
with col2:
    if runner_up:
        s2 = runner_up["scenario"]
        scenario_summary_card(
            "🥈 Runner-up",
            [
                ("Lender / Program", f"{s2.lender_name} / {s2.program_name}"),
                ("Rate", f"{s2.rate_percent:.3f}% · {s2.points_percent:.3f} pts"),
                ("Monthly total", f"${runner_up['monthly_total']:,.2f}"),
                ("Cash to close", f"${runner_up['cash_to_close']:,.2f}"),
                ("DSCR", f"{runner_up['dscr']:.3f}"),
                ("Flexibility / Hold fit", f"{runner_up['flexibility']:.0f} / {runner_up['hold_fit']:.0f}"),
                ("Est. prepay exposure", f"${runner_up['prepay_risk']:,.0f}"),
                ("Score", f"{runner_up['score']:.2f}"),
            ],
        )
    else:
        st.info("Add a second scenario to unlock side-by-side comparison.")

# ── Plain-English explanation ─────────────────────────────────────────────────
explanation = explain_recommendation(ranked, deal.objective_mode)
st.info(explanation)

# ── Breakeven ─────────────────────────────────────────────────────────────────
if runner_up:
    b = breakeven_months(deal, top["scenario"], runner_up["scenario"])
    if b is not None:
        st.caption(
            f"📅 Estimated breakeven between top recommendation and runner-up: **{b} months**. "
            f"If you sell or refinance before month {b:.0f}, the runner-up may be more cost-effective."
        )

# ── Scenario comparison table ─────────────────────────────────────────────────
section_title("Scenario comparison table")
styled_dataframe(
    df,
    currency_cols=["Monthly P&I", "Monthly Total", "Cash to Close", "Est. Prepay Cost"],
    pct_cols=["DSCR"]
)

# ── Full ranked table ─────────────────────────────────────────────────────────
section_title("Full ranked output")
rank_df = pd.DataFrame([{
    "Rank": i + 1,
    "ID": item["scenario"].id,
    "Lender": item["scenario"].lender_name,
    "Program": item["scenario"].program_name,
    "Score": item["score"],
    "Monthly Total": item["monthly_total"],
    "Cash to Close": item["cash_to_close"],
    "DSCR": item["dscr"],
    "Flexibility": item["flexibility"],
    "Hold Fit": item["hold_fit"],
    "Prepay Risk": item["prepay_risk"],
} for i, item in enumerate(ranked)])
styled_dataframe(rank_df, currency_cols=["Monthly Total", "Cash to Close", "Prepay Risk"])

# ── Cash-to-close breakdown ───────────────────────────────────────────────────
with st.expander("Cash-to-close breakdown (top recommendation)"):
    breakdown = cash_to_close_breakdown(deal, top["scenario"])
    bd_df = pd.DataFrame([
        {"Item": k, "Amount": v} for k, v in breakdown.items()
    ])
    bd_df.loc[len(bd_df)] = {"Item": "TOTAL", "Amount": sum(breakdown.values())}
    styled_dataframe(bd_df, currency_cols=["Amount"])

# ── Exports ───────────────────────────────────────────────────────────────────
section_title("Export decision artifacts")
col_a, col_b, col_c = st.columns(3)

with col_a:
    csv_bytes = dataframe_to_csv_bytes(df)
    st.download_button(
        "📥 Download CSV",
        csv_bytes,
        file_name=f"deal_{deal.id}_scenarios.csv",
        mime="text/csv",
    )

with col_b:
    txt_memo = build_decision_memo_text(deal, scenarios)
    st.download_button(
        "📄 Download TXT memo",
        txt_memo,
        file_name=f"deal_{deal.id}_decision_memo.txt",
        mime="text/plain",
    )

with col_c:
    if st.button("📑 Generate PDF memo"):
        with st.spinner("Building PDF..."):
            pdf_path = build_decision_memo_pdf(deal, scenarios)
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
        st.download_button(
            "⬇️ Download PDF",
            pdf_bytes,
            file_name=f"deal_{deal.id}_decision_memo.pdf",
            mime="application/pdf",
        )
