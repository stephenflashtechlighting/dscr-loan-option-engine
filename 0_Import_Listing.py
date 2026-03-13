import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import streamlit as st
from services.listing_import import import_listing, estimate_insurance, ai_extract_listing
from db import upsert_deal
from models import Deal
from config import OBJECTIVE_MODES, OBJECTIVE_MODE_LABELS
from ui_components import section_title

st.title("🏡 Import Listing")
st.caption("Pre-fill a deal from a listing — paste text, enter a URL, or fill in manually.")

# ── Method tabs ───────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs([
    "📋 Paste listing text",
    "🔗 Try URL (Redfin / Realtor.com)",
    "✏️ Enter manually"
])

if "listing_result" not in st.session_state:
    st.session_state["listing_result"] = None

# ── Tab 1: Paste text (recommended) ──────────────────────────────────────────
with tab1:
    st.caption("Copy the listing details from Zillow, Redfin, your MLS, or any email/document and paste them here. Claude will extract the fields.")
    
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

Or paste directly from Zillow's listing page, an MLS sheet, or a forwarded email.""",
        key="paste_listing_text"
    )

    if st.button("🔍 Extract from text", type="primary", disabled=not (pasted or "").strip(), key="extract_paste_btn"):
        with st.spinner("Reading listing text..."):
            result = ai_extract_listing(pasted)
        if "error" in result and not result.get("purchase_price"):
            st.error(f"Extraction failed: {result['error']}")
        else:
            # Build full address
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

# ── Tab 2: URL ────────────────────────────────────────────────────────────────
with tab2:
    st.caption("Works best with Redfin and Realtor.com. Zillow blocks server-side requests — use the Paste Text tab for Zillow listings.")
    
    url = st.text_input(
        "Listing URL",
        placeholder="https://www.redfin.com/... or https://www.realtor.com/...",
        key="listing_url"
    )
    use_ai = st.checkbox("Use AI fallback if fields are missing", value=True, key="url_ai_fallback")

    if st.button("🔍 Fetch listing", type="primary", disabled=not (url or "").strip(), key="fetch_url_btn"):
        with st.spinner("Fetching listing..."):
            result = import_listing(url.strip(), use_ai_fallback=use_ai)
        if "error" in result:
            st.error(f"Could not import listing: {result['error']}")
            st.info("Try copying the listing text and using the **Paste listing text** tab instead.")
        else:
            st.session_state["listing_result"] = result
            st.success(f"Fetched from {result.get('source', 'listing page')}.")
            st.rerun()

# ── Tab 3: Manual entry ───────────────────────────────────────────────────────
with tab3:
    st.caption("Skip the import — just fill in the fields directly.")
    
    if st.button("📝 Open manual form", key="manual_btn"):
        st.session_state["listing_result"] = {
            "source": "Manual entry",
            "confidence": "high",
            "full_address": "",
            "purchase_price": 0.0,
            "annual_taxes": 0.0,
            "annual_insurance_estimate": 0.0,
        }
        st.rerun()

# ── Shared review form ────────────────────────────────────────────────────────
listing = st.session_state.get("listing_result")

if listing:
    st.markdown("---")
    section_title("Review and complete deal details")

    conf_color = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(listing.get("confidence", "low"), "🔴")
    source = listing.get("source", "")
    st.caption(f"Source: {source}  |  Confidence: {conf_color} — verify all fields before saving.")

    # Show what was found vs what needs manual entry
    found = []
    missing = []
    check_fields = [
        ("purchase_price", "Purchase price"),
        ("annual_taxes", "Property taxes"),
        ("annual_insurance_estimate", "Insurance estimate"),
        ("address", "Address"),
        ("beds", "Beds/baths"),
    ]
    for key, label in check_fields:
        if listing.get(key):
            found.append(label)
        else:
            missing.append(label)

    col_f, col_m = st.columns(2)
    with col_f:
        if found:
            st.success("**Pulled from listing:** " + "  ·  ".join(found))
    with col_m:
        if missing:
            st.warning("**Needs your input:** " + "  ·  ".join(missing))

    if listing.get("fields") or any(k in listing for k in ("purchase_price", "address", "annual_taxes")):
        with st.expander("Raw extracted data", expanded=False):
            display = {k: v for k, v in listing.items() if k not in ("source_url",) and v is not None}
            st.json(display)

    with st.form("listing_deal_form"):
        col1, col2 = st.columns(2)

        with col1:
            # Build default deal name from address
            addr = listing.get("full_address") or listing.get("address") or ""
            default_name = addr.split(",")[0] if addr else ""
            
            deal_name = st.text_input("Deal name *", value=default_name)
            property_address = st.text_input("Property address", value=addr)
            purchase_price = st.number_input(
                "Purchase price ($) *",
                value=float(listing.get("purchase_price") or 0.0),
                min_value=0.0, step=5000.0,
                help="✅ Pre-filled from listing" if listing.get("purchase_price") else "⚠️ Not found — enter manually"
            )
            down_pct = st.number_input("Down payment (%)", value=25.0, min_value=0.0, max_value=100.0, step=0.5)

        with col2:
            monthly_rent = st.number_input(
                "Gross monthly rent ($) *",
                value=0.0, min_value=0.0, step=50.0,
                help="Not available in listing data — enter your rent estimate"
            )
            annual_taxes = st.number_input(
                "Annual property taxes ($)",
                value=float(listing.get("annual_taxes") or 0.0),
                min_value=0.0, step=100.0,
                help="✅ Pre-filled from listing" if listing.get("annual_taxes") else "⚠️ Not found in listing — verify with county records"
            )
            ins_default = listing.get("annual_insurance_estimate") or listing.get("annual_insurance") or 0.0
            annual_insurance = st.number_input(
                "Annual insurance ($)",
                value=float(ins_default),
                min_value=0.0, step=100.0,
                help="✅ Estimated from purchase price & state" if ins_default else "⚠️ Not found — enter your insurance quote"
            )

        # Property info if available
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

    # Rent estimation helper
    price = listing.get("purchase_price", 0) or 0
    if price > 0:
        with st.expander("💡 Rent estimation benchmarks"):
            st.markdown(f"""
| Rule | Monthly Rent | Notes |
|------|-------------|-------|
| 1% rule | **${price * 0.01:,.0f}** | Aggressive — hard to hit in most markets |
| 0.7% rule | **${price * 0.007:,.0f}** | Realistic in many Sunbelt markets |
| 0.5% rule | **${price * 0.005:,.0f}** | Conservative / expensive markets |

**Comp sources:** [Rentometer](https://www.rentometer.com)  ·  [Zillow Rental Manager](https://www.zillow.com/rental-manager/price-my-rental/)  ·  [RentCast](https://www.rentcast.io)
            """)
