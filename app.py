import streamlit as st
from db import init_db
from config import APP_TITLE, APP_VERSION

st.set_page_config(
    page_title=APP_TITLE,
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

init_db()

st.sidebar.title(f"🏠 {APP_TITLE}")
st.sidebar.caption(f"v{APP_VERSION}")

# Active deal indicator in sidebar
if "active_deal_id" in st.session_state and st.session_state["active_deal_id"]:
    from db import get_deal
    d = get_deal(st.session_state["active_deal_id"])
    if d:
        st.sidebar.success(f"Active deal: **{d.deal_name}**")
    else:
        st.session_state.pop("active_deal_id", None)
else:
    st.sidebar.info("No active deal. Go to **Deal Intake** to create or load one.")

st.sidebar.markdown("---")
st.sidebar.markdown("""
**Pages**
- 🏠 Home — deal list
- 📋 Deal Intake — create / edit deal
- 🔢 Scenario Builder — add / edit scenarios
- 📊 Comparison Dashboard — rankings & exports
- 📥 Import Quote — paste or upload a quote
- ⚙️ Settings — fee defaults & scoring weights
""")

st.title(f"🏠 {APP_TITLE}")
st.markdown(
    "Use the **sidebar** to navigate. Start with **Deal Intake** to create a new deal, "
    "then add scenarios in the **Scenario Builder**."
)

from db import list_deals, deal_scenario_count
from ui_components import deal_card

deals = list_deals()

if not deals:
    st.info("No deals yet. Go to **Deal Intake** in the sidebar to create your first deal.")
else:
    st.subheader(f"Recent deals ({len(deals)})")
    for deal in deals[:10]:
        sc = deal_scenario_count(deal.id)
        deal_card(deal, sc)
        col1, col2, col3 = st.columns([1, 1, 4])
        with col1:
            if st.button("Load", key=f"load_{deal.id}"):
                st.session_state["active_deal_id"] = deal.id
                st.success(f"Loaded: {deal.deal_name}")
                st.rerun()
        with col2:
            if st.button("Delete", key=f"del_{deal.id}", type="secondary"):
                from db import delete_deal
                delete_deal(deal.id)
                if st.session_state.get("active_deal_id") == deal.id:
                    st.session_state.pop("active_deal_id", None)
                st.rerun()
