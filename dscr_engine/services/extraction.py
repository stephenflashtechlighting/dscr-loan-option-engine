from __future__ import annotations
import re
import json
import os
from models import LoanScenario


# ── Regex extraction ───────────────────────────────────────────────────────────

_RATE_RE = re.compile(r"(?:rate|interest)[^\d]*(\d{1,2}(?:\.\d{1,4})?)\s*%", re.I)
_POINTS_RE = re.compile(r"(\d{1,2}(?:\.\d{1,3})?)\s*(?:point|pt)s?", re.I)
_PREPAY_RE = re.compile(r"(\d{1,2})\s*(?:year|yr|mo(?:nth)?s?)[- ]*(?:prepay|penalty|step)", re.I)
_PREPAY_MONTHS_RE = re.compile(r"prepay(?:ment)?\s*(?:penalty)?[:\s]*(\d{2,3})\s*(?:month|mo)", re.I)
_IO_RE = re.compile(r"(?:IO|interest[- ]*only)[^\d]*(\d{1,2})\s*(?:year|yr|month|mo)", re.I)
_UW_FEE_RE = re.compile(r"underwr[^\d]*\$?([\d,]+)", re.I)
_PROC_FEE_RE = re.compile(r"process[^\d]*\$?([\d,]+)", re.I)
_APPRAISAL_RE = re.compile(r"apprais[^\d]*\$?([\d,]+)", re.I)
_TITLE_RE = re.compile(r"title[^\d]*\$?([\d,]+)", re.I)
_CREDIT_RE = re.compile(r"(?:lender\s*credit|credit)[^\d]*\$?([\d,]+)", re.I)
_LENDER_RE = re.compile(r"(?:lender|bank|from)[:\s]+([A-Za-z0-9 &.,'-]{2,40}?)(?:\n|$)", re.I)
_PROGRAM_RE = re.compile(r"(?:program|product)[:\s]+([A-Za-z0-9 /-]{2,40}?)(?:\n|$|,)", re.I)


def _parse_dollars(s: str) -> float:
    return float(s.replace(",", "").strip())


def regex_extract(text: str) -> dict:
    """
    Extract loan fields from pasted text using regex.
    Returns dict of field->value with a confidence dict.
    """
    results = {}
    confidence = {}

    def _try(pattern, key, transform=None, multiplier=1.0):
        m = pattern.search(text)
        if m:
            try:
                raw = m.group(1)
                val = float(raw.replace(",", "")) * multiplier if transform is None else transform(raw)
                results[key] = val
                confidence[key] = "medium"
            except Exception:
                pass

    _try(_RATE_RE, "rate_percent")
    _try(_POINTS_RE, "points_percent")

    # Prepay: prefer explicit months, else convert years
    mp = _PREPAY_MONTHS_RE.search(text)
    if mp:
        results["prepay_months"] = int(mp.group(1))
        confidence["prepay_months"] = "medium"
    else:
        yp = _PREPAY_RE.search(text)
        if yp:
            val = int(yp.group(1))
            results["prepay_months"] = val * 12 if val <= 10 else val
            confidence["prepay_months"] = "low"

    # IO months: convert years if small number
    iom = _IO_RE.search(text)
    if iom:
        val = int(iom.group(1))
        results["interest_only_months"] = val * 12 if val <= 10 else val
        confidence["interest_only_months"] = "medium"

    _try(_UW_FEE_RE, "underwriting_fee", lambda x: _parse_dollars(x))
    _try(_PROC_FEE_RE, "processing_fee", lambda x: _parse_dollars(x))
    _try(_APPRAISAL_RE, "appraisal_fee", lambda x: _parse_dollars(x))
    _try(_TITLE_RE, "title_fee", lambda x: _parse_dollars(x))
    _try(_CREDIT_RE, "lender_credit", lambda x: _parse_dollars(x))

    # Prepay type
    if re.search(r"no[- ]*prepay", text, re.I):
        results["prepay_type"] = "none"
        results["prepay_months"] = 0
        confidence["prepay_type"] = "high"
    elif re.search(r"declin", text, re.I):
        results["prepay_type"] = "declining"
        confidence["prepay_type"] = "medium"
    elif re.search(r"\bflat\b.*prepay", text, re.I):
        results["prepay_type"] = "flat"
        confidence["prepay_type"] = "medium"

    # Lender / program names
    lm = _LENDER_RE.search(text)
    if lm:
        results["lender_name"] = lm.group(1).strip().rstrip(",")
        confidence["lender_name"] = "low"

    pm = _PROGRAM_RE.search(text)
    if pm:
        results["program_name"] = pm.group(1).strip()
        confidence["program_name"] = "low"

    return {"fields": results, "confidence": confidence}


# ── AI extraction ──────────────────────────────────────────────────────────────

_AI_SYSTEM = """You are a mortgage data extraction assistant.
Extract loan scenario fields from the provided text and return ONLY a JSON object.
No explanation, no markdown, no code fences — pure JSON only.

JSON schema:
{
  "lender_name": string or null,
  "program_name": string or null,
  "rate_percent": number or null,
  "points_percent": number or null,
  "loan_term_months": number or null,
  "amortization_months": number or null,
  "interest_only_months": number or null,
  "prepay_type": "declining" | "flat" | "none" | null,
  "prepay_months": number or null,
  "underwriting_fee": number or null,
  "processing_fee": number or null,
  "appraisal_fee": number or null,
  "title_fee": number or null,
  "lender_credit": number or null,
  "notes": string or null,
  "confidence": {
    "field_name": "high" | "medium" | "low"
  }
}

Rules:
- All dollar values as plain numbers (no $ or commas)
- Prepay period always in months (convert years × 12)
- IO period always in months
- If a field is not present in the text, return null for it
- confidence object should rate each non-null field
"""


def ai_extract(text: str) -> dict:
    """
    Call Anthropic API to extract loan fields from unstructured text.
    Returns same format as regex_extract: {"fields": {...}, "confidence": {...}}
    """
    try:
        import anthropic
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            system=_AI_SYSTEM,
            messages=[{"role": "user", "content": f"Extract loan fields from this text:\n\n{text}"}],
        )
        raw = response.content[0].text.strip()
        # Strip any accidental markdown
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        parsed = json.loads(raw)
        confidence = parsed.pop("confidence", {})
        # Remove nulls
        fields = {k: v for k, v in parsed.items() if v is not None}
        return {"fields": fields, "confidence": confidence}
    except Exception as e:
        return {"fields": {}, "confidence": {}, "error": str(e)}


def merge_extraction_into_scenario(
    base: LoanScenario,
    extraction: dict,
) -> LoanScenario:
    """Apply extracted fields onto a base scenario, returning updated copy."""
    fields = extraction.get("fields", {})
    data = base.model_dump()
    for k, v in fields.items():
        if k in data and v is not None:
            data[k] = v
    return LoanScenario(**data)
