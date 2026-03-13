import streamlit as st
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_deal, upsert_scenario
from models import LoanScenario
from services.extraction import regex_extract, ai_extract, extract_from_pdf
from config import FEE_DEFAULTS, PREPAY_TYPES
from ui_components import section_title

st.title("📥 Import Quote")
st.caption("Import a loan quote from pasted text or a PDF file.")

active_id = st.session_state.get("active_deal_id")
if not active_id:
    st.warning("Load or create a deal first on the **Deal Intake** page.")
    st.stop()

deal = get_deal(active_id)
st.info(f"Importing into deal: **{deal.deal_name}**")

tab1, tab2 = st.tabs(["📋 Paste text / email", "📄 Upload PDF"])

if "extraction_result" not in st.session_state:
    st.session_state["extraction_result"] = None
if "source_text" not in st.session_state:
    st.session_state["source_text"] = ""
if "extraction_source_type" not in st.session_state:
    st.session_state["extraction_source_type"] = "manual"

with tab1:
    section_title("Paste quote text")
    raw_text = st.text_area(
        "Paste the loan quote, email, or text message here",
        height=220,
        placeholder="Example:\nLender: First National DSCR\nRate: 7.25%\n2 points\n5-year declining prepayment penalty\nLoan term: 360 months\nAmortization: 360 months\nUnderwriting fee: $1,495\nProcessing: $895\nNo lender credit",
        key="paste_text",
    )
    extraction_mode = st.radio(
        "Extraction method",
        ["AI extraction (Anthropic API)", "Regex only (faster, no API call)"],
        horizontal=True,
        key="text_mode",
    )
    use_ai = extraction_mode.startswith("AI")

    if st.button("🔍 Extract from text", type="primary", disabled=not (raw_text or "").strip(), key="extract_text_btn"):
        with st.spinner("Extracting..."):
            if use_ai:
                result = ai_extract(raw_text)
                source_type = "ai_extracted"
            else:
                result = regex_extract(raw_text)
                source_type = "extracted"
            if "error" in result and not result.get("fields"):
                st.error(f"AI extraction error: {result['error']} — falling back to regex.")
                result = regex_extract(raw_text)
                source_type = "extracted"
        st.session_state["extraction_result"] = result
        st.session_state["extraction_source_type"] = source_type
        st.session_state["source_text"] = result.get("clean_text") or raw_text
        st.rerun()

with tab2:
    section_title("Upload quote PDF")
    st.caption("Upload a lender fee sheet, term sheet, or quote PDF. The app now extracts text first and refuses unsafe raw-byte parsing.")
    uploaded_file = st.file_uploader(
        "Choose a PDF file",
        type=["pdf"],
        key="pdf_uploader",
        help="Lender fee sheets, term sheets, loan estimates, or quote documents in PDF format."
    )

    if uploaded_file is not None:
        st.success(f"File loaded: **{uploaded_file.name}** ({uploaded_file.size / 1024:.1f} KB)")
        if st.button("🔍 Extract from PDF", type="primary", key="extract_pdf_btn"):
            with st.spinner("Reading PDF and extracting loan fields..."):
                pdf_bytes = uploaded_file.read()
                result = extract_from_pdf(pdf_bytes)
                source_type = "ai_extracted" if result.get("method") == "pdf_text+ai" else "extracted"
                if "error" in result and not result.get("fields"):
                    st.error(result["error"])
                else:
                    method = result.get("method", "")
                    if method == "pdf_text+targeted":
                        st.success("Extracted from readable PDF text using the targeted loan-document parser.")
                    elif method == "pdf_text+ai":
                        st.success("Extracted from readable PDF text using AI.")
                    elif method == "pdf_text+regex":
                        st.warning("Extracted from readable PDF text using regex only — verify every field before saving.")
                    st.session_state["extraction_result"] = result
                    st.session_state["extraction_source_type"] = source_type
                    st.session_state["source_text"] = result.get("clean_text") or f"[PDF: {uploaded_file.name}]"
                    st.rerun()

result = st.session_state.get("extraction_result")
if result is not None:
    fields = result.get("fields", {})
    confidence = result.get("confidence", {})
    source_type = st.session_state.get("extraction_source_type", "extracted")
    method = result.get("method", "")

    source_label = "🤖 AI" if source_type == "ai_extracted" else "🔤 Regex"
    if "pdf" in method.lower():
        source_label += " (PDF)"

    section_title("Review extracted fields")
    st.caption(f"Source: {source_label}. Edit any field before saving.")

    if not fields:
        st.warning("No fields were automatically extracted. Fill in the form below manually.")

    preview_text = st.session_state.get("source_text", "")
    if preview_text:
        with st.expander("Show extracted source text", expanded=False):
            st.text(preview_text[:4000])

    with st.form("review_form"):
        col1, col2 = st.columns(2)
        with col1:
            lender_name = st.text_input("Lender name *", value=fields.get("lender_name", ""), help=f"Confidence: {confidence.get('lender_name', 'low')}")
            program_name = st.text_input("Program name", value=fields.get("program_name", "DSCR 30yr"))
            rate_percent = st.number_input("Note rate (%)", value=float(fields.get("rate_percent", 7.25)), min_value=0.0, max_value=25.0, step=0.125, format="%.3f", help=f"Confidence: {confidence.get('rate_percent', 'low')}")
            points_percent = st.number_input("Points (%)", value=float(fields.get("points_percent", 0.0)), min_value=-5.0, max_value=10.0, step=0.125, format="%.3f", help=f"Confidence: {confidence.get('points_percent', 'low')}")
            loan_term_months = st.number_input("Loan term (months)", value=int(fields.get("loan_term_months", 360)), min_value=1, max_value=600, step=12)
            amortization_months = st.number_input("Amortization (months)", value=int(fields.get("amortization_months", 360)), min_value=1, max_value=600, step=12)
            io_months = st.number_input("Interest-only period (months)", value=int(fields.get("interest_only_months", 0)), min_value=0, max_value=120, step=12)

        with col2:
            prepay_type_val = fields.get("prepay_type", "declining")
            if prepay_type_val not in PREPAY_TYPES:
                prepay_type_val = "declining"
            prepay_type = st.selectbox("Prepay type", PREPAY_TYPES, index=PREPAY_TYPES.index(prepay_type_val), help=f"Confidence: {confidence.get('prepay_type', 'low')}")
            prepay_months = st.number_input("Prepay window (months)", value=int(fields.get("prepay_months", 60)), min_value=0, max_value=120, step=6, help=f"Confidence: {confidence.get('prepay_months', 'low')}")
            uw_fee = st.number_input("Underwriting fee ($)", value=float(fields.get("underwriting_fee", FEE_DEFAULTS["underwriting_fee"])), min_value=0.0, step=50.0)
            proc_fee = st.number_input("Processing fee ($)", value=float(fields.get("processing_fee", FEE_DEFAULTS["processing_fee"])), min_value=0.0, step=50.0)
            appraisal = st.number_input("Appraisal fee ($)", value=float(fields.get("appraisal_fee", FEE_DEFAULTS["appraisal_fee"])), min_value=0.0, step=50.0)
            title_fee = st.number_input("Title fee ($)", value=float(fields.get("title_fee", FEE_DEFAULTS["title_fee"])), min_value=0.0, step=50.0)
            credit = st.number_input("Lender credit ($)", value=float(fields.get("lender_credit", 0.0)), min_value=0.0, step=100.0)

        notes = st.text_area("Notes (optional)", height=60, value=fields.get("notes", f"Imported via {source_label}"))
        st.caption("⚠️ Always verify extracted values against the original lender document before using for decisions.")
        col_save, col_clear = st.columns([1, 4])
        with col_save:
            save = st.form_submit_button("✅ Save as scenario", type="primary")
        with col_clear:
            clear = st.form_submit_button("🗑 Clear and start over")

    if save:
        if not lender_name:
            lender_name = "Imported quote"
        scenario = LoanScenario(
                deal_id=active_id,
                lender_name=lender_name,
                program_name=program_name,
                rate_percent=rate_percent,
                points_percent=points_percent,
                loan_term_months=int(loan_term_months),
                amortization_months=int(amortization_months),
                interest_only_months=int(io_months),
                prepay_type=prepay_type,
                prepay_months=int(prepay_months),
                underwriting_fee=uw_fee,
                processing_fee=proc_fee,
                appraisal_fee=appraisal,
                title_fee=title_fee,
                lender_credit=credit,
                notes=notes,
                source_type=source_type,
                source_text=st.session_state.get("source_text", "")[:4000],
            )
        sid = upsert_scenario(scenario)
        st.session_state["extraction_result"] = None
        st.session_state["source_text"] = ""
        st.success(f"Scenario #{sid} saved! Go to the **Comparison Dashboard** to compare.")

    if clear:
        st.session_state["extraction_result"] = None
        st.session_state["source_text"] = ""
        st.rerun()
