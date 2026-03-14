"""
services/manual_input_parser.py
Strict key:value parser for user-pasted structured loan data.

Design principles:
- Each line is treated as  Label[optional (unit)]: value
- Parenthetical suffixes like ($), (%), (months) are stripped before lookup
- Synonyms are mapped to canonical field names
- If a label is found but value fails to parse, a parse_error is recorded —
  we never silently fall back to a default for a present-but-broken field
- Returns a ParseResult with fields, field_provenance, and parse_errors
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional


# ── Canonical → list of accepted label variants ──────────────────────────────

LABEL_MAP: dict[str, list[str]] = {
    "lender_name": [
        "lender name", "lender", "bank", "credit union",
    ],
    "program_name": [
        "program name", "program", "product name", "product",
        "loan program", "loan type", "loan product",
    ],
    "rate_percent": [
        "note rate", "interest rate", "rate", "coupon rate",
    ],
    "points_percent": [
        "points", "origination points", "origination",
        "discount points", "origination fee",
    ],
    "loan_term_months": [
        "loan term", "term", "note term",
    ],
    "amortization_months": [
        "amortization", "amortized over", "amort",
    ],
    "interest_only_months": [
        "interest only period", "interest-only period",
        "interest only", "i/o period", "io period",
    ],
    "prepay_type": [
        "prepay type", "prepayment type", "prepayment penalty type",
        "ppp type",
    ],
    "prepay_months": [
        "prepay window", "prepay period", "prepayment window",
        "prepayment period", "prepay", "ppp",
    ],
    "underwriting_fee": [
        "underwriting fee", "underwriting",
        "uw fee",
    ],
    "processing_fee": [
        "processing fee", "processing", "proc fee",
        "administrative fee", "admin fee",
    ],
    "origination_fee": [
        "origination fee",
    ],
    "appraisal_fee": [
        "appraisal fee", "appraisal", "appraisal cost",
    ],
    "title_fee": [
        "title fee", "title", "title charges", "title service",
        "title and escrow", "settlement fee", "closing fee",
        "escrow fee",
    ],
    "lender_credit": [
        "lender credit", "credit to borrower", "lender rebate",
        "credit",
    ],
    "notes": [
        "notes", "note", "comments", "memo",
    ],
}

# Pre-build a fast lookup: cleaned_label → canonical_key
_LABEL_LOOKUP: dict[str, str] = {}
for _canonical, _variants in LABEL_MAP.items():
    for _v in _variants:
        _LABEL_LOOKUP[_v.lower().strip()] = _canonical


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class FieldProvenance:
    canonical_key: str
    source_label: str          # label as it appeared in input
    raw_value: str             # value string before conversion
    method: str                # "kv_parse"
    confidence: str            # high / medium / low


@dataclass
class ParseResult:
    fields: dict                          # canonical key → typed value
    field_provenance: list[FieldProvenance]
    parse_errors: list[str]               # human-readable errors for flagged lines
    unparsed_lines: list[str]             # lines that had no matching label


# ── Helpers ───────────────────────────────────────────────────────────────────

_PAREN_SUFFIX_RE = re.compile(r"\s*\([^)]{1,30}\)\s*$")
_LINE_RE = re.compile(r"^(.+?)\s*:\s*(.+)$")
_JUNK_UNICODE_RE = re.compile(r"[\u200b\u00a0\ufeff\u2019\u2018\u201c\u201d]+")


def _clean_label(raw: str) -> str:
    """Strip parenthetical suffixes, whitespace, and junk unicode from a label."""
    s = _JUNK_UNICODE_RE.sub(" ", raw)
    s = _PAREN_SUFFIX_RE.sub("", s)
    return s.strip().lower()


def _to_float(s: str) -> Optional[float]:
    s = re.sub(r"[$,%]", "", s).strip()
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _to_months(s: str) -> Optional[int]:
    """Parse '360', '30 years', '360 months' → int months."""
    s = s.strip()
    m = re.fullmatch(r"(\d+)\s*(years?|yrs?|months?|mos?)?", s, re.I)
    if not m:
        return None
    val = int(m.group(1))
    unit = (m.group(2) or "months").lower()
    if re.match(r"year|yr", unit):
        return val * 12
    return val


def _parse_prepay_type(s: str) -> Optional[str]:
    s = s.strip().lower()
    if re.search(r"declin", s):
        return "declining"
    if re.search(r"\bflat\b", s):
        return "flat"
    if re.search(r"\bnone\b|\bno\b|n/a|0", s):
        return "none"
    return None


# ── Field parsers by canonical key ───────────────────────────────────────────

def _parse_value(canonical_key: str, raw: str):
    """
    Convert raw value string to the appropriate Python type for the canonical key.
    Returns (typed_value, confidence) or raises ValueError on failure.
    """
    raw = raw.strip()

    str_fields = {"lender_name", "program_name", "notes"}
    if canonical_key in str_fields:
        if not raw:
            raise ValueError("empty string field")
        return raw, "high"

    if canonical_key == "prepay_type":
        parsed = _parse_prepay_type(raw)
        if parsed is None:
            raise ValueError(f"unrecognized prepay type: {raw!r}")
        return parsed, "high"

    month_fields = {
        "loan_term_months", "amortization_months",
        "interest_only_months", "prepay_months",
    }
    if canonical_key in month_fields:
        val = _to_months(raw)
        if val is None:
            raise ValueError(f"could not convert to months: {raw!r}")
        return val, "high"

    # All remaining fields are floats/percents
    val = _to_float(raw)
    if val is None:
        raise ValueError(f"could not convert to number: {raw!r}")
    return val, "high"


# ── Main parser ───────────────────────────────────────────────────────────────

def parse(text: str) -> ParseResult:
    """
    Parse a structured key:value block.
    Returns ParseResult with typed fields, provenance, and any errors.
    """
    # Normalise unicode and line endings
    text = _JUNK_UNICODE_RE.sub(" ", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    fields: dict = {}
    provenance: list[FieldProvenance] = []
    errors: list[str] = []
    unparsed: list[str] = []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        m = _LINE_RE.match(line)
        if not m:
            unparsed.append(line)
            continue

        raw_label = m.group(1)
        raw_value = m.group(2).strip()
        cleaned_label = _clean_label(raw_label)

        canonical = _LABEL_LOOKUP.get(cleaned_label)
        if canonical is None:
            unparsed.append(line)
            continue

        try:
            typed_val, conf = _parse_value(canonical, raw_value)
        except ValueError as exc:
            errors.append(
                f"Label '{raw_label}' was recognized but value could not be parsed: {exc}"
            )
            continue

        fields[canonical] = typed_val
        provenance.append(FieldProvenance(
            canonical_key=canonical,
            source_label=raw_label.strip(),
            raw_value=raw_value,
            method="kv_parse",
            confidence=conf,
        ))

    return ParseResult(
        fields=fields,
        field_provenance=provenance,
        parse_errors=errors,
        unparsed_lines=unparsed,
    )
