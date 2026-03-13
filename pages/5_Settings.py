import streamlit as st
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json
from pathlib import Path
from config import BASE_DIR, SCORING_WEIGHTS, FEE_DEFAULTS, OBJECTIVE_MODE_LABELS
from ui_components import section_title

st.title("⚙️ Settings")
st.caption("Configure default fees, scoring weights, and application preferences.")

SETTINGS_PATH = BASE_DIR / "user_settings.json"


def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except Exception:
            pass
    return {}


def save_settings(s: dict) -> None:
    SETTINGS_PATH.write_text(json.dumps(s, indent=2))


settings = load_settings()

# ── Fee defaults ──────────────────────────────────────────────────────────────
section_title("Default fees", "These are pre-filled in the Scenario Builder for new scenarios.")

saved_fees = settings.get("fee_defaults", FEE_DEFAULTS)

with st.form("fee_form"):
    col1, col2 = st.columns(2)
    with col1:
        uw = st.number_input("Underwriting fee ($)", value=float(saved_fees.get("underwriting_fee", 1295.0)), min_value=0.0, step=50.0)
        proc = st.number_input("Processing fee ($)", value=float(saved_fees.get("processing_fee", 895.0)), min_value=0.0, step=50.0)
    with col2:
        appr = st.number_input("Appraisal fee ($)", value=float(saved_fees.get("appraisal_fee", 750.0)), min_value=0.0, step=50.0)
        title = st.number_input("Title / settlement ($)", value=float(saved_fees.get("title_fee", 1800.0)), min_value=0.0, step=50.0)
    if st.form_submit_button("💾 Save fee defaults"):
        settings["fee_defaults"] = {"underwriting_fee": uw, "processing_fee": proc, "appraisal_fee": appr, "title_fee": title}
        save_settings(settings)
        st.success("Fee defaults saved.")

# ── Scoring weight presets ────────────────────────────────────────────────────
section_title("Scoring weight presets", "Saved presets are available in the Comparison Dashboard.")

saved_weights = settings.get("scoring_weights", SCORING_WEIGHTS)

for mode_key, mode_label in [("balanced", "Balanced"), ("best_long_hold", "Best long hold")]:
    with st.expander(f"{mode_label} weights"):
        base = saved_weights.get(mode_key, SCORING_WEIGHTS[mode_key])
        with st.form(f"weight_form_{mode_key}"):
            c1, c2, c3 = st.columns(3)
            with c1:
                w_dscr = st.number_input("DSCR weight", value=float(base["dscr"]), step=1.0, key=f"dscr_{mode_key}")
                w_flex = st.number_input("Flexibility weight", value=float(base["flexibility"]), step=0.05, format="%.3f", key=f"flex_{mode_key}")
            with c2:
                w_pmt = st.number_input("Payment weight (neg)", value=float(base["payment"]), step=0.005, format="%.4f", key=f"pmt_{mode_key}")
                w_cash = st.number_input("Cash weight (neg)", value=float(base["cash"]), step=0.001, format="%.4f", key=f"cash_{mode_key}")
            with c3:
                w_hold = st.number_input("Hold-fit weight", value=float(base.get("hold_fit", 0.45)), step=0.05, format="%.3f", key=f"hold_{mode_key}")
                w_risk = st.number_input("Prepay risk weight (neg)", value=float(base.get("prepay_risk", -0.04)), step=0.005, format="%.4f", key=f"risk_{mode_key}")
            if st.form_submit_button(f"💾 Save {mode_label} weights"):
                if "scoring_weights" not in settings:
                    settings["scoring_weights"] = {}
                settings["scoring_weights"][mode_key] = {
                    "dscr": w_dscr, "flexibility": w_flex, "payment": w_pmt,
                    "cash": w_cash, "hold_fit": w_hold, "prepay_risk": w_risk,
                }
                save_settings(settings)
                st.success(f"{mode_label} weights saved.")

# ── About ─────────────────────────────────────────────────────────────────────
st.markdown("---")
section_title("About")
st.markdown("""
**DSCR Loan Option Engine v2.1**

Decision-support software for normalizing investor loan quotes and generating 
hold-period-aware comparisons. Not lending, legal, or financial advice.

- Source type legend: ✏️ Manual entry · 📥 Regex extraction · 🤖 AI extraction
- Scoring is a policy layer — adjust weights to reflect your priorities
- All estimates should be verified against final lender disclosures
""")
