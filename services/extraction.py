from __future__ import annotations
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
    "loan estimate", "projected payments", "origination charges", "cash to close", "closing cost details",
]


def _parse_dollars(s: str) -> float:
    return float(s.replace(",", "").replace("$", "").strip())


def _to_months(raw: str, match_text: str) -> int:
    val = int(raw)
    return val * 12 if re.search(r"year|yr", match_text, re.I) and val <= 40 else val


def clean_extracted_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_pdf_text(text: str) -> str:
    text = clean_extracted_text(text)
    # Join label/value pairs often split across lines in PDF extraction.
    lines = [ln.strip() for ln in text.splitlines()]
    merged: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line:
            i += 1
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if nxt and len(line) <= 40 and len(nxt) <= 40:
            label_like = bool(re.search(r"(rate|term|amort|prepay|points|origination|underwriting|processing|appraisal|title|credit|lender|loan type|interest only)", line, re.I))
            value_like = bool(re.fullmatch(r"(?:\$?[\d,]+(?:\.\d+)?%?|YES|NO|Conventional|DSCR|Fixed|ARM|\d+\s*(?:years?|months?))", nxt, re.I))
            if label_like and value_like:
                merged.append(f"{line}: {nxt}")
                i += 2
                continue
        merged.append(line)
        i += 1
    return "\n".join(merged)


def is_likely_loan_quote(text: str) -> bool:
    lowered = text.lower()
    hits = sum(1 for kw in MORTGAGE_KEYWORDS if kw in lowered)
    return hits >= 3


def _extract_label_value(lines: list[str], labels: list[str], value_pattern: str) -> str | None:
    for i, line in enumerate(lines):
        lowered = line.lower()
        if not any(label.lower() in lowered for label in labels):
            continue
        # same-line match first
        m = re.search(value_pattern, line, re.I)
        if m:
            return m.group(1)
        # nearby lines next
        for j in range(i + 1, min(i + 4, len(lines))):
            m = re.search(value_pattern, lines[j], re.I)
            if m:
                return m.group(1)
    return None


def _loan_estimate_extract(text: str) -> dict:
    """Extra parser for common Loan Estimate / fee sheet layouts."""
    lines = [ln.strip() for ln in normalize_pdf_text(text).splitlines() if ln.strip()]
    results: dict = {}
    confidence: dict = {}

    def set_num(key: str, raw: str | None, pct: bool = False, months=False):
        if raw is None:
            return
        try:
            value = raw.strip()
            if months:
                value_num = _to_months(re.search(r"(\d+)", value).group(1), value)
            else:
                value_num = _parse_dollars(value) if not pct else float(value.replace(",", "").replace("%", "").strip())
            results[key] = value_num
            confidence[key] = "medium"
        except Exception:
            pass

    set_num("rate_percent", _extract_label_value(lines, ["Interest Rate", "Note Rate", "rate"], r"([0-9]{1,2}(?:\.[0-9]{1,3})?)\s*%"), pct=True)
    set_num("loan_term_months", _extract_label_value(lines, ["Loan Term", "Term"], r"(\d+\s*(?:years?|months?))"), months=True)
    set_num("amortization_months", _extract_label_value(lines, ["Amortization", "Amortized Over"], r"(\d+\s*(?:years?|months?))"), months=True)
    set_num("interest_only_months", _extract_label_value(lines, ["Interest Only", "I/O"], r"(\d+\s*(?:years?|months?))"), months=True)
    set_num("underwriting_fee", _extract_label_value(lines, ["Underwriting", "Underwriting Fee"], r"\$\s*([\d,]+(?:\.\d+)?)"))
    set_num("processing_fee", _extract_label_value(lines, ["Processing", "Processing Fee", "Administrative Fee"], r"\$\s*([\d,]+(?:\.\d+)?)"))
    set_num("appraisal_fee", _extract_label_value(lines, ["Appraisal", "Appraisal Fee"], r"\$\s*([\d,]+(?:\.\d+)?)"))
    set_num("title_fee", _extract_label_value(lines, ["Title", "Settlement", "Closing Fee"], r"\$\s*([\d,]+(?:\.\d+)?)"))
    set_num("lender_credit", _extract_label_value(lines, ["Lender Credit", "Credit"], r"\$\s*([\d,]+(?:\.\d+)?)"))

    # Points often appear as "% of Loan Amount (Points)".
    points_val = _extract_label_value(lines, ["Points", "% of Loan Amount (Points)", "Origination Charges"], r"([0-9]{1,2}(?:\.[0-9]{1,3})?)\s*%")
    if points_val is not None:
        set_num("points_percent", points_val, pct=True)
    else:
        # Sometimes points are only shown as a dollar amount. If line contains both points and dollar figure,
        # leave it for manual review rather than inventing a percent.
        pass

    prepay_line = _extract_label_value(lines, ["Prepayment Penalty"], r"\b(YES|NO)\b")
    if prepay_line:
        if prepay_line.upper() == "NO":
            results["prepay_type"] = "none"
            results["prepay_months"] = 0
            confidence["prepay_type"] = "high"
            confidence["prepay_months"] = "high"
        else:
            # Find an accompanying term in nearby lines.
            prepay_term = _extract_label_value(lines, ["Prepayment Penalty", "Prepayment"], r"(\d+\s*(?:years?|months?))")
            if prepay_term:
                results["prepay_months"] = _to_months(re.search(r"(\d+)", prepay_term).group(1), prepay_term)
                confidence["prepay_months"] = "medium"
            results.setdefault("prepay_type", "declining")
            confidence.setdefault("prepay_type", "low")

    lender_name = _extract_label_value(lines, ["Lender"], r"Lender\s*:?\s*(.+)$")
    if lender_name and len(lender_name.split()) <= 12:
        results["lender_name"] = lender_name.strip()
        confidence["lender_name"] = "medium"

    loan_type = _extract_label_value(lines, ["Loan Type"], r"Loan Type\s*:?\s*(.+)$")
    if loan_type:
        results["program_name"] = loan_type.strip()
        confidence["program_name"] = "low"

    return {"fields": results, "confidence": confidence, "clean_text": normalize_pdf_text(text)}


def regex_extract(text: str) -> dict:
    text = normalize_pdf_text(text)
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
    if re.search(r"no[- ]*prepay|no\s+prepayment\s+penalty|prepayment penalty\s*:?\s*no\b", lowered, re.I):
        results["prepay_type"] = "none"
        results["prepay_months"] = 0
        confidence["prepay_type"] = "high"
        confidence["prepay_months"] = "high"
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

    # Supplement with loan-estimate-style parsing for missed fields.
    le_result = _loan_estimate_extract(text)
    for key, value in le_result.get("fields", {}).items():
        results.setdefault(key, value)
    for key, value in le_result.get("confidence", {}).items():
        confidence.setdefault(key, value)

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
    text = normalize_pdf_text(text)
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
    return normalize_pdf_text(text)


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

    # First try targeted parsers, then AI, then broader regex.
    targeted = _loan_estimate_extract(text)
    targeted["method"] = "pdf_text+targeted"
    if len(targeted.get("fields", {})) >= 3:
        return targeted

    ai_result = ai_extract(text)
    ai_result["method"] = "pdf_text+ai"
    if ai_result.get("fields"):
        # backfill any obvious misses from targeted extraction
        for k, v in targeted.get("fields", {}).items():
            ai_result["fields"].setdefault(k, v)
        for k, v in targeted.get("confidence", {}).items():
            ai_result["confidence"].setdefault(k, v)
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
