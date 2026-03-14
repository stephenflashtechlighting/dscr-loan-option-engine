"""
services/multi_quote_parser.py
Extract multiple scenario rows from a summary quote document or table.

Output: a list of ScenarioRow dicts suitable for display in a picker table.
The caller (Import page) lets the user select one row, then routes it through
single_quote_parser or manual_input_parser for full field population.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ScenarioRow:
    label: str
    down_pct: Optional[float]
    loan_amount: Optional[float]
    note_rate: Optional[float]
    points_pct: Optional[float]
    points_dollars: Optional[float]
    title_escrow_summary: Optional[str]
    closing_cost_range: Optional[str]
    raw_text: str
    confidence: str          # high / medium / low
    index: int               # row index in the parsed list


# ── Regex patterns ────────────────────────────────────────────────────────────

# Matches rows like:
#   20% down / 6.25% / 0 pts
#   25% Down: 6.125 / 0.65 points
#   Option A – 25% down – 6.00% – 1.5 pts
_RATE_POINTS_ROW_RE = re.compile(
    r"(?P<down>\d{1,2})\s*%\s*[Dd]own"
    r"(?:[^\d]{1,20})"
    r"(?P<rate>\d{1,2}\.\d{1,4})\s*%?"
    r"(?:[^\d]{1,20})"
    r"(?P<points>[\-]?\d{1,2}(?:\.\d{1,3})?)\s*(?:points?|pts?)?",
    re.I,
)

# Simpler: a decimal rate followed by some separator and points
_RATE_ONLY_ROW_RE = re.compile(
    r"(?P<rate>\d{1,2}\.\d{3,4})\s*%?\s*[/|,\-–—]\s*(?P<points>[\-]?\d{1,2}(?:\.\d{1,3})?)\s*(?:points?|pts?|%)",
    re.I,
)

_LOAN_AMOUNT_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)", re.I)
_DOWN_PCT_RE = re.compile(r"(\d{1,2})\s*%\s*[Dd]own", re.I)
_RATE_LABELED_RE = re.compile(
    r"(?:rate|note\s*rate|interest\s*rate)\s*[:\-]?\s*(\d{1,2}\.\d{1,4})\s*%?",
    re.I,
)
_POINTS_LABELED_RE = re.compile(
    r"(?:points?|origination)\s*[:\-]?\s*([\-]?\d{1,2}(?:\.\d{1,3})?)\s*%?",
    re.I,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_dollar(s: str) -> Optional[float]:
    try:
        return float(s.replace(",", "").strip())
    except ValueError:
        return None


def _find_loan_amount(line: str) -> Optional[float]:
    m = _LOAN_AMOUNT_RE.search(line)
    if m:
        return _parse_dollar(m.group(1))
    return None


def _infer_label(row_text: str, index: int) -> str:
    """Try to pull a descriptive label from the row text."""
    # Down pct is a good label
    dm = _DOWN_PCT_RE.search(row_text)
    rate_m = re.search(r"(\d{1,2}\.\d{1,4})\s*%?", row_text)
    if dm and rate_m:
        return f"{dm.group(1)}% down / {rate_m.group(1)}%"
    if dm:
        return f"{dm.group(1)}% down — option {index + 1}"
    return f"Option {index + 1}"


# ── Main extractor ────────────────────────────────────────────────────────────

def extract_scenarios(text: str) -> list[ScenarioRow]:
    """
    Parse summary text and return a list of ScenarioRow objects.
    Returns empty list if no multi-scenario structure is detected.
    """
    rows: list[ScenarioRow] = []

    # Strategy 1 – rich down/rate/points rows
    for m in _RATE_POINTS_ROW_RE.finditer(text):
        idx = len(rows)
        raw = m.group(0).strip()
        down_pct = float(m.group("down"))
        note_rate = float(m.group("rate"))
        points_pct = float(m.group("points"))
        # loan amount from nearby context
        start = max(0, m.start() - 120)
        end = min(len(text), m.end() + 120)
        context = text[start:end]
        loan_amount = _find_loan_amount(context)
        rows.append(ScenarioRow(
            label=_infer_label(raw, idx),
            down_pct=down_pct,
            loan_amount=loan_amount,
            note_rate=note_rate,
            points_pct=points_pct,
            points_dollars=None,
            title_escrow_summary=None,
            closing_cost_range=None,
            raw_text=raw,
            confidence="high",
            index=idx,
        ))

    if rows:
        return _dedupe(rows)

    # Strategy 2 – look for lines that contain a rate and optional points
    # (looser, used when strategy 1 finds nothing)
    lines = text.splitlines()
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        rate_m = _RATE_LABELED_RE.search(line) or re.search(r"\b(\d{1,2}\.\d{3,4})\b", line)
        if not rate_m:
            continue
        note_rate = float(rate_m.group(1))
        if not (3.0 <= note_rate <= 18.0):
            continue
        points_m = _POINTS_LABELED_RE.search(line)
        points_pct = float(points_m.group(1)) if points_m else None
        down_m = _DOWN_PCT_RE.search(line)
        down_pct = float(down_m.group(1)) if down_m else None
        loan_amount = _find_loan_amount(line)
        idx = len(rows)
        rows.append(ScenarioRow(
            label=_infer_label(line, idx),
            down_pct=down_pct,
            loan_amount=loan_amount,
            note_rate=note_rate,
            points_pct=points_pct,
            points_dollars=None,
            title_escrow_summary=None,
            closing_cost_range=None,
            raw_text=line,
            confidence="medium",
            index=idx,
        ))

    return _dedupe(rows)


def _dedupe(rows: list[ScenarioRow]) -> list[ScenarioRow]:
    """Remove duplicate rate+points combinations."""
    seen: set[tuple] = set()
    out: list[ScenarioRow] = []
    for r in rows:
        key = (round(r.note_rate or 0, 4), round(r.points_pct or 0, 4), r.down_pct)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    # Re-index
    for i, r in enumerate(out):
        r.index = i
    return out
