"""
services/input_classifier.py
Classify raw input text into one of the known intake types so each can be
routed to the appropriate specialized parser.
"""
from __future__ import annotations
import re
from dataclasses import dataclass


INPUT_TYPES = [
    "manual_kv",         # structured key: value block
    "single_quote",      # single-scenario free-text quote
    "multi_scenario",    # multi-scenario summary / table
    "listing_url",       # property listing URL
    "csv",               # uploaded CSV (handled upstream, rarely reaches here)
    "unknown",           # could not determine
]


@dataclass
class ClassifierResult:
    input_type: str
    confidence: str          # high / medium / low
    rationale: str
    signals: dict


# ── Helpers ──────────────────────────────────────────────────────────────────

def _count_kv_lines(text: str) -> int:
    """Count lines that look like  Label: value  or  Label (unit): value."""
    pattern = re.compile(
        r"^[A-Za-z][A-Za-z0-9 /\-]*(?:\s*\([^)]{1,20}\))?\s*:\s*.+",
        re.MULTILINE,
    )
    return len(pattern.findall(text))


_KV_FIELD_LABELS = re.compile(
    r"\b(lender\s*name?|program\s*name?|note\s*rate|points?|loan\s*term|"
    r"amortization|interest[\s-]*only|prepay|underwriting|processing\s*fee|"
    r"appraisal|title\s*fee|lender\s*credit)\b",
    re.I,
)

_MULTI_SIGNALS = re.compile(
    r"(20\s*%\s*down|25\s*%\s*down|30\s*%\s*down|"
    r"\d+\s*%\s*down\s*/|scenario\s+[A-Za-z0-9]+|option\s+\d+|"
    r"\bvs\.?\s|\bcompar)",
    re.I,
)

_RATE_ROW_RE = re.compile(
    r"(?:^|\n)\s*(?:25\s*%?\s*down|20\s*%?\s*down|[A-Z]{2,}.*?)"
    r"\s*/?\s*\d{1,2}\.\d{1,3}\s*%?\s*/?\s*[\d.]+\s*(?:points?|pts?)?",
    re.I | re.MULTILINE,
)

_LISTING_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:trulia|zillow|realtor|redfin)\.com/",
    re.I,
)

_SINGLE_QUOTE_SIGNALS = re.compile(
    r"\b(loan\s*estimate|fee\s*sheet|term\s*sheet|lender\s*quote|"
    r"closing\s*disclosure|good\s*faith\s*estimate)\b",
    re.I,
)


# ── Main classifier ───────────────────────────────────────────────────────────

def classify(text: str) -> ClassifierResult:
    """Return a ClassifierResult for the given raw input string."""
    stripped = (text or "").strip()
    if not stripped:
        return ClassifierResult(
            input_type="unknown",
            confidence="high",
            rationale="Input is empty.",
            signals={},
        )

    signals: dict = {}

    # 1. Listing URL — fastest check
    if _LISTING_URL_RE.search(stripped):
        domain_m = _LISTING_URL_RE.search(stripped)
        domain = domain_m.group(0) if domain_m else ""
        signals["listing_url"] = True
        return ClassifierResult(
            input_type="listing_url",
            confidence="high",
            rationale=f"URL matches a known listing domain ({domain}).",
            signals=signals,
        )

    lines = stripped.splitlines()
    kv_count = _count_kv_lines(stripped)
    field_label_count = len(_KV_FIELD_LABELS.findall(stripped))
    multi_signal_count = len(_MULTI_SIGNALS.findall(stripped))
    rate_rows = len(_RATE_ROW_RE.findall(stripped))
    single_doc_signals = len(_SINGLE_QUOTE_SIGNALS.findall(stripped))

    signals["kv_lines"] = kv_count
    signals["field_labels"] = field_label_count
    signals["multi_signals"] = multi_signal_count
    signals["rate_rows"] = rate_rows
    signals["single_doc_signals"] = single_doc_signals
    signals["total_lines"] = len(lines)

    # 2. Manual key:value — high kv density and recognizable field labels
    if kv_count >= 6 and field_label_count >= 4:
        return ClassifierResult(
            input_type="manual_kv",
            confidence="high" if kv_count >= 8 else "medium",
            rationale=(
                f"Found {kv_count} key:value lines and {field_label_count} "
                "recognized loan field labels. This looks like a structured paste."
            ),
            signals=signals,
        )

    if kv_count >= 3 and field_label_count >= 2:
        # Could still be kv but less confident
        pass  # fall through to further checks

    # 3. Multi-scenario summary
    if multi_signal_count >= 2 or rate_rows >= 2:
        return ClassifierResult(
            input_type="multi_scenario",
            confidence="high" if (multi_signal_count >= 3 or rate_rows >= 3) else "medium",
            rationale=(
                f"Found {multi_signal_count} multi-scenario indicators and "
                f"{rate_rows} rate-row patterns. Likely a summary with multiple options."
            ),
            signals=signals,
        )

    # 4. Single-scenario quote text
    if single_doc_signals >= 1 or (field_label_count >= 2 and kv_count >= 2):
        return ClassifierResult(
            input_type="single_quote",
            confidence="medium",
            rationale=(
                f"Found {single_doc_signals} document-type indicators and "
                f"{field_label_count} field labels. Likely a single-scenario quote."
            ),
            signals=signals,
        )

    # 5. Low-confidence kv fallback
    if kv_count >= 3 and field_label_count >= 2:
        return ClassifierResult(
            input_type="manual_kv",
            confidence="low",
            rationale=(
                f"Moderate key:value density ({kv_count} lines, {field_label_count} field labels). "
                "Treating as structured input — verify results."
            ),
            signals=signals,
        )

    # 6. Unknown
    return ClassifierResult(
        input_type="unknown",
        confidence="low",
        rationale=(
            "Could not identify a reliable input type. "
            "Consider using manual entry or the CSV upload path."
        ),
        signals=signals,
    )
