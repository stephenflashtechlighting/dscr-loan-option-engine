"""
services/pdf_quote_parser.py
PDF-specific entry point.

Pipeline:
1. Extract readable text from PDF (pdfminer)
2. Normalize / clean text
3. Classify extracted text (single vs multi vs unknown)
4. Route to appropriate parser
5. Apply field provenance and validation gate

Does NOT do raw-byte guessing. If text extraction fails, returns an error
result without attempting low-quality fallback parsing.
"""
from __future__ import annotations
import io
import re
from dataclasses import dataclass
from typing import Optional

from services.input_classifier import classify, ClassifierResult
from services.single_quote_parser import parse as single_parse, SingleQuoteResult
from services.multi_quote_parser import extract_scenarios, ScenarioRow
from services.manual_input_parser import FieldProvenance


@dataclass
class PdfParseResult:
    success: bool
    input_type: str                      # single_quote | multi_scenario | unknown
    fields: dict                          # populated for single_quote
    confidence: dict
    field_provenance: list[FieldProvenance]
    parse_errors: list[str]
    scenario_rows: list[ScenarioRow]     # populated for multi_scenario
    clean_text: str
    method: str
    error: Optional[str] = None


_LOAN_KEYWORDS = [
    "rate", "interest only", "loan term", "amortization", "prepay",
    "underwriting", "processing fee", "appraisal", "title", "lender credit",
    "dscr", "term sheet", "quote", "loan estimate", "closing cost",
    "origination charges", "cash to close",
]


def _is_loan_document(text: str) -> bool:
    lowered = text.lower()
    hits = sum(1 for kw in _LOAN_KEYWORDS if kw in lowered)
    return hits >= 3


def _normalize(text: str) -> str:
    """Basic normalization of PDF-extracted text."""
    text = text.replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Merge split label/value pairs common in PDFs
    lines = [ln.strip() for ln in text.splitlines()]
    merged: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line:
            i += 1
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if nxt and len(line) <= 50 and len(nxt) <= 50:
            label_like = bool(re.search(
                r"(rate|term|amort|prepay|points|origination|underwriting|"
                r"processing|appraisal|title|credit|lender|loan type|interest only)",
                line, re.I))
            value_like = bool(re.fullmatch(
                r"(?:\$?[\d,]+(?:\.\d+)?%?|YES|NO|Conventional|DSCR|Fixed|ARM|\d+\s*(?:years?|months?))",
                nxt, re.I))
            if label_like and value_like:
                merged.append(f"{line}: {nxt}")
                i += 2
                continue
        merged.append(line)
        i += 1
    return "\n".join(merged).strip()


def parse_pdf(pdf_bytes: bytes) -> PdfParseResult:
    """Main entry point — parse a PDF and return a structured result."""

    # ── Step 1: Extract text ─────────────────────────────────────────────────
    try:
        from pdfminer.high_level import extract_text
        raw_text = extract_text(io.BytesIO(pdf_bytes))
    except Exception as exc:
        return PdfParseResult(
            success=False,
            input_type="unknown",
            fields={}, confidence={}, field_provenance=[],
            parse_errors=[], scenario_rows=[],
            clean_text="",
            method="failed",
            error=f"PDF text extraction failed: {exc}",
        )

    # ── Step 2: Normalize ────────────────────────────────────────────────────
    clean_text = _normalize(raw_text or "")

    if not clean_text or len(clean_text) < 40:
        return PdfParseResult(
            success=False,
            input_type="unknown",
            fields={}, confidence={}, field_provenance=[],
            parse_errors=[], scenario_rows=[],
            clean_text=clean_text,
            method="failed",
            error="PDF did not contain enough readable text. Try pasting the text directly.",
        )

    if not _is_loan_document(clean_text):
        return PdfParseResult(
            success=False,
            input_type="unknown",
            fields={}, confidence={}, field_provenance=[],
            parse_errors=[], scenario_rows=[],
            clean_text=clean_text,
            method="failed",
            error=(
                "This PDF does not appear to be a loan quote or fee sheet. "
                "Upload a lender fee sheet, term sheet, or loan estimate."
            ),
        )

    # ── Step 3: Classify extracted text ──────────────────────────────────────
    classification: ClassifierResult = classify(clean_text)
    input_type = classification.input_type

    # ── Step 4: Route ────────────────────────────────────────────────────────
    if input_type == "multi_scenario":
        scenario_rows = extract_scenarios(clean_text)
        if scenario_rows:
            return PdfParseResult(
                success=True,
                input_type="multi_scenario",
                fields={}, confidence={}, field_provenance=[],
                parse_errors=[],
                scenario_rows=scenario_rows,
                clean_text=clean_text,
                method="pdf_text+multi_scenario_picker",
            )
        # Fall through to single parse if multi extraction yielded nothing

    # Default: treat as single quote
    sq_result: SingleQuoteResult = single_parse(clean_text)

    # ── Step 5: Sanity check — reject obvious mislabels ─────────────────────
    cleaned_fields = _reject_mislabels(sq_result.fields, sq_result.confidence)
    errors = list(sq_result.parse_errors)

    return PdfParseResult(
        success=True,
        input_type="single_quote",
        fields=cleaned_fields,
        confidence=sq_result.confidence,
        field_provenance=sq_result.field_provenance,
        parse_errors=errors,
        scenario_rows=[],
        clean_text=clean_text,
        method="pdf_text+single_quote_parser",
    )


def _reject_mislabels(fields: dict, confidence: dict) -> dict:
    """
    Remove fields that are obviously wrong.
    Rules:
    - lender_name must not look like a fee description
    - lender_credit must not be >= typical down payment (> 50000)
    - title_fee must not be >= typical loan amount (> 100000)
    - note_rate must be between 0 and 20
    """
    out = dict(fields)

    # Lender name sanity
    lender = (out.get("lender_name") or "").lower()
    bad_lender_patterns = re.compile(
        r"(fee|charge|credit|amount|total|closing|title|escrow|service)", re.I
    )
    if len(lender) < 3 or bad_lender_patterns.search(lender):
        out.pop("lender_name", None)

    # Rate sanity
    rate = out.get("rate_percent")
    if rate is not None and not (0.0 < float(rate) <= 20.0):
        out.pop("rate_percent", None)

    # Lender credit should not exceed $50k for typical DSCR
    lc = out.get("lender_credit")
    if lc is not None and float(lc) > 50_000:
        out.pop("lender_credit", None)

    # Title fee should not be a loan amount
    tf = out.get("title_fee")
    if tf is not None and float(tf) > 20_000:
        out.pop("title_fee", None)

    return out
