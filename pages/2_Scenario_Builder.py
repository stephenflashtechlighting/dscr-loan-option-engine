import streamlit as st
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_deal, list_scenarios, upsert_scenario, delete_scenario, duplicate_scenario
from models import LoanScenario
from config import PREPAY_TYPES, FEE_DEFAULTS
from ui_components import section_title, styled_dataframe
from services.calculations import monthly_total_payment, total_cash_to_close, dscr

st.title("🔢 Scenario Builder")

active_id = st.session_state.get("active_deal_id")
if not active_id:
    st.warning("Load or create a deal first on the **Deal Intake** page.")
    st.stop()

deal = get_deal(active_id)
scenarios = list_scenarios(active_id)

st.caption(f"Deal: **{deal.deal_name}** — {len(scenarios)} scenario(s)")

# ── Existing scenarios ────────────────────────────────────────────────────────
if scenarios:
    section_title("Current scenarios")
    for s in scenarios:
        with st.container():
            col_info, col_dup, col_del = st.columns([6, 1, 1])
            with col_info:
                payment = monthly_total_payment(deal, s)
                ctc = total_cash_to_close(deal, s)
                coverage = dscr(deal, s)
                badge = "🤖" if s.source_type == "ai_extracted" else ("📥" if s.source_type == "extracted" else "✏️")
                st.markdown(
                    f"{badge} **#{s.id} {s.lender_name} / {s.program_name}** — "
                    f"{s.rate_percent:.3f}% | {s.points_percent:.2f} pts | "
                    f"prepay {s.prepay_months}mo | "
                    f"**${payment:,.0f}/mo** | **${ctc:,.0f} CTC** | DSCR {coverage:.3f}"
                )
                if s.notes:
                    st.caption(f"Notes: {s.notes}")
            with col_dup:
                if st.button("Copy", key=f"dup_{s.id}"):
                    duplicate_scenario(s.id)
                    st.rerun()
            with col_del:
                if st.button("🗑", key=f"del_{s.id}"):
                    delete_scenario(s.id)
                    st.rerun()

st.markdown("---")

# ── Edit existing or create new ───────────────────────────────────────────────
edit_id = st.session_state.get("edit_scenario_id")
editing: LoanScenario | None = None
if edit_id:
    from db import get_scenario
    editing = get_scenario(edit_id)

section_title(f"{'Edit' if editing else 'Add'} scenario")

with st.form("scenario_form"):
    col1, col2 = st.columns(2)
    with col1:
        lender_name = st.text_input("Lender name *", value=editing.lender_name if editing else "")
        program_name = st.text_input("Program name", value=editing.program_name if editing else "DSCR 30yr")
        rate_percent = st.number_input("Note rate (%)", value=editing.rate_percent if editing else 7.25, min_value=0.0, max_value=25.0, step=0.125, format="%.3f")
        points_percent = st.number_input("Points (%)", value=editing.points_percent if editing else 0.0, min_value=-5.0, max_value=10.0, step=0.125, format="%.3f")
        loan_term = st.number_input("Loan term (months)", value=editing.loan_term_months if editing else 360, min_value=60, max_value=480, step=12)
        amort_months = st.number_input("Amortization (months)", value=editing.amortization_months if editing else 360, min_value=60, max_value=480, step=12)
        io_months = st.number_input("Interest-only period (months)", value=editing.interest_only_months if editing else 0, min_value=0, max_value=120, step=12)

    with col2:
        prepay_type = st.selectbox("Prepay type", PREPAY_TYPES,
                                    index=PREPAY_TYPES.index(editing.prepay_type) if editing and editing.prepay_type in PREPAY_TYPES else 0)
        prepay_months = st.number_input("Prepay window (months)", value=editing.prepay_months if editing else 60, min_value=0, max_value=120, step=6)
        uw_fee = st.number_input("Underwriting fee ($)", value=editing.underwriting_fee if editing else FEE_DEFAULTS["underwriting_fee"], min_value=0.0, step=50.0)
        proc_fee = st.number_input("Processing fee ($)", value=editing.processing_fee if editing else FEE_DEFAULTS["processing_fee"], min_value=0.0, step=50.0)
        appraisal = st.number_input("Appraisal fee ($)", value=editing.appraisal_fee if editing else FEE_DEFAULTS["appraisal_fee"], min_value=0.0, step=50.0)
        title_fee = st.number_input("Title / settlement ($)", value=editing.title_fee if editing else FEE_DEFAULTS["title_fee"], min_value=0.0, step=50.0)
        credit = st.number_input("Lender credit ($)", value=editing.lender_credit if editing else 0.0, min_value=0.0, step=100.0)
        reserve_months = st.number_input("Reserve months required", value=editing.reserve_months if editing else 0, min_value=0, max_value=12, step=1)
        escrow_months = st.number_input("Escrow pre-fund months", value=editing.escrow_months if editing else 0, min_value=0, max_value=6, step=1)
        notes = st.text_area("Notes", value=editing.notes if editing else "", height=60)

    col_save, col_cancel = st.columns([1, 4])
    with col_save:
        submitted = st.form_submit_button("💾 Save scenario", type="primary")
    with col_cancel:
        if editing and st.form_submit_button("Cancel edit"):
            st.session_state.pop("edit_scenario_id", None)
            st.rerun()

if submitted:
    if not lender_name:
        st.error("Lender name is required.")
    elif rate_percent <= 0:
        st.error("Rate must be positive.")
    else:
        scenario = LoanScenario(
            id=editing.id if editing else None,
            deal_id=active_id,
            lender_name=lender_name,
            program_name=program_name,
            rate_percent=rate_percent,
            points_percent=points_percent,
            loan_term_months=int(loan_term),
            amortization_months=int(amort_months),
            interest_only_months=int(io_months),
            prepay_type=prepay_type,
            prepay_months=int(prepay_months),
            underwriting_fee=uw_fee,
            processing_fee=proc_fee,
            appraisal_fee=appraisal,
            title_fee=title_fee,
            lender_credit=credit,
            reserve_months=int(reserve_months),
            escrow_months=int(escrow_months),
            notes=notes,
            source_type=editing.source_type if editing else "manual",
            source_text=editing.source_text if editing else "",
        )
        sid = upsert_scenario(scenario)
        st.session_state.pop("edit_scenario_id", None)
        st.success(f"Scenario saved (ID {sid})")
        st.rerun()

# Edit selector
if scenarios:
    st.markdown("---")
    section_title("Edit an existing scenario")
    edit_options = {f"#{s.id} {s.lender_name} / {s.program_name}": s.id for s in scenarios}
    sel = st.selectbox("Select to edit", ["— select —"] + list(edit_options.keys()))
    if sel != "— select —" and st.button("Load for editing"):
        st.session_state["edit_scenario_id"] = edit_options[sel]
        st.rerun()
