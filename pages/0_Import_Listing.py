import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import re
import streamlit as st
from services.listing_import import estimate_insurance, ai_extract_listing
from db import upsert_deal
from models import Deal
from config import OBJECTIVE_MODE_LABELS
from ui_components import section_title

st.title("🏡 New Deal")
st.caption("Create a deal by pasting listing details or entering them manually.")

if "listing_result" not in st.session_state:
    st.session_state["listing_result"] = None

# ── Method tabs ───────────────────────────────────────────────────────────────
tab_paste, tab_manual = st.tabs([
    "📋 Paste listing text",
    "✏️ Enter manually",
])

# ── Tab 1: Paste text ─────────────────────────────────────────────────────────
with tab_paste:
    st.markdown(
        "**Go to the Trulia listing, select all the text on the page, copy it, and paste it below.** "
        "Claude will extract the fields — address, price, taxes, beds/baths, etc."
    )
    st.caption("Works with Trulia, Zillow, MLS printouts, forwarded emails, or any listing text.")

    pasted = st.text_area(
        "Paste listing text here",
        height=220,
        placeholder=(
            "Example — paste anything:\n\n"
            "1149 Hodges St, Lake Charles, LA 70601\n"
            "3 bed · 2 bath · 1,450 sqft\n"
            "List price: $189,000\n"
            "Property taxes: $2,400/yr\n"
            "Built 1978"
        ),
        key="paste_listing_text",
    )

    if st.button("🔍 Extract fields", type="primary", disabled=not (pasted or "").strip(), key="extract_btn"):
        with st.spinner("Reading listing..."):
            result = ai_extract_listing(pasted)

        if not result.get("purchase_price") and "error" in result:
            st.error(f"Extraction failed: {result['error']}")
        else:
            parts = [result.get("address"), result.get("city_state_zip")]
            result["full_address"] = ", ".join(p for p in parts if p)
            if result.get("purchase_price") and not result.get("annual_insurance_estimate"):
                czp = result.get("city_state_zip", "")
                m = re.search(r',\s*([A-Z]{2})\b', czp)
                state = m.group(1) if m else ""
                result["annual_insurance_estimate"] = estimate_insurance(result["purchase_price"], state)
            result["source"] = "Pasted text (AI)"
            result["confidence"] = "medium"
            st.session_state["listing_result"] = result
            st.rerun()

# ── Tab 2: Manual entry ───────────────────────────────────────────────────────
with tab_manual:
    st.markdown("No listing needed — enter the property details directly.")
    if st.button("📝 Open blank form", type="primary", key="manual_btn"):
        st.session_state["listing_result"] = {
            "source": "Manual entry",
            "confidence": "high",
        }
        st.rerun()

# ── Shared form ───────────────────────────────────────────────────────────────
listing = st.session_state.get("listing_result")

if listing is not None:
    st.markdown("---")
    source = listing.get("source", "")
    is_manual = source == "Manual entry"

    if not is_manual:
        section_title("Review extracted fields")
        conf = listing.get("confidence", "low")
        conf_icon = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(conf, "🔴")
        st.caption(f"Source: {source}  |  {conf_icon} Confidence — always verify before saving.")

        # Pulled vs missing
        check_fields = [
            ("purchase_price", "Purchase price"),
            ("annual_taxes", "Property taxes"),
            ("annual_insurance_estimate", "Insurance estimate"),
            ("address", "Address"),
            ("beds", "Beds/baths"),
        ]
        found   = [lbl for k, lbl in check_fields if listing.get(k)]
        missing = [lbl for k, lbl in check_fields if not listing.get(k)]
        c1, c2 = st.columns(2)
        with c1:
            if found:
                st.success("**Pulled:** " + "  ·  ".join(found))
        with c2:
            if missing:
                st.warning("**Enter manually:** " + "  ·  ".join(missing))

        with st.expander("Raw extracted data", expanded=False):
            st.json({k: v for k, v in listing.items() if v is not None})
    else:
        section_title("Enter deal details")

    # ── Form ──────────────────────────────────────────────────────────────────
    addr = listing.get("full_address") or listing.get("address") or ""
    default_name = addr.split(",")[0].strip() if addr else ""

    with st.form("deal_form"):
        st.markdown("##### Property")
        c1, c2 = st.columns(2)

        with c1:
            deal_name = st.text_input(
                "Deal name *",
                value=default_name,
                placeholder="e.g. 1149 Hodges St",
            )
            property_address = st.text_input(
                "Property address",
                value=addr,
                placeholder="Street, City, State ZIP",
            )
            purchase_price = st.number_input(
                "Purchase price ($) *",
                value=float(listing.get("purchase_price") or 0.0),
                min_value=0.0, step=5000.0, format="%.0f",
                help=("✅ From listing" if listing.get("purchase_price") else "⚠️ Not found — enter manually"),
            )
            down_pct = st.number_input(
                "Down payment (%)",
                value=25.0, min_value=0.0, max_value=100.0, step=0.5,
            )

        with c2:
            monthly_rent = st.number_input(
                "Gross monthly rent ($) *",
                value=0.0, min_value=0.0, step=50.0, format="%.0f",
                help="⚠️ Not in listing — enter your rent estimate",
            )
            annual_taxes = st.number_input(
                "Annual property taxes ($)",
                value=float(listing.get("annual_taxes") or 0.0),
                min_value=0.0, step=100.0, format="%.0f",
                help=("✅ From listing" if listing.get("annual_taxes") else "⚠️ Not found — check county records"),
            )
            ins_val = float(listing.get("annual_insurance_estimate") or listing.get("annual_insurance") or 0.0)
            annual_insurance = st.number_input(
                "Annual insurance ($)",
                value=ins_val,
                min_value=0.0, step=100.0, format="%.0f",
                help=("✅ Estimated from price & state" if ins_val else "⚠️ Not found — enter your quote"),
            )

        # Property detail line if available
        info_parts = []
        for key, lbl in [("beds","bed"),("baths","bath"),("sqft","sqft"),("year_built","built")]:
            v = listing.get(key)
            if v:
                info_parts.append(f"{int(v):,} {lbl}" if key == "sqft" else f"{v} {lbl}")
        if info_parts:
            st.caption("From listing: " + "  ·  ".join(info_parts))

        st.markdown("##### Borrower intent")
        c3, c4 = st.columns(2)
        with c3:
            hold_months = st.number_input(
                "Expected hold period (months)",
                value=60, min_value=1, max_value=480, step=6,
            )
            refi_prob = st.slider(
                "Refinance probability",
                0.0, 1.0, value=0.30, step=0.05,
                help="Chance you refinance before the prepay window closes",
            )
        with c4:
            mode_options = list(OBJECTIVE_MODE_LABELS.values())
            obj_label = st.selectbox("Recommendation objective", mode_options)
            obj_mode = [k for k, v in OBJECTIVE_MODE_LABELS.items() if v == obj_label][0]

        if listing.get("hoa_monthly"):
            st.info(f"HOA detected: ${listing['hoa_monthly']:,.0f}/month — factor into cash flow.")

        cs, cc = st.columns([2, 5])
        with cs:
            submitted = st.form_submit_button("💾 Create deal", type="primary")
        with cc:
            cleared = st.form_submit_button("✖ Start over")

    if submitted:
        if not deal_name:
            st.error("Deal name is required.")
        elif purchase_price <= 0:
            st.error("Purchase price must be greater than $0.")
        else:
            if monthly_rent <= 0:
                st.warning("Monthly rent is $0 — update it on the Deal Intake page before running analysis.")
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
            st.success(f"✅ Deal '{deal_name}' created! Go to **Scenario Builder** to add loan quotes.")
            st.balloons()

    if cleared:
        st.session_state["listing_result"] = None
        st.rerun()

    # Rent benchmarks
    price = listing.get("purchase_price") or 0
    if price > 0:
        with st.expander("💡 Rent estimation benchmarks"):
            st.markdown(f"""
| Rule | Monthly Rent | Notes |
|------|-------------|-------|
| 1% rule | **${price * 0.01:,.0f}** | Aggressive — rarely achievable today |
| 0.7% rule | **${price * 0.007:,.0f}** | Realistic in many Sunbelt markets |
| 0.5% rule | **${price * 0.005:,.0f}** | Conservative / high-price markets |

**Sources:** [Rentometer](https://www.rentometer.com) · [Zillow Rental Manager](https://www.zillow.com/rental-manager/price-my-rental/) · [RentCast](https://www.rentcast.io)
""")
