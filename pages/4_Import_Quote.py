"""
pages/4_Import_Quote.py
Import Quote — refactored with classified intake, CSV upload, validation gate,
multi-scenario picker, and structured key:value paste path.
"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd

from db import get_deal, upsert_scenario
from models import LoanScenario
from config import FEE_DEFAULTS, PREPAY_TYPES
from ui_components import section_title

from services.input_classifier import classify
from services.manual_input_parser import parse as kv_parse
from services.single_quote_parser import parse as single_parse
from services.multi_quote_parser import extract_scenarios
from services.pdf_quote_parser import parse_pdf
from services.csv_import import (
    load_csv, generate_full_template_csv, generate_simple_template_csv,
    REQUIRED_COLUMNS,
)
from services.validation import validate_scenario

st.title("📥 Import Quote")
st.caption("Import a loan scenario from pasted text, PDF, CSV, or manual entry.")

active_id = st.session_state.get("active_deal_id")
if not active_id:
    st.warning("Load or create a deal first on the **Deal Intake** page.")
    st.stop()

deal = get_deal(active_id)
st.info(f"Importing into deal: **{deal.deal_name}**")

_KEYS = [
    "iq_extracted_fields", "iq_confidence", "iq_provenance", "iq_errors",
    "iq_source_type", "iq_source_text", "iq_input_type", "iq_scenario_rows",
    "iq_selected_row_idx", "iq_validation_override", "iq_csv_result",
]
for _k in _KEYS:
    if _k not in st.session_state:
        st.session_state[_k] = None


def _clear_extraction():
    for k in _KEYS:
        st.session_state[k] = None


def _safe_float(val, default=0.0):
    try:
        return float(val) if val is not None and val != "" else float(default)
    except Exception:
        return float(default)


def _safe_int(val, default=0):
    try:
        return int(float(val)) if val is not None and val != "" else int(default)
    except Exception:
        return int(default)


def _clamp(val, lo=None, hi=None):
    if lo is not None and val < lo:
        return lo
    if hi is not None and val > hi:
        return hi
    return val


def _conf_badge(key, conf):
    c = conf.get(key, "")
    return {"high": "✅", "medium": "🔶", "low": "⚠️"}.get(c, "")


def _run_validation(lender, program, rate, points, amort, term,
                    uw, proc, appraisal, title, credit, prepay_type, prepay_months,
                    source="unknown", n_defaulted=0):
    return validate_scenario(
        lender_name=lender, program_name=program,
        rate_percent=rate, points_percent=points,
        loan_amount=None,
        purchase_price=getattr(deal, "purchase_price", None),
        amortization_months=amort, loan_term_months=term,
        underwriting_fee=uw, processing_fee=proc,
        appraisal_fee=appraisal, title_fee=title,
        lender_credit=credit, prepay_type=prepay_type,
        prepay_months=prepay_months,
        import_source=source, num_defaulted_fields=n_defaulted,
    )


def _show_validation(vr):
    if vr.status == "pass":
        st.success("✅ Validation passed.")
        return False
    if vr.status == "warn":
        st.warning("🔶 Warnings — review before saving.")
        for w in vr.warnings:
            st.caption(f"• {w}")
        return False
    st.error("🚫 Cannot save — fix issues:")
    for b in vr.hard_blocks:
        st.caption(f"• {b}")
    if vr.warnings:
        st.warning("Additional warnings:")
        for w in vr.warnings:
            st.caption(f"• {w}")
    return True


def _do_save(active_id, **kwargs):
    scenario = LoanScenario(deal_id=active_id, **kwargs)
    sid = upsert_scenario(scenario)
    _clear_extraction()
    st.success(f"✅ Scenario #{sid} saved! View on the **Comparison Dashboard**.")
    st.rerun()


def _review_and_save_form(fields, confidence, source_type, source_text, provenance):
    from services.validation import count_defaulted_fields
    c = confidence or {}
    section_title("Review & Save")
    st.caption("Confidence: ✅ high  🔶 medium  ⚠️ low  — Edit any field before saving.")

    if not fields:
        st.warning("No fields extracted. Fill in manually below.")

    if source_text:
        with st.expander("Source text", expanded=False):
            st.text(source_text[:4000])

    if provenance:
        with st.expander("Extraction details", expanded=False):
            prows = [{"Field": p.canonical_key, "Label": p.source_label,
                      "Method": p.method, "Conf.": p.confidence,
                      "Raw": p.raw_value} for p in provenance]
            st.dataframe(pd.DataFrame(prows), use_container_width=True, hide_index=True)

    default_field_values = {
        "underwriting_fee": FEE_DEFAULTS["underwriting_fee"],
        "processing_fee": FEE_DEFAULTS["processing_fee"],
        "appraisal_fee": FEE_DEFAULTS["appraisal_fee"],
        "title_fee": FEE_DEFAULTS["title_fee"],
        "loan_term_months": 360, "amortization_months": 360, "prepay_months": 60,
    }
    n_def = count_defaulted_fields(fields or {}, default_field_values)

    raw_lender = (fields.get("lender_name") or "").strip()
    raw_program = fields.get("program_name", "DSCR 30yr")
    rate_val = _clamp(_safe_float(fields.get("rate_percent"), 7.25), 0.0, 25.0)
    raw_pts = _safe_float(fields.get("points_percent"), 0.0)
    pts_clamped = raw_pts < -5.0 or raw_pts > 10.0
    pts_val = _clamp(raw_pts, -5.0, 10.0)
    term_val = _clamp(_safe_int(fields.get("loan_term_months"), 360), 1, 600)
    amort_val = _clamp(_safe_int(fields.get("amortization_months"), 360), 1, 600)
    io_val = _clamp(_safe_int(fields.get("interest_only_months"), 0), 0, 120)
    prepay_m_val = _clamp(_safe_int(fields.get("prepay_months"), 60), 0, 120)
    uw_val = max(0.0, _safe_float(fields.get("underwriting_fee"), FEE_DEFAULTS["underwriting_fee"]))
    proc_val = max(0.0, _safe_float(fields.get("processing_fee"), FEE_DEFAULTS["processing_fee"]))
    appr_val = max(0.0, _safe_float(fields.get("appraisal_fee"), FEE_DEFAULTS["appraisal_fee"]))
    title_val = max(0.0, _safe_float(fields.get("title_fee"), FEE_DEFAULTS["title_fee"]))
    credit_val = max(0.0, _safe_float(fields.get("lender_credit"), 0.0))
    prepay_type_val = fields.get("prepay_type", "declining")
    if prepay_type_val not in PREPAY_TYPES:
        prepay_type_val = "declining"
    notes_val = fields.get("notes", f"Imported via {source_type}")

    if pts_clamped:
        st.warning("Extracted points were out of range and clamped. Verify before saving.")

    with st.form("review_save_form"):
        col1, col2 = st.columns(2)
        with col1:
            lender_name = st.text_input(f"Lender name * {_conf_badge('lender_name', c)}", value=raw_lender)
            program_name = st.text_input(f"Program name {_conf_badge('program_name', c)}", value=raw_program)
            rate_percent = st.number_input(f"Note rate (%) {_conf_badge('rate_percent', c)}",
                value=rate_val, min_value=0.0, max_value=25.0, step=0.125, format="%.3f")
            points_percent = st.number_input(f"Points (%) {_conf_badge('points_percent', c)}",
                value=pts_val, min_value=-5.0, max_value=10.0, step=0.125, format="%.3f")
            loan_term_months = st.number_input(f"Loan term (months) {_conf_badge('loan_term_months', c)}",
                value=term_val, min_value=1, max_value=600, step=12)
            amortization_months = st.number_input(f"Amortization (months) {_conf_badge('amortization_months', c)}",
                value=amort_val, min_value=1, max_value=600, step=12)
            io_months = st.number_input(f"Interest-only period (months) {_conf_badge('interest_only_months', c)}",
                value=io_val, min_value=0, max_value=120, step=12)
        with col2:
            prepay_type = st.selectbox(f"Prepay type {_conf_badge('prepay_type', c)}",
                PREPAY_TYPES, index=PREPAY_TYPES.index(prepay_type_val))
            prepay_months = st.number_input(f"Prepay window (months) {_conf_badge('prepay_months', c)}",
                value=prepay_m_val, min_value=0, max_value=120, step=6)
            uw_fee = st.number_input(f"Underwriting fee ($) {_conf_badge('underwriting_fee', c)}",
                value=uw_val, min_value=0.0, step=50.0)
            proc_fee = st.number_input(f"Processing fee ($) {_conf_badge('processing_fee', c)}",
                value=proc_val, min_value=0.0, step=50.0)
            appraisal = st.number_input(f"Appraisal fee ($) {_conf_badge('appraisal_fee', c)}",
                value=appr_val, min_value=0.0, step=50.0)
            title_fee = st.number_input(f"Title fee ($) {_conf_badge('title_fee', c)}",
                value=title_val, min_value=0.0, step=50.0)
            credit = st.number_input(f"Lender credit ($) {_conf_badge('lender_credit', c)}",
                value=credit_val, min_value=0.0, step=100.0)

        notes = st.text_area("Notes (optional)", value=notes_val, height=60)
        st.caption("⚠️ Always verify extracted values against the original lender document.")
        col_save, col_clear = st.columns([1, 4])
        with col_save:
            save_clicked = st.form_submit_button("✅ Save scenario", type="primary")
        with col_clear:
            clear_clicked = st.form_submit_button("🗑 Clear")

    if save_clicked:
        vr = _run_validation(lender=lender_name, program=program_name,
            rate=rate_percent, points=points_percent,
            amort=int(amortization_months), term=int(loan_term_months),
            uw=uw_fee, proc=proc_fee, appraisal=appraisal, title=title_fee,
            credit=credit, prepay_type=prepay_type, prepay_months=int(prepay_months),
            source=source_type, n_defaulted=n_def)
        blocked = _show_validation(vr)
        if not blocked:
            _do_save(active_id=active_id,
                lender_name=lender_name or "Imported quote",
                program_name=program_name, rate_percent=rate_percent,
                points_percent=points_percent, loan_term_months=int(loan_term_months),
                amortization_months=int(amortization_months),
                interest_only_months=int(io_months), prepay_type=prepay_type,
                prepay_months=int(prepay_months), underwriting_fee=uw_fee,
                processing_fee=proc_fee, appraisal_fee=appraisal,
                title_fee=title_fee, lender_credit=credit, notes=notes,
                source_type=source_type,
                source_text=(source_text or "")[:4000])

    if clear_clicked:
        _clear_extraction()
        st.rerun()


# ─── TABS ─────────────────────────────────────────────────────────────────────

tab_text, tab_pdf, tab_csv, tab_manual = st.tabs([
    "📋 Paste structured text",
    "📄 Upload PDF",
    "📊 Upload CSV",
    "✏️ Manual entry",
])

# ══ Tab 1 — Paste text ════════════════════════════════════════════════════════

with tab_text:
    section_title("Paste loan quote text")
    st.caption(
        "Paste a structured key:value block for best results. "
        "Multi-scenario summaries are automatically detected and routed to a picker."
    )

    EXAMPLE = (
        "Lender name: My Community Mortgage LLC\n"
        "Program name: 30 Year NON-QM Fixed\n"
        "Note rate (%): 6.500\n"
        "Points (%): 1.500\n"
        "Loan term (months): 360\n"
        "Amortization (months): 360\n"
        "Interest-only period (months): 0\n"
        "Prepay type: declining\n"
        "Prepay window (months): 60\n"
        "Underwriting fee ($): 1395\n"
        "Processing fee ($): 0\n"
        "Appraisal fee ($): 700\n"
        "Title fee ($): 1922.56\n"
        "Lender credit ($): 0"
    )

    raw_text = st.text_area("Paste quote text or email here", height=220,
                            placeholder=EXAMPLE, key="paste_text_input")
    use_ai = st.toggle("Use AI extraction (Anthropic API)", value=True, key="text_use_ai")

    if st.button("🔍 Classify and extract", type="primary",
                 disabled=not (raw_text or "").strip(), key="extract_text_btn"):
        classification = classify(raw_text)
        st.info(
            f"**Detected:** {classification.input_type.replace('_', ' ').title()}  "
            f"| Confidence: {classification.confidence}  \n_{classification.rationale}_"
        )

        itype = classification.input_type

        if itype == "manual_kv":
            result = kv_parse(raw_text)
            st.session_state.update({
                "iq_extracted_fields": result.fields,
                "iq_confidence": {p.canonical_key: p.confidence for p in result.field_provenance},
                "iq_provenance": result.field_provenance,
                "iq_errors": result.parse_errors,
                "iq_source_type": "manual_kv",
                "iq_source_text": raw_text,
                "iq_input_type": "manual_kv",
                "iq_scenario_rows": None,
            })

        elif itype == "multi_scenario":
            rows = extract_scenarios(raw_text)
            if rows:
                st.session_state.update({
                    "iq_scenario_rows": rows,
                    "iq_input_type": "multi_scenario",
                    "iq_source_text": raw_text,
                    "iq_extracted_fields": None,
                })
            else:
                st.warning("Multi-scenario signals detected but no rows could be parsed. "
                           "Try manual entry or paste a single-scenario quote.")

        else:
            if use_ai:
                from services.extraction import ai_extract
                ai_res = ai_extract(raw_text)
                if ai_res.get("fields"):
                    st.session_state.update({
                        "iq_extracted_fields": ai_res["fields"],
                        "iq_confidence": ai_res.get("confidence", {}),
                        "iq_provenance": [],
                        "iq_errors": [],
                        "iq_source_type": "ai_extracted",
                        "iq_source_text": ai_res.get("clean_text", raw_text),
                        "iq_input_type": "single_quote",
                        "iq_scenario_rows": None,
                    })
                else:
                    st.warning(f"AI returned no fields ({ai_res.get('error', '')}). Using parser.")
                    sq = single_parse(raw_text)
                    st.session_state.update({
                        "iq_extracted_fields": sq.fields,
                        "iq_confidence": sq.confidence,
                        "iq_provenance": sq.field_provenance,
                        "iq_errors": sq.parse_errors,
                        "iq_source_type": "extracted",
                        "iq_source_text": sq.clean_text,
                        "iq_input_type": "single_quote",
                        "iq_scenario_rows": None,
                    })
            else:
                sq = single_parse(raw_text)
                st.session_state.update({
                    "iq_extracted_fields": sq.fields,
                    "iq_confidence": sq.confidence,
                    "iq_provenance": sq.field_provenance,
                    "iq_errors": sq.parse_errors,
                    "iq_source_type": "extracted",
                    "iq_source_text": sq.clean_text,
                    "iq_input_type": "single_quote",
                    "iq_scenario_rows": None,
                })
        st.rerun()

    # Multi-scenario picker
    if st.session_state.get("iq_input_type") == "multi_scenario":
        rows = st.session_state.get("iq_scenario_rows") or []
        if rows:
            section_title("Multiple scenarios detected — pick one")
            st.info(f"Found **{len(rows)} options** in the pasted text. Select one to import.")
            df = pd.DataFrame([{
                "Label": r.label, "Down %": r.down_pct,
                "Loan Amount": f"${r.loan_amount:,.0f}" if r.loan_amount else "—",
                "Rate %": r.note_rate, "Points %": r.points_pct,
                "Confidence": r.confidence,
            } for r in rows])
            st.dataframe(df, use_container_width=True, hide_index=True)
            sel_idx = st.selectbox("Select scenario", range(len(rows)),
                                   format_func=lambda i: rows[i].label,
                                   key="multi_row_select")
            col_a, col_b = st.columns([1, 4])
            with col_a:
                if st.button("Use this scenario →", key="use_multi_row"):
                    chosen = rows[sel_idx]
                    partial: dict = {}
                    if chosen.note_rate is not None:
                        partial["rate_percent"] = chosen.note_rate
                    if chosen.points_pct is not None:
                        partial["points_percent"] = chosen.points_pct
                    src = st.session_state.get("iq_source_text", "")
                    sq = single_parse(src) if src else None
                    merged = {**(sq.fields if sq else {}), **partial}
                    st.session_state.update({
                        "iq_extracted_fields": merged,
                        "iq_confidence": sq.confidence if sq else {},
                        "iq_provenance": sq.field_provenance if sq else [],
                        "iq_errors": sq.parse_errors if sq else [],
                        "iq_input_type": "single_quote",
                        "iq_source_type": "extracted",
                        "iq_scenario_rows": None,
                    })
                    st.rerun()
            with col_b:
                if st.button("🗑 Clear", key="clear_multi"):
                    _clear_extraction()
                    st.rerun()

    # Review form for text tab
    if st.session_state.get("iq_input_type") in ("manual_kv", "single_quote") \
            and st.session_state.get("iq_extracted_fields") is not None \
            and st.session_state.get("iq_source_type") not in ("pdf_extracted", "csv"):
        errs = st.session_state.get("iq_errors") or []
        if errs:
            with st.expander(f"⚠️ {len(errs)} parse warning(s)", expanded=True):
                for e in errs:
                    st.caption(f"• {e}")
        _review_and_save_form(
            fields=st.session_state["iq_extracted_fields"],
            confidence=st.session_state.get("iq_confidence") or {},
            source_type=st.session_state.get("iq_source_type", "extracted"),
            source_text=st.session_state.get("iq_source_text", ""),
            provenance=st.session_state.get("iq_provenance") or [],
        )


# ══ Tab 2 — Upload PDF ════════════════════════════════════════════════════════

with tab_pdf:
    section_title("Upload quote PDF")
    st.caption("Upload a lender fee sheet, term sheet, or loan estimate. Text is extracted first.")

    uploaded_pdf = st.file_uploader("Choose a PDF", type=["pdf"], key="pdf_uploader")

    if uploaded_pdf:
        st.success(f"Loaded: **{uploaded_pdf.name}** ({uploaded_pdf.size / 1024:.1f} KB)")
        if st.button("🔍 Extract from PDF", type="primary", key="extract_pdf_btn"):
            with st.spinner("Reading PDF..."):
                pdf_result = parse_pdf(uploaded_pdf.read())
            if not pdf_result.success:
                st.error(pdf_result.error or "PDF extraction failed.")
            elif pdf_result.input_type == "multi_scenario":
                rows = pdf_result.scenario_rows
                st.info(f"Multiple scenarios found in PDF ({len(rows)} options).")
                st.session_state.update({
                    "iq_scenario_rows": rows,
                    "iq_input_type": "multi_scenario",
                    "iq_source_text": pdf_result.clean_text,
                    "iq_extracted_fields": None,
                    "iq_source_type": "pdf_extracted",
                })
                st.rerun()
            else:
                method = pdf_result.method
                if "single_quote" in (method or ""):
                    st.success("Extracted using PDF text-layer parser.")
                else:
                    st.warning("Fallback extraction used — review all fields carefully.")
                st.session_state.update({
                    "iq_extracted_fields": pdf_result.fields,
                    "iq_confidence": pdf_result.confidence,
                    "iq_provenance": pdf_result.field_provenance,
                    "iq_errors": pdf_result.parse_errors,
                    "iq_source_type": "pdf_extracted",
                    "iq_source_text": pdf_result.clean_text,
                    "iq_input_type": "single_quote",
                    "iq_scenario_rows": None,
                })
                st.rerun()

    if (st.session_state.get("iq_source_type") == "pdf_extracted"
            and st.session_state.get("iq_input_type") == "single_quote"
            and st.session_state.get("iq_extracted_fields") is not None):
        errs = st.session_state.get("iq_errors") or []
        if errs:
            with st.expander(f"⚠️ {len(errs)} parse warning(s)", expanded=True):
                for e in errs:
                    st.caption(f"• {e}")
        _review_and_save_form(
            fields=st.session_state["iq_extracted_fields"],
            confidence=st.session_state.get("iq_confidence") or {},
            source_type="pdf_extracted",
            source_text=st.session_state.get("iq_source_text", ""),
            provenance=st.session_state.get("iq_provenance") or [],
        )


# ══ Tab 3 — Upload CSV ════════════════════════════════════════════════════════

with tab_csv:
    section_title("Upload CSV")
    col_t1, col_t2, _ = st.columns([1, 1, 2])
    with col_t1:
        st.download_button("📥 Simple lender template",
            data=generate_simple_template_csv(),
            file_name="dscr_lender_template_simple.csv", mime="text/csv")
    with col_t2:
        st.download_button("📥 Full template",
            data=generate_full_template_csv(),
            file_name="dscr_full_template.csv", mime="text/csv")

    st.caption("Send the **simple lender template** to your lenders. Each row = one scenario.")

    with st.expander("CSV format guide", expanded=False):
        st.markdown(
            "- One row per loan option — do **not** combine multiple options in one row\n"
            "- Plain numbers only (`7.25` not `7.25%`), though importer strips `$` and `%`\n"
            "- Leave cells blank if a value doesn't apply\n"
            f"- **Required columns:** `{', '.join(sorted(REQUIRED_COLUMNS))}`\n"
            "- `note_rate_percent`: percentage as number (e.g. `7.25`)\n"
            "- `loan_term_months` / `amortization_months`: in months (e.g. `360`)\n"
            "- `prepay_type`: `declining`, `flat`, or `none`"
        )

    uploaded_csv = st.file_uploader("Upload completed CSV", type=["csv"], key="csv_uploader")
    if uploaded_csv:
        if st.button("📊 Preview and validate", type="primary", key="validate_csv_btn"):
            with st.spinner("Validating..."):
                csv_result = load_csv(uploaded_csv.read())
            st.session_state["iq_csv_result"] = csv_result
            st.rerun()

    csv_result = st.session_state.get("iq_csv_result")
    if csv_result:
        if csv_result.header_errors:
            for e in csv_result.header_errors:
                st.error(e)
        else:
            st.success(f"**{csv_result.valid_rows}** of {csv_result.total_rows} rows valid.")
            preview = []
            for r in csv_result.rows:
                preview.append({
                    "Row": r.row_index + 1,
                    "Status": "✅ Valid" if r.valid else "🚫 Error",
                    "Lender": r.raw.get("lender_name", ""),
                    "Program": r.raw.get("program_name", ""),
                    "Rate %": r.raw.get("note_rate_percent", ""),
                    "Points %": r.raw.get("points_percent", ""),
                    "Issues": "; ".join(r.errors + r.warnings) or "—",
                })
            st.dataframe(pd.DataFrame(preview), use_container_width=True, hide_index=True)

            valid_idxs = [r.row_index for r in csv_result.rows if r.valid]
            if valid_idxs:
                selected = st.multiselect(
                    "Select rows to import", options=valid_idxs, default=valid_idxs,
                    format_func=lambda i: (
                        f"Row {i+1} — "
                        f"{csv_result.rows[i].raw.get('lender_name', '?')} / "
                        f"{csv_result.rows[i].raw.get('program_name', '?')}"
                    ),
                )
                if st.button(f"✅ Import {len(selected)} row(s)", type="primary",
                             disabled=not selected, key="import_csv_btn"):
                    saved = 0
                    for idx in selected:
                        row = csv_result.rows[idx]
                        if not row.valid or row.scenario is None:
                            continue
                        sc = row.scenario.model_copy(update={"deal_id": active_id})
                        try:
                            upsert_scenario(sc)
                            saved += 1
                        except Exception as exc:
                            st.error(f"Row {idx+1}: {exc}")
                    if saved:
                        st.success(f"✅ Imported {saved} scenario(s).")
                        st.session_state["iq_csv_result"] = None
            else:
                st.warning("No valid rows to import.")

            if st.button("🗑 Clear", key="clear_csv_btn"):
                st.session_state["iq_csv_result"] = None
                st.rerun()


# ══ Tab 4 — Manual entry ══════════════════════════════════════════════════════

with tab_manual:
    section_title("Manual scenario entry")
    st.caption("The safest intake path — all values entered directly, no extraction involved.")

    with st.form("manual_entry_form"):
        col1, col2 = st.columns(2)
        with col1:
            m_lender = st.text_input("Lender name *", placeholder="First National DSCR")
            m_program = st.text_input("Program name", value="DSCR 30yr Fixed")
            m_rate = st.number_input("Note rate (%)", value=7.25, min_value=0.0,
                max_value=25.0, step=0.125, format="%.3f")
            m_points = st.number_input("Points (%)", value=0.0, min_value=-5.0,
                max_value=10.0, step=0.125, format="%.3f")
            m_term = st.number_input("Loan term (months)", value=360, min_value=1, max_value=600, step=12)
            m_amort = st.number_input("Amortization (months)", value=360, min_value=1, max_value=600, step=12)
            m_io = st.number_input("Interest-only period (months)", value=0, min_value=0, max_value=120, step=12)
        with col2:
            m_prepay_type = st.selectbox("Prepay type", PREPAY_TYPES)
            m_prepay_months = st.number_input("Prepay window (months)", value=60, min_value=0, max_value=120, step=6)
            m_uw = st.number_input("Underwriting fee ($)", value=FEE_DEFAULTS["underwriting_fee"], min_value=0.0, step=50.0)
            m_proc = st.number_input("Processing fee ($)", value=FEE_DEFAULTS["processing_fee"], min_value=0.0, step=50.0)
            m_appraisal = st.number_input("Appraisal fee ($)", value=FEE_DEFAULTS["appraisal_fee"], min_value=0.0, step=50.0)
            m_title = st.number_input("Title fee ($)", value=FEE_DEFAULTS["title_fee"], min_value=0.0, step=50.0)
            m_credit = st.number_input("Lender credit ($)", value=0.0, min_value=0.0, step=100.0)
        m_notes = st.text_area("Notes (optional)", height=60)
        manual_save = st.form_submit_button("✅ Save scenario", type="primary")

    if manual_save:
        vr = _run_validation(lender=m_lender, program=m_program,
            rate=m_rate, points=m_points,
            amort=int(m_amort), term=int(m_term),
            uw=m_uw, proc=m_proc, appraisal=m_appraisal, title=m_title,
            credit=m_credit, prepay_type=m_prepay_type, prepay_months=int(m_prepay_months),
            source="manual", n_defaulted=0)
        blocked = _show_validation(vr)
        if not blocked:
            _do_save(active_id=active_id,
                lender_name=m_lender or "Manual entry",
                program_name=m_program, rate_percent=m_rate, points_percent=m_points,
                loan_term_months=int(m_term), amortization_months=int(m_amort),
                interest_only_months=int(m_io), prepay_type=m_prepay_type,
                prepay_months=int(m_prepay_months), underwriting_fee=m_uw,
                processing_fee=m_proc, appraisal_fee=m_appraisal,
                title_fee=m_title, lender_credit=m_credit,
                notes=m_notes or "Manual entry", source_type="manual", source_text="")
