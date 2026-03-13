import streamlit as st
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_deal, upsert_deal, list_deals
from models import Deal
from config import OBJECTIVE_MODES, OBJECTIVE_MODE_LABELS
from ui_components import section_title

st.title("📋 Deal Intake")
st.caption("Create a new deal or load an existing one to edit.")

# ── Load existing deal ────────────────────────────────────────────────────────
deals = list_deals()
active_id = st.session_state.get("active_deal_id")

if deals:
    with st.expander("Load an existing deal", expanded=(active_id is None)):
        options = {f"{d.deal_name} (${d.purchase_price:,.0f})": d.id for d in deals}
        choice = st.selectbox("Select deal", ["— new deal —"] + list(options.keys()))
        if choice != "— new deal —" and st.button("Load selected deal"):
            st.session_state["active_deal_id"] = options[choice]
            st.rerun()

# Pre-fill form if editing
existing: Deal | None = None
if active_id:
    existing = get_deal(active_id)

st.markdown("---")
section_title("Property details")

with st.form("deal_form"):
    col1, col2 = st.columns(2)
    with col1:
        deal_name = st.text_input("Deal name *", value=existing.deal_name if existing else "")
        property_address = st.text_input("Property address", value=existing.property_address or "" if existing else "")
        purchase_price = st.number_input("Purchase price ($) *", value=existing.purchase_price if existing else 300000.0, min_value=1000.0, step=5000.0)
        down_payment_pct = st.number_input("Down payment (%)", value=existing.down_payment_percent if existing else 25.0, min_value=0.0, max_value=100.0, step=0.5)

    with col2:
        monthly_rent = st.number_input("Gross monthly rent ($) *", value=existing.monthly_rent if existing else 2200.0, min_value=0.0, step=50.0)
        annual_taxes = st.number_input("Annual property taxes ($)", value=existing.annual_taxes if existing else 3600.0, min_value=0.0, step=100.0)
        annual_insurance = st.number_input("Annual insurance ($)", value=existing.annual_insurance if existing else 1800.0, min_value=0.0, step=100.0)

    section_title("Borrower intent")
    col3, col4 = st.columns(2)
    with col3:
        hold_months = st.number_input("Expected hold period (months)", value=existing.hold_months if existing else 60, min_value=1, max_value=480, step=6)
        refi_prob = st.slider("Refinance probability", 0.0, 1.0, value=existing.refinance_probability if existing else 0.30, step=0.05,
                               help="How likely are you to refinance or sell before the prepayment window closes?")

    with col4:
        mode_label = OBJECTIVE_MODE_LABELS.get(existing.objective_mode, existing.objective_mode) if existing else list(OBJECTIVE_MODE_LABELS.values())[0]
        mode_options = list(OBJECTIVE_MODE_LABELS.values())
        mode_index = mode_options.index(mode_label) if mode_label in mode_options else 0
        obj_mode_label = st.selectbox("Recommendation objective", mode_options, index=mode_index)
        obj_mode = [k for k, v in OBJECTIVE_MODE_LABELS.items() if v == obj_mode_label][0]

    submitted = st.form_submit_button("💾 Save deal", type="primary")

if submitted:
    if not deal_name:
        st.error("Deal name is required.")
    elif purchase_price <= 0:
        st.error("Purchase price must be positive.")
    else:
        deal = Deal(
            id=existing.id if existing else None,
            deal_name=deal_name,
            property_address=property_address or None,
            purchase_price=purchase_price,
            down_payment_percent=down_payment_pct,
            monthly_rent=monthly_rent,
            annual_taxes=annual_taxes,
            annual_insurance=annual_insurance,
            hold_months=int(hold_months),
            refinance_probability=refi_prob,
            objective_mode=obj_mode,
        )
        did = upsert_deal(deal)
        st.session_state["active_deal_id"] = did
        st.success(f"Deal saved! (ID {did})")
        st.rerun()

# Quick derived stats preview
if existing:
    from services.calculations import loan_amount, monthly_ti
    la = loan_amount(existing.purchase_price, existing.down_payment_percent)
    ti = monthly_ti(existing)
    st.markdown("---")
    section_title("Deal snapshot")
    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("Loan amount", f"${la:,.0f}")
    col_b.metric("Monthly TI", f"${ti:,.0f}")
    col_c.metric("GRM", f"{existing.purchase_price / (existing.monthly_rent * 12):.1f}x")
    col_d.metric("Gross yield", f"{existing.monthly_rent * 12 / existing.purchase_price * 100:.2f}%")
