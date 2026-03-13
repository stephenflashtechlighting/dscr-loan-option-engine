import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import streamlit as st
from services.listing_import import import_listing, estimate_insurance, ai_extract_listing
from db import upsert_deal
from models import Deal
from config import OBJECTIVE_MODE_LABELS
from ui_components import section_title

st.title("🏡 Import Listing")
st.caption("Pre-fill a deal from Trulia, pasted listing text, or manual entry.")

# ── Method tabs ───────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs([
    "📋 Paste listing text",
    "🔗 Import from Trulia URL",
    "✏️ Enter manually"
])

if "listing_result" not in st.session_state:
    st.session_state["listing_result"] = None

# ── Tab 1: Paste text ────────────────────────────────────────────────────────
with tab1:
    st.caption("Best fallback for any listing source. Paste the property text and the app will try to extract price, address, and taxes.")
    pasted = st.text_area(
        "Paste listing details here",
        height=220,
        placeholder="""Example — paste anything like:

1149 Hodges St, Lake Charles, LA 70601
3 bed / 2 bath / 1,450 sqft
List price: $189,000
Property taxes: $2,400/year
Built 1978
HOA: None

Or paste directly from Trulia, MLS, Zillow, Redfin, Realtor, or an email.""",
        key="paste_listing_text"
    )

    if st.button("🔍 Extract from text", type="primary", disabled=not (pasted or "").strip(), key="extract_paste_btn"):
        with st.spinner("Reading listing text..."):
            result = ai_extract_listing(pasted)
        if "error" in result and not result.get("purchase_price"):
            st.error(f"Extraction failed: {result['error']}")
        else:
            parts = [result.get("address"), result.get("city_state_zip")]
            result["full_address"] = ", ".join(p for p in parts if p)
            if result.get("purchase_price") and "annual_insurance_estimate" not in result:
                state = ""
                czp = result.get("city_state_zip", "")
                import re
                m = re.search(r',\s*([A-Z]{2})\b', czp)
                if m:
                    state = m.group(1)
                result["annual_insurance_estimate"] = estimate_insurance(result["purchase_price"], state)
            result["source"] = "Pasted text (AI)"
            result["confidence"] = "medium"
            st.session_state["listing_result"] = result
            st.success("Fields extracted — review below.")
            st.rerun()

# ── Tab 2: Trulia URL ────────────────────────────────────────────────────────
with tab2:
    st.caption("This URL importer is tuned for Trulia. For anything else, use paste text or manual entry.")
    url = st.text_input(
        "Trulia listing URL",
        placeholder="https://www.trulia.com/home/...",
        key="listing_url"
    )
    use_ai = st.checkbox("Use AI fallback if any critical field is missing", value=True, key="url_ai_fallback")

    if st.button("🔍 Fetch listing", type="primary", disabled=not (url or "").strip(), key="fetch_url_btn"):
        clean_url = url.strip()
        if "trulia.com" not in clean_url.lower():
            st.error("Use a Trulia URL here. For non-Trulia listings, use paste text or manual entry.")
        else:
            with st.spinner("Fetching listing..."):
                result = import_listing(clean_url, use_ai_fallback=use_ai)
            if "error" in result:
                st.error(f"Could not import listing: {result['error']}")
                st.info("Try copying the listing text and using the **Paste listing text** tab instead.")
            else:
                st.session_state["listing_result"] = result
                st.success(f"Fetched from {result.get('source', 'listing page')}.")
                st.rerun()

# ── Tab 3: Manual entry ───────────────────────────────────────────────────────
with tab3:
    st.caption("Use this when the property is off-market, not publicly listed, or you simply want to key it in yourself.")
    if st.button("📝 Open manual form", key="manual_btn"):
        st.session_state["listing_result"] = {
            "source": "Manual entry",
            "confidence": "high",
            "full_address": "",
            "purchase_price": None,
            "annual_taxes": None,
            "annual_insurance_estimate": None,
        }
        st.rerun()

listing = st.session_state.get("listing_result")

if listing:
    st.markdown("---")
    section_title("Review and complete deal details")

    conf_color = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(listing.get("confidence", "low"), "🔴")
    source = listing.get("source", "")
    st.caption(f"Source: {source}  |  Confidence: {conf_color} — verify all fields before saving.")

    for warning in listing.get("warnings", []):
        st.warning(warning)

    if listing.get("fields") or any(k in listing for k in ("purchase_price", "address", "annual_taxes")):
        with st.expander("Raw extracted data", expanded=False):
            display = {k: v for k, v in listing.items() if k not in ("source_url",) and v is not None}
            st.json(display)

    with st.form("listing_deal_form"):
        col1, col2 = st.columns(2)
        with col1:
            addr = listing.get("full_address") or listing.get("address") or ""
            default_name = addr.split(",")[0] if addr else ""
            deal_name = st.text_input("Deal name *", value=default_name)
            property_address = st.text_input("Property address", value=addr)
            purchase_price = st.number_input(
                "Purchase price ($) *",
                value=float(listing.get("purchase_price") or 300000.0),
                min_value=1000.0, step=5000.0,
                help="Pre-filled from listing" if listing.get("purchase_price") else "Enter manually"
            )
            down_pct = st.number_input("Down payment (%)", value=25.0, min_value=0.0, max_value=100.0, step=0.5)

        with col2:
            monthly_rent = st.number_input(
                "Gross monthly rent ($) *",
                value=0.0, min_value=0.0, step=50.0,
                help="Enter your rent estimate or known lease amount"
            )
            annual_taxes = st.number_input(
                "Annual property taxes ($)",
                value=float(listing.get("annual_taxes") or 3600.0),
                min_value=0.0, step=100.0,
                help="Pre-filled from listing" if listing.get("annual_taxes") else "Enter manually or verify with county"
            )
            ins_default = listing.get("annual_insurance_estimate") or 1800.0
            annual_insurance = st.number_input(
                "Annual insurance ($)",
                value=float(ins_default),
                min_value=0.0, step=100.0,
                help="Estimated or manual — adjust to your quote"
            )

        info_parts = []
        for key, label in [("beds", "bed"), ("baths", "bath"), ("sqft", "sqft"), ("year_built", "built")]:
            val = listing.get(key)
            if val:
                info_parts.append(f"{val:,} {label}" if key == "sqft" else f"{val} {label}")
        if info_parts:
            st.caption("Property: " + "  ·  ".join(info_parts))

        section_title("Borrower intent")
        col3, col4 = st.columns(2)
        with col3:
            hold_months = st.number_input("Expected hold period (months)", value=60, min_value=1, max_value=480, step=6)
            refi_prob = st.slider("Refinance probability", 0.0, 1.0, value=0.30, step=0.05)
        with col4:
            mode_options = list(OBJECTIVE_MODE_LABELS.values())
            obj_mode_label = st.selectbox("Recommendation objective", mode_options)
            obj_mode = [k for k, v in OBJECTIVE_MODE_LABELS.items() if v == obj_mode_label][0]

        if listing.get("hoa_monthly"):
            st.info(f"⚠️ HOA detected: ${listing['hoa_monthly']:,.0f}/month — factor this into your cash flow analysis.")

        col_save, col_clear = st.columns([2, 5])
        with col_save:
            submitted = st.form_submit_button("💾 Create deal", type="primary")
        with col_clear:
            clear = st.form_submit_button("🗑 Start over")

    if submitted:
        if not deal_name:
            st.error("Deal name is required.")
        else:
            if monthly_rent <= 0:
                st.warning("Monthly rent is $0 — you can update it later on the Deal Intake page.")
            deal = Deal(
                deal_name=deal_name,
                property_address=property_address or None,
                purchase_price=purchase_price,
                down_payment_percent=down_pct,
                monthly_rent=monthly_rent,
                annual_taxes=annual_taxes,
                annual_insurance=annual_insurance,
                hold_months=int(hold_months),
                refinance_probability=refi_prob,
                objective_mode=obj_mode,
            )
            did = upsert_deal(deal)
            st.session_state["active_deal_id"] = did
            st.session_state["listing_result"] = None
            st.success(f"Deal created (ID {did})! Go to **Scenario Builder** to add loan quotes.")
            st.balloons()

    if clear:
        st.session_state["listing_result"] = None
        st.rerun()
