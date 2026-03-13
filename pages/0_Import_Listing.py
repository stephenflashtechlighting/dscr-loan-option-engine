import streamlit as st
from services.listing_import import import_listing, estimate_insurance
from db import upsert_deal
from models import Deal
from config import OBJECTIVE_MODES, OBJECTIVE_MODE_LABELS
from ui_components import section_title

st.title("🏡 Import Listing")
st.caption("Paste a Zillow, Redfin, or Realtor.com URL to pre-fill a deal from the listing.")

# ── URL input ─────────────────────────────────────────────────────────────────
section_title("Step 1 — Paste listing URL")

url = st.text_input(
    "Listing URL",
    placeholder="https://www.zillow.com/homedetails/... or https://www.redfin.com/...",
)

use_ai = st.checkbox(
    "Use AI fallback if fields are missing",
    value=True,
    help="If the page parser can't find price or address, Claude will read the page text and extract them."
)

if "listing_result" not in st.session_state:
    st.session_state["listing_result"] = None

if st.button("🔍 Fetch listing", type="primary", disabled=not url.strip()):
    with st.spinner("Fetching listing data..."):
        result = import_listing(url.strip(), use_ai_fallback=use_ai)
    if "error" in result:
        st.error(f"Could not import listing: {result['error']}")
        st.session_state["listing_result"] = None
    else:
        st.session_state["listing_result"] = result
        st.success(f"Listing fetched from {result.get('source', 'listing page')}.")

# ── Review and save ───────────────────────────────────────────────────────────
listing = st.session_state.get("listing_result")

if listing:
    section_title("Step 2 — Review and complete deal details")

    conf_color = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(listing.get("confidence", "low"), "🔴")
    st.caption(f"Data confidence: {conf_color} {listing.get('confidence', 'low').upper()} — always verify against the actual listing before using for decisions.")

    # Show what was found
    with st.expander("Raw data extracted from listing", expanded=False):
        display = {k: v for k, v in listing.items() if k not in ("source_url",) and v is not None}
        st.json(display)

    st.markdown("---")

    with st.form("listing_deal_form"):
        col1, col2 = st.columns(2)

        with col1:
            deal_name = st.text_input(
                "Deal name *",
                value=listing.get("full_address", listing.get("address", "")).split(",")[0] if listing.get("full_address") or listing.get("address") else "",
                help="Give this deal a memorable name"
            )
            property_address = st.text_input(
                "Property address",
                value=listing.get("full_address", listing.get("address", ""))
            )
            purchase_price = st.number_input(
                "Purchase price ($) *",
                value=float(listing.get("purchase_price", 300000.0)),
                min_value=1000.0,
                step=5000.0,
                help="Pre-filled from listing" if listing.get("purchase_price") else "Not found — enter manually"
            )
            down_pct = st.number_input("Down payment (%)", value=25.0, min_value=0.0, max_value=100.0, step=0.5)

        with col2:
            monthly_rent = st.number_input(
                "Gross monthly rent ($) *",
                value=0.0,
                min_value=0.0,
                step=50.0,
                help="Not available from listing — enter your rent estimate or pull from RentCast"
            )
            annual_taxes = st.number_input(
                "Annual property taxes ($)",
                value=float(listing.get("annual_taxes", 3600.0)),
                min_value=0.0,
                step=100.0,
                help="Pre-filled from listing" if listing.get("annual_taxes") else "Estimated — verify with county records"
            )
            ins_default = listing.get("annual_insurance_estimate", 1800.0)
            annual_insurance = st.number_input(
                "Annual insurance ($)",
                value=float(ins_default),
                min_value=0.0,
                step=100.0,
                help=f"Estimated at ~{'1.0' if ins_default / max(purchase_price, 1) > 0.008 else '0.6'}% of value — adjust for actual quote"
            )

        # Property info for context
        info_parts = []
        if listing.get("beds"):
            info_parts.append(f"{listing['beds']} bed")
        if listing.get("baths"):
            info_parts.append(f"{listing['baths']} bath")
        if listing.get("sqft"):
            info_parts.append(f"{listing['sqft']:,} sqft")
        if listing.get("year_built"):
            info_parts.append(f"built {listing['year_built']}")
        if info_parts:
            st.caption(f"Property: {' · '.join(info_parts)}")

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

        submitted = st.form_submit_button("💾 Create deal from listing", type="primary")

    if submitted:
        if not deal_name:
            st.error("Deal name is required.")
        elif monthly_rent <= 0:
            st.warning("Monthly rent is $0 — you can save and update later, but DSCR will not calculate correctly.")
        
        if deal_name:
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

    # Rent estimate helper
    with st.expander("💡 Rent estimation tips"):
        price = listing.get("purchase_price", 0)
        beds = listing.get("beds")
        sqft = listing.get("sqft")
        st.markdown(f"""
**Quick estimates for this property:**
- 1% rule: **${price * 0.01:,.0f}/month** (aggressive target)
- 0.7% rule: **${price * 0.007:,.0f}/month** (more realistic in most markets)
- 0.5% rule: **${price * 0.005:,.0f}/month** (conservative / LCOL markets)

**Better sources for rent comps:**
- [Rentometer](https://www.rentometer.com) — free basic comps by address
- [RentCast](https://www.rentcast.io) — API-grade rental data (~$50/mo)
- [Zillow Rental Manager](https://www.zillow.com/rental-manager/price-my-rental/) — free estimate by address
- Search Zillow/Facebook Marketplace for active rentals nearby
        """)
