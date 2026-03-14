"""
services/csv_import.py
CSV upload import — load, validate headers, normalize rows, convert to scenarios.
"""
from __future__ import annotations
import io
import re
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from models import LoanScenario
from config import FEE_DEFAULTS, PREPAY_TYPES


# ── Canonical column definitions ─────────────────────────────────────────────

# Required for import
REQUIRED_COLUMNS = {
    "lender_name",
    "program_name",
    "purchase_price",
    "loan_amount",
    "note_rate_percent",
    "points_percent",
    "loan_term_months",
    "amortization_months",
}

# All columns recognized by the importer
ALL_COLUMNS = [
    "quote_name", "lender_name", "program_name", "scenario_label",
    "purchase_price", "loan_amount", "down_payment", "ltv_percent",
    "note_rate_percent", "apr_percent", "points_percent", "points_dollars",
    "loan_term_months", "amortization_months", "interest_only_months",
    "prepay_type", "prepay_months",
    "underwriting_fee", "processing_fee", "origination_fee", "appraisal_fee",
    "title_fee", "credit_report_fee", "flood_cert_fee", "tax_service_fee",
    "recording_fee", "escrow_fee",
    "lender_credit", "seller_credit",
    "estimated_monthly_pi", "estimated_monthly_ti", "estimated_monthly_total",
    "estimated_cash_to_close", "estimated_total_closing_costs",
    "dscr",
    "property_address", "property_city", "property_state", "property_zip",
    "property_type", "occupancy",
    "quote_date", "lock_period_days", "fico",
    "notes",
]

# Simple lender template columns
SIMPLE_TEMPLATE_COLUMNS = [
    "lender_name", "program_name", "scenario_label",
    "purchase_price", "loan_amount", "down_payment",
    "note_rate_percent", "points_percent",
    "loan_term_months", "amortization_months", "interest_only_months",
    "prepay_type", "prepay_months",
    "underwriting_fee", "processing_fee", "appraisal_fee",
    "title_fee", "lender_credit",
    "estimated_monthly_total", "estimated_cash_to_close",
    "notes",
]


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class CsvRowResult:
    row_index: int
    raw: dict
    scenario: Optional[LoanScenario]
    errors: list[str]
    warnings: list[str]
    valid: bool


@dataclass
class CsvImportResult:
    rows: list[CsvRowResult]
    header_errors: list[str]
    missing_required: list[str]
    total_rows: int
    valid_rows: int


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clean_numeric(val) -> Optional[float]:
    """Strip $, %, commas from a value and return float or None."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    if not s or s.lower() in ("", "nan", "none", "n/a", "-"):
        return None
    s = re.sub(r"[$%,\s]", "", s)
    try:
        return float(s)
    except ValueError:
        return None


def _clean_str(val) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    return str(val).strip()


def _normalize_prepay_type(val: str) -> str:
    v = val.strip().lower()
    if re.search(r"declin", v):
        return "declining"
    if re.search(r"\bflat\b", v):
        return "flat"
    if re.search(r"\bnone\b|\bno\b|n/a|0|^$", v):
        return "none"
    return "declining"  # safe default


# ── Public API ────────────────────────────────────────────────────────────────

def load_csv(file_bytes: bytes) -> CsvImportResult:
    """
    Load CSV bytes, validate headers, parse and validate each row.
    Returns a CsvImportResult.
    """
    try:
        df = pd.read_csv(io.BytesIO(file_bytes), dtype=str)
    except Exception as exc:
        return CsvImportResult(
            rows=[],
            header_errors=[f"Could not read CSV file: {exc}"],
            missing_required=list(REQUIRED_COLUMNS),
            total_rows=0,
            valid_rows=0,
        )

    # Normalize column names
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # Header validation
    header_errors: list[str] = []
    missing_required = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_required:
        header_errors.append(
            f"Missing required columns: {', '.join(missing_required)}"
        )

    if header_errors:
        return CsvImportResult(
            rows=[],
            header_errors=header_errors,
            missing_required=missing_required,
            total_rows=len(df),
            valid_rows=0,
        )

    row_results: list[CsvRowResult] = []

    for i, row in df.iterrows():
        row_dict = row.to_dict()
        errors: list[str] = []
        warnings: list[str] = []

        # Required field extraction
        lender_name = _clean_str(row_dict.get("lender_name"))
        program_name = _clean_str(row_dict.get("program_name"))
        purchase_price = _clean_numeric(row_dict.get("purchase_price"))
        loan_amount = _clean_numeric(row_dict.get("loan_amount"))
        note_rate = _clean_numeric(row_dict.get("note_rate_percent"))
        points_pct = _clean_numeric(row_dict.get("points_percent"))
        loan_term = _clean_numeric(row_dict.get("loan_term_months"))
        amort = _clean_numeric(row_dict.get("amortization_months"))

        # Required field validation
        if not lender_name:
            errors.append("lender_name is required and missing.")
        if not program_name:
            errors.append("program_name is required and missing.")
        if purchase_price is None:
            errors.append("purchase_price is required.")
        elif purchase_price <= 0:
            errors.append(f"purchase_price must be > 0 (got {purchase_price}).")
        if loan_amount is None:
            errors.append("loan_amount is required.")
        elif loan_amount <= 0:
            errors.append(f"loan_amount must be > 0 (got {loan_amount}).")
        if note_rate is None:
            errors.append("note_rate_percent is required.")
        elif not (0 < note_rate <= 20):
            errors.append(f"note_rate_percent {note_rate} is outside valid range (0–20).")
        if points_pct is None:
            warnings.append("points_percent is missing, defaulting to 0.")
            points_pct = 0.0
        if loan_term is None:
            warnings.append("loan_term_months missing, defaulting to 360.")
            loan_term = 360.0
        if amort is None:
            warnings.append("amortization_months missing, defaulting to 360.")
            amort = 360.0

        # Loan amount vs purchase price
        if purchase_price and loan_amount and loan_amount > purchase_price:
            errors.append(
                f"loan_amount ({loan_amount:,.0f}) exceeds purchase_price ({purchase_price:,.0f})."
            )

        # Optional fields with safe defaults
        io_months = int(_clean_numeric(row_dict.get("interest_only_months")) or 0)
        prepay_type_raw = _clean_str(row_dict.get("prepay_type"))
        prepay_type = _normalize_prepay_type(prepay_type_raw)
        prepay_months = int(_clean_numeric(row_dict.get("prepay_months")) or 60)

        uw_fee = _clean_numeric(row_dict.get("underwriting_fee")) or FEE_DEFAULTS["underwriting_fee"]
        proc_fee = _clean_numeric(row_dict.get("processing_fee")) or FEE_DEFAULTS["processing_fee"]
        appraisal_fee = _clean_numeric(row_dict.get("appraisal_fee")) or FEE_DEFAULTS["appraisal_fee"]
        title_fee = _clean_numeric(row_dict.get("title_fee")) or FEE_DEFAULTS["title_fee"]
        lender_credit = _clean_numeric(row_dict.get("lender_credit")) or 0.0
        notes = _clean_str(row_dict.get("notes"))

        scenario: Optional[LoanScenario] = None
        if not errors:
            try:
                # We need deal_id injected at import time — use 0 as sentinel
                scenario = LoanScenario(
                    deal_id=0,  # caller must set this before saving
                    lender_name=lender_name,
                    program_name=program_name,
                    rate_percent=float(note_rate),
                    points_percent=float(points_pct),
                    loan_term_months=int(loan_term),
                    amortization_months=int(amort),
                    interest_only_months=io_months,
                    prepay_type=prepay_type,
                    prepay_months=prepay_months,
                    underwriting_fee=float(uw_fee),
                    processing_fee=float(proc_fee),
                    appraisal_fee=float(appraisal_fee),
                    title_fee=float(title_fee),
                    lender_credit=float(lender_credit),
                    notes=notes or f"Imported from CSV",
                    source_type="csv",
                )
            except Exception as exc:
                errors.append(f"Could not create scenario: {exc}")

        row_results.append(CsvRowResult(
            row_index=int(i),
            raw=row_dict,
            scenario=scenario,
            errors=errors,
            warnings=warnings,
            valid=not errors,
        ))

    valid_count = sum(1 for r in row_results if r.valid)
    return CsvImportResult(
        rows=row_results,
        header_errors=[],
        missing_required=[],
        total_rows=len(df),
        valid_rows=valid_count,
    )


def generate_full_template_csv() -> str:
    """Return the full CSV template as a string."""
    header = ",".join(ALL_COLUMNS)
    sample = ",".join(
        "My Lender" if c == "lender_name"
        else "DSCR 30yr Fixed" if c == "program_name"
        else "Sample Option A" if c == "scenario_label"
        else "500000" if c == "purchase_price"
        else "375000" if c == "loan_amount"
        else "125000" if c == "down_payment"
        else "75" if c == "ltv_percent"
        else "7.250" if c == "note_rate_percent"
        else "1.500" if c == "points_percent"
        else "360" if c in ("loan_term_months", "amortization_months")
        else "declining" if c == "prepay_type"
        else "60" if c == "prepay_months"
        else "1395" if c == "underwriting_fee"
        else "0" if c == "processing_fee"
        else "700" if c == "appraisal_fee"
        else "1800" if c == "title_fee"
        else "0" if c == "lender_credit"
        else ""
        for c in ALL_COLUMNS
    )
    instructions = (
        "# DSCR Loan Option Engine — Full CSV Template\n"
        "# One row = one loan scenario\n"
        "# Use plain numbers only (no $ or % symbols preferred, though importer tolerates them)\n"
        "# Leave blank if unknown\n"
        "# Do NOT combine multiple options in one row\n"
    )
    return instructions + "\n" + header + "\n" + sample + "\n"


def generate_simple_template_csv() -> str:
    """Return the simplified lender-facing CSV template."""
    header = ",".join(SIMPLE_TEMPLATE_COLUMNS)
    sample = ",".join(
        "My Lender" if c == "lender_name"
        else "DSCR 30yr Fixed" if c == "program_name"
        else "Option A" if c == "scenario_label"
        else "500000" if c == "purchase_price"
        else "375000" if c == "loan_amount"
        else "125000" if c == "down_payment"
        else "7.250" if c == "note_rate_percent"
        else "1.500" if c == "points_percent"
        else "360" if c in ("loan_term_months", "amortization_months")
        else "0" if c == "interest_only_months"
        else "declining" if c == "prepay_type"
        else "60" if c == "prepay_months"
        else "1395" if c == "underwriting_fee"
        else "0" if c == "processing_fee"
        else "700" if c == "appraisal_fee"
        else "1800" if c == "title_fee"
        else "0" if c == "lender_credit"
        else ""
        for c in SIMPLE_TEMPLATE_COLUMNS
    )
    instructions = (
        "# DSCR Loan Option Engine — Simple Lender Template\n"
        "# Fill in one row per loan option and return this file\n"
        "# Use plain numbers only — no $ or % needed\n"
        "# Leave cells blank if a value does not apply\n"
        "# Do NOT merge multiple options into a single row\n"
    )
    return instructions + "\n" + header + "\n" + sample + "\n"
