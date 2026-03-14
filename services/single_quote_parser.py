"""
services/single_quote_parser.py
Parse a single-scenario free-text loan quote.

Uses a layered approach:
1. Structured key:value pass (via manual_input_parser)
2. Labeled regex extraction for common patterns
3. Returns field provenance for every captured field
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional

from services.manual_input_parser import parse as kv_parse, FieldProvenance


@dataclass
class SingleQuoteResult:
    fields: dict
    field_provenance: list[FieldProvenance]
    parse_errors: list[str]
    confidence: dict          # field → "high" | "medium" | "low"
    clean_text: str


# ── Regex fallback patterns ──────────────────────────────────────────────────

_PATTERNS: list[tuple[str, str, str]] = [
    # (canonical_key, pattern, value_group)
    ("rate_percent",         r"(?:note\s*rate|interest\s*rate|rate)\s*[:\-]?\s*([0-9]{1,2}(?:\.[0-9]{1,4})?)\s*%", "1"),
    ("points_percent",       r"(?:origination\s*points?|points?)\s*[:\-]?\s*([\-]?[0-9]{1,2}(?:\.[0-9]{1,3})?)\s*%?", "1"),
    ("loan_term_months",     r"(?:loan\s*term|term)\s*[:\-]?\s*([0-9]{1,3})\s*(months?|years?|yrs?)", "1,2"),
    ("amortization_months",  r"(?:amortization|amortized\s*over|amort)\s*[:\-]?\s*([0-9]{1,3})\s*(months?|years?|yrs?)", "1,2"),
    ("interest_only_months", r"(?:interest[\s-]*only|i/o|io\s*period)\s*[:\-]?\s*([0-9]{1,3})\s*(months?|years?|yrs?)", "1,2"),
    ("prepay_months",        r"(?:prepay(?:ment)?\s*(?:penalty|window|period)?|ppp)\s*[:\-]?\s*([0-9]{1,3})\s*(months?|years?|yrs?)", "1,2"),
    ("underwriting_fee",     r"(?:underwriting\s*fee?|uw\s*fee)\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", "1"),
    ("processing_fee",       r"(?:processing\s*fee?|proc\s*fee)\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", "1"),
    ("appraisal_fee",        r"(?:appraisal\s*fee?)\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", "1"),
    ("title_fee",            r"(?:title\s*fee?|settlement\s*fee)\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", "1"),
    ("lender_credit",        r"(?:lender\s*credit|credit\s*to\s*borrower)\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", "1"),
    ("lender_name",          r"(?:lender(?:\s*name)?|bank)\s*[:\-]?\s*([A-Za-z0-9 &.,'\\-]{2,60}?)(?:\n|$)", "1"),
    ("program_name",         r"(?:program(?:\s*name)?|product|loan\s*type)\s*[:\-]?\s*([A-Za-z0-9 /\\-]{2,80}?)(?:\n|$|,)", "1"),
]


def _to_months(val: str, unit: str) -> int:
    v = int(val)
    if re.match(r"year|yr", unit, re.I):
        return v * 12
    return v


def _parse_dollar(s: str) -> float:
    return float(s.replace(",", "").replace("$", "").strip())


def parse(text: str) -> SingleQuoteResult:
    clean = text.strip()

    # 1. Key:value pass first
    kv_result = kv_parse(clean)
    fields = dict(kv_result.fields)
    provenance = list(kv_result.field_provenance)
    errors = list(kv_result.parse_errors)
    confidence: dict = {p.canonical_key: p.confidence for p in provenance}

    # 2. Regex fallback for any fields not captured by kv pass
    for entry in _PATTERNS:
        canonical = entry[0]
        pattern = entry[1]
        groups = entry[2]

        if canonical in fields:
            continue  # already have it

        try:
            m = re.search(pattern, clean, re.I | re.MULTILINE)
            if not m:
                continue

            if canonical in ("loan_term_months", "amortization_months",
                             "interest_only_months", "prepay_months"):
                raw_val = m.group(1)
                raw_unit = m.group(2) if m.lastindex >= 2 else "months"
                val = _to_months(raw_val, raw_unit)
                fields[canonical] = val
                confidence[canonical] = "medium"
            elif canonical in ("rate_percent", "points_percent"):
                fields[canonical] = float(m.group(1))
                confidence[canonical] = "medium"
            elif canonical in ("underwriting_fee", "processing_fee", "appraisal_fee",
                               "title_fee", "lender_credit"):
                fields[canonical] = _parse_dollar(m.group(1))
                confidence[canonical] = "medium"
            elif canonical in ("lender_name", "program_name"):
                val_str = m.group(1).strip().rstrip(",")
                if len(val_str.split()) <= 12:
                    fields[canonical] = val_str
                    confidence[canonical] = "low"
            else:
                fields[canonical] = m.group(1).strip()
                confidence[canonical] = "low"

            provenance.append(FieldProvenance(
                canonical_key=canonical,
                source_label=canonical.replace("_", " ").title(),
                raw_value=m.group(1),
                method="regex_fallback",
                confidence=confidence[canonical],
            ))
        except Exception:
            pass

    # 3. Prepay type from text signals
    if "prepay_type" not in fields:
        lowered = clean.lower()
        if re.search(r"no[\s-]*prepay|prepayment\s*penalty\s*[:\-]?\s*no\b", lowered):
            fields["prepay_type"] = "none"
            confidence["prepay_type"] = "high"
        elif re.search(r"declin", lowered):
            fields["prepay_type"] = "declining"
            confidence["prepay_type"] = "medium"
        elif re.search(r"\bflat\b.*prepay|prepay.*\bflat\b", lowered):
            fields["prepay_type"] = "flat"
            confidence["prepay_type"] = "medium"

    return SingleQuoteResult(
        fields=fields,
        field_provenance=provenance,
        parse_errors=errors,
        confidence=confidence,
        clean_text=clean,
    )
