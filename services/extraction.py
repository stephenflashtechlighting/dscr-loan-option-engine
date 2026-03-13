from __future__ import annotations
import base64
import io
import json
import re
from models import LoanScenario

# ── Regex helpers ─────────────────────────────────────────────────────────────
_RATE_RE = re.compile(r"(?:note\s*rate|interest\s*rate|rate)\s*[:\-]?\s*([0-9]{1,2}(?:\.[0-9]{1,3})?)\s*%", re.I)
_POINTS_RE = re.compile(r"(?:origination\s*points?|points?)\s*[:\-]?\s*([0-9]{1,2}(?:\.[0-9]{1,3})?)\s*%?", re.I)
_PREPAY_RE = re.compile(r"(?:prepay(?:ment)?(?:\s+penalty)?|ppp)\s*[:\-]?\s*([0-9]{1,2})\s*(?:year|yr|month|mo)s?", re.I)
_PREPAY_MONTHS_RE = re.compile(r"(?:prepay(?:ment)?(?:\s+penalty)?|ppp)\s*[:\-]?\s*([0-9]{1,3})\s*(?:months?|mos?)", re.I)
_IO_RE = re.compile(r"(?:interest[- ]only|i/o|io\s*period)\s*[:\-]?\s*([0-9]{1,3})\s*(?:months?|mos?|years?|yrs?)", re.I)
_TERM_RE = re.compile(r"(?:loan\s*term|term)\s*[:\-]?\s*([0-9]{1,3})\s*(?:months?|mos?|years?|yrs?)", re.I)
_AMORT_RE = re.compile(r"(?:amortization|amortized\s*over|amort)\s*[:\-]?\s*([0-9]{1,3})\s*(?:months?|mos?|years?|yrs?)", re.I)
_UW_FEE_RE = re.compile(r"(?:underwriting(?:\s*fee)?)\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", re.I)
_PROC_FEE_RE = re.compile(r"(?:processing(?:\s*fee)?|proc(?:essing)?(?:\s*fee)?)\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", re.I)
_APPRAISAL_RE = re.compile(r"(?:appraisal(?:\s*fee)?)\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", re.I)
_TITLE_RE = re.compile(r"(?:title(?:\s*(?:fee|charges?|services?))?)\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", re.I)
_CREDIT_RE = re.compile(r"(?:lender\s*credit|credit\s*to\s*borrower)\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", re.I)
_LENDER_RE = re.compile(r"(?:lender|bank|credit\s*union)\s*[:\-]?\s*([A-Za-z0-9 &.,'\-]{2,60}?)(?:\n|$)", re.I)
_PROGRAM_RE = re.compile(r"(?:program|product|loan\s*program)\s*[:\-]?\s*([A-Za-z0-9 /\-]{2,60}?)(?:\n|$|,)", re.I)

MORTGAGE_KEYWORDS = [
    "rate", "interest only", "loan term", "amortization", "prepay", "underwriting",
    "processing fee", "appraisal", "title", "lender credit", "dscr", "term sheet", "quote",
]


def _parse_dollars(s: str) -> float:
    return float(s.replace(",", "").strip())


def _to_months(raw: str, match_text: str) -> int:
    val = int(raw)
    return val * 12 if re.search(r"year|yr", match_text, re.I) and val <= 40 else val


def clean_extracted_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_likely_loan_quote(text: str) -> bool:
    lowered = text.lower()
    hits = sum(1 for kw in MORTGAGE_KEYWORDS if kw in lowered)
    return hits >= 3


def regex_extract(text: str) -> dict:
    text = clean_extracted_text(text)
    results = {}
    confidence = {}

    def _try(pattern, key, transform=None):
        m = pattern.search(text)
        if not m:
            return
        try:
            raw = m.group(1)
            val = transform(raw, m.group(0)) if transform else float(raw.replace(",", ""))
            results[key] = val
            confidence[key] = "medium"
        except Exception:
            pass

    _try(_RATE_RE, "rate_percent")
    _try(_POINTS_RE, "points_percent")
    _try(_TERM_RE, "loan_term_months", lambda raw, full: _to_months(raw, full))
    _try(_AMORT_RE, "amortization_months", lambda raw, full: _to_months(raw, full))

    mp = _PREPAY_MONTHS_RE.search(text)
    if mp:
        results["prepay_months"] = int(mp.group(1))
        confidence["prepay_months"] = "medium"
    else:
        yp = _PREPAY_RE.search(text)
        if yp:
            results["prepay_months"] = _to_months(yp.group(1), yp.group(0))
            confidence["prepay_months"] = "medium"

    iom = _IO_RE.search(text)
    if iom:
        results["interest_only_months"] = _to_months(iom.group(1), iom.group(0))
        confidence["interest_only_months"] = "medium"

    _try(_UW_FEE_RE, "underwriting_fee", lambda x, _: _parse_dollars(x))
    _try(_PROC_FEE_RE, "processing_fee", lambda x, _: _parse_dollars(x))
    _try(_APPRAISAL_RE, "appraisal_fee", lambda x, _: _parse_dollars(x))
    _try(_TITLE_RE, "title_fee", lambda x, _: _parse_dollars(x))
    _try(_CREDIT_RE, "lender_credit", lambda x, _: _parse_dollars(x))

    lowered = text.lower()
    if re.search(r"no[- ]*prepay|no\s+prepayment\s+penalty", lowered, re.I):
        results["prepay_type"] = "none"
        results["prepay_months"] = 0
        confidence["prepay_type"] = "high"
    elif re.search(r"declin", lowered, re.I):
        results["prepay_type"] = "declining"
        confidence["prepay_type"] = "medium"
    elif re.search(r"\bflat\b.*prepay|prepay.*\bflat\b", lowered, re.I):
        results["prepay_type"] = "flat"
        confidence["prepay_type"] = "medium"

    lm = _LENDER_RE.search(text)
    if lm:
        lender = lm.group(1).strip().rstrip(",")
        if len(lender.split()) <= 8:
            results["lender_name"] = lender
            confidence["lender_name"] = "low"

    pm = _PROGRAM_RE.search(text)
    if pm:
        results["program_name"] = pm.group(1).strip()
        confidence["program_name"] = "low"

    return {"fields": results, "confidence": confidence, "clean_text": text}


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
- Prepay period always in months (convert years x 12)
- IO period always in months
- If a field is not present in the text, return null for it
- confidence object should rate each non-null field
"""


def ai_extract(text: str) -> dict:
    text = clean_extracted_text(text)
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
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        parsed = json.loads(raw)
        confidence = parsed.pop("confidence", {})
        fields = {k: v for k, v in parsed.items() if v is not None}
        return {"fields": fields, "confidence": confidence, "clean_text": text}
    except Exception as e:
        return {"fields": {}, "confidence": {}, "error": str(e), "clean_text": text}


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    from pdfminer.high_level import extract_text
    text = extract_text(io.BytesIO(pdf_bytes))
    return clean_extracted_text(text)


def extract_from_pdf(pdf_bytes: bytes) -> dict:
    try:
        text = extract_text_from_pdf(pdf_bytes)
    except Exception as e:
        return {"fields": {}, "confidence": {}, "error": f"PDF text extraction failed: {e}", "method": "failed"}

    if not text or len(text) < 40:
        return {"fields": {}, "confidence": {}, "error": "The PDF did not contain enough readable text to extract.", "method": "failed", "clean_text": text}

    if not is_likely_loan_quote(text):
        return {
            "fields": {},
            "confidence": {},
            "error": "This PDF does not look like a loan quote or fee sheet. Paste the relevant page text or upload a cleaner lender document.",
            "method": "failed",
            "clean_text": text,
        }

    # First try AI on extracted text; if that fails, use regex on cleaned text only.
    ai_result = ai_extract(text)
    ai_result["method"] = "pdf_text+ai"
    if ai_result.get("fields"):
        return ai_result

    regex_result = regex_extract(text)
    regex_result["method"] = "pdf_text+regex"
    if regex_result.get("fields"):
        return regex_result

    return {
        "fields": {},
        "confidence": {},
        "error": "No reliable loan fields could be extracted from this PDF.",
        "method": "failed",
        "clean_text": text,
    }


def merge_extraction_into_scenario(base: LoanScenario, extraction: dict) -> LoanScenario:
    fields = extraction.get("fields", {})
    data = base.model_dump()
    for k, v in fields.items():
        if k in data and v is not None:
            data[k] = v
    return LoanScenario(**data)
