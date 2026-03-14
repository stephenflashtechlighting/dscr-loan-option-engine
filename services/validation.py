"""
services/validation.py
Central validation rules for all import paths and manual save.

Usage:
    from services.validation import validate_scenario, ValidationResult

Returns a ValidationResult with:
    - status: "pass" | "warn" | "block"
    - hard_blocks: list of reasons that prevent save
    - warnings: list of caution messages
    - field_issues: dict of field → issue text (for inline display)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ValidationResult:
    status: str                          # "pass" | "warn" | "block"
    hard_blocks: list[str]               # must resolve to save
    warnings: list[str]                  # should review but can override
    field_issues: dict[str, str]         # field_name → human description

    @property
    def can_save(self) -> bool:
        return self.status != "block"

    @property
    def needs_review(self) -> bool:
        return self.status in ("warn", "block")


def validate_scenario(
    lender_name: str,
    program_name: str,
    rate_percent: float,
    points_percent: float,
    loan_amount: Optional[float],
    purchase_price: Optional[float],
    amortization_months: int,
    loan_term_months: int,
    underwriting_fee: float,
    processing_fee: float,
    appraisal_fee: float,
    title_fee: float,
    lender_credit: float,
    prepay_type: Optional[str],
    prepay_months: Optional[int],
    estimated_cash_to_close: Optional[float] = None,
    import_source: str = "unknown",
    num_defaulted_fields: int = 0,
) -> ValidationResult:
    """
    Run all validation rules against the given scenario field values.

    Parameters correspond to individual scenario fields — call this from any
    intake path before persisting to the database.
    """
    blocks: list[str] = []
    warnings: list[str] = []
    field_issues: dict[str, str] = {}

    # ── Hard blocks ──────────────────────────────────────────────────────────

    # Note rate
    try:
        r = float(rate_percent)
        if r <= 0 or r > 20.0:
            blocks.append(f"Note rate {r:.3f}% is outside the valid range (0–20%).")
            field_issues["rate_percent"] = f"Value {r} is out of range."
    except (TypeError, ValueError):
        blocks.append("Note rate is missing or invalid.")
        field_issues["rate_percent"] = "Missing or invalid."

    # Points
    try:
        p = float(points_percent)
        if p < -5.0 or p > 5.0:
            # Warn rather than hard-block since some lenders use unusual structures
            warnings.append(
                f"Points value {p:.3f}% is outside the typical range (-5 to +5). "
                "Verify against the source document before saving."
            )
            field_issues["points_percent"] = f"Unusual value: {p}."
    except (TypeError, ValueError):
        warnings.append("Points value could not be parsed.")
        field_issues["points_percent"] = "Could not parse."

    # Loan amount
    if loan_amount is not None:
        try:
            la = float(loan_amount)
            if la <= 0:
                blocks.append("Loan amount must be greater than zero.")
                field_issues["loan_amount"] = "Must be > 0."
        except (TypeError, ValueError):
            blocks.append("Loan amount is invalid.")
            field_issues["loan_amount"] = "Invalid value."

    # Purchase price
    if purchase_price is not None:
        try:
            pp = float(purchase_price)
            if pp <= 0:
                blocks.append("Purchase price must be greater than zero.")
                field_issues["purchase_price"] = "Must be > 0."
            elif loan_amount is not None and float(loan_amount) > pp:
                blocks.append(
                    f"Loan amount (${loan_amount:,.0f}) exceeds purchase price (${pp:,.0f})."
                )
                field_issues["loan_amount"] = "Exceeds purchase price."
        except (TypeError, ValueError):
            pass

    # Cash to close vs purchase price
    if estimated_cash_to_close is not None and purchase_price is not None:
        try:
            ctc = float(estimated_cash_to_close)
            pp = float(purchase_price)
            if pp > 0 and ctc > pp * 1.25:
                blocks.append(
                    f"Estimated cash to close (${ctc:,.0f}) exceeds 125% of purchase price. "
                    "This looks like a data error."
                )
                field_issues["estimated_cash_to_close"] = "Unusually high — verify."
        except (TypeError, ValueError):
            pass

    # Amortization vs loan term
    try:
        at = int(amortization_months)
        lt = int(loan_term_months)
        if at < lt:
            blocks.append(
                f"Amortization ({at} months) is shorter than loan term ({lt} months). "
                "This is only valid for balloon products. Override if intentional."
            )
            field_issues["amortization_months"] = "Shorter than loan term."
    except (TypeError, ValueError):
        blocks.append("Loan term or amortization is invalid.")

    # Lender name junk check
    lender = (lender_name or "").strip()
    bad_lender_patterns = [
        "charges", "smart fees", "title charges", "lender charges",
        "fee", "closing", "escrow",
    ]
    if not lender:
        warnings.append("Lender name is missing. Consider adding it before saving.")
        field_issues["lender_name"] = "Missing."
    elif len(lender) < 3 or any(p in lender.lower() for p in bad_lender_patterns):
        blocks.append(
            f"Lender name '{lender}' looks like a fee label, not a real lender name. "
            "This is likely a parsing error."
        )
        field_issues["lender_name"] = "Looks like a misparse."

    # Title fee range
    try:
        tf = float(title_fee)
        if tf > 10_000:
            warnings.append(
                f"Title fee (${tf:,.0f}) is unusually high. Confirm this is correct."
            )
            field_issues["title_fee"] = "Unusually high — verify."
    except (TypeError, ValueError):
        pass

    # Lender credit range
    try:
        lc = float(lender_credit)
        if lc > 25_000:
            warnings.append(
                f"Lender credit (${lc:,.0f}) exceeds $25,000. "
                "This may indicate a parsing error where credit was confused with loan amount."
            )
            field_issues["lender_credit"] = "Unusually high — verify."
    except (TypeError, ValueError):
        pass

    # ── Soft warnings ────────────────────────────────────────────────────────

    # Program name
    if not (program_name or "").strip():
        warnings.append("Program name is missing.")
        field_issues["program_name"] = "Missing."

    # Prepay type
    if not prepay_type:
        warnings.append("Prepay type is not set. Defaulted to 'declining'.")
        field_issues["prepay_type"] = "Missing — defaulted."

    # Appraisal unusually high
    try:
        af = float(appraisal_fee)
        if af > 2_500:
            warnings.append(
                f"Appraisal fee (${af:,.0f}) is unusually high for a standard appraisal."
            )
            field_issues["appraisal_fee"] = "Higher than typical — verify."
    except (TypeError, ValueError):
        pass

    # Underwriting unusually high
    try:
        uf = float(underwriting_fee)
        if uf > 3_500:
            warnings.append(
                f"Underwriting fee (${uf:,.0f}) is unusually high."
            )
            field_issues["underwriting_fee"] = "Higher than typical — verify."
    except (TypeError, ValueError):
        pass

    # Multiple defaulted fields — sign of a broken parse
    if num_defaulted_fields >= 4 and import_source not in ("manual", "csv"):
        warnings.append(
            f"{num_defaulted_fields} fields were set to default values during extraction. "
            "Review each field carefully — the parser may not have read the document correctly."
        )

    # ── Compute final status ─────────────────────────────────────────────────
    if blocks:
        status = "block"
    elif warnings:
        status = "warn"
    else:
        status = "pass"

    return ValidationResult(
        status=status,
        hard_blocks=blocks,
        warnings=warnings,
        field_issues=field_issues,
    )


def count_defaulted_fields(fields: dict, defaults: dict) -> int:
    """Return how many fields match their default values exactly."""
    count = 0
    for key, default_val in defaults.items():
        if fields.get(key) == default_val:
            count += 1
    return count
