from __future__ import annotations
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from math import pow
from models import Deal, LoanScenario


def loan_amount(purchase_price: float, down_payment_percent: float) -> float:
    return purchase_price * (1 - down_payment_percent / 100)


def monthly_rate(rate_percent: float) -> float:
    return rate_percent / 100 / 12


def amortized_payment(principal: float, annual_rate_percent: float, amortization_months: int) -> float:
    r = monthly_rate(annual_rate_percent)
    if r == 0:
        return principal / amortization_months if amortization_months > 0 else 0
    return principal * (r * pow(1 + r, amortization_months)) / (pow(1 + r, amortization_months) - 1)


def monthly_pi(principal: float, scenario: LoanScenario) -> float:
    """P&I payment, respecting IO period."""
    if scenario.interest_only_months > 0:
        return principal * monthly_rate(scenario.rate_percent)
    return amortized_payment(principal, scenario.rate_percent, scenario.amortization_months)


def monthly_ti(deal: Deal) -> float:
    return (deal.annual_taxes + deal.annual_insurance) / 12


def monthly_total_payment(deal: Deal, scenario: LoanScenario) -> float:
    principal = loan_amount(deal.purchase_price, deal.down_payment_percent)
    return monthly_pi(principal, scenario) + monthly_ti(deal)


def dscr(deal: Deal, scenario: LoanScenario) -> float:
    debt_service = monthly_pi(loan_amount(deal.purchase_price, deal.down_payment_percent), scenario)
    if debt_service == 0:
        return 0.0
    return deal.monthly_rent / debt_service


def points_cost(deal: Deal, scenario: LoanScenario) -> float:
    return loan_amount(deal.purchase_price, deal.down_payment_percent) * (scenario.points_percent / 100)


def reserve_cash(deal: Deal, scenario: LoanScenario) -> float:
    return monthly_total_payment(deal, scenario) * scenario.reserve_months


def escrow_cash(deal: Deal, scenario: LoanScenario) -> float:
    return monthly_ti(deal) * scenario.escrow_months


def total_cash_to_close(deal: Deal, scenario: LoanScenario) -> float:
    down = deal.purchase_price * (deal.down_payment_percent / 100)
    fees = (
        points_cost(deal, scenario)
        + scenario.underwriting_fee
        + scenario.processing_fee
        + scenario.appraisal_fee
        + scenario.title_fee
        + reserve_cash(deal, scenario)
        + escrow_cash(deal, scenario)
        - scenario.lender_credit
    )
    return down + fees


def prepay_flexibility_score(scenario: LoanScenario) -> float:
    """0–100: higher = more flexible / less prepay risk."""
    if scenario.prepay_months <= 0 or scenario.prepay_type == "none":
        return 100.0
    return max(0.0, 100.0 - min(90.0, scenario.prepay_months * 1.2 + scenario.points_percent * 6))


def hold_period_alignment_score(deal: Deal, scenario: LoanScenario) -> float:
    """
    0–100: higher = better alignment between expected hold and prepay window.
    If hold_months > prepay_months by a comfortable margin, score is high.
    If hold is inside the prepay window, score is penalized.
    """
    if scenario.prepay_months <= 0 or scenario.prepay_type == "none":
        return 100.0
    overlap = max(0, scenario.prepay_months - deal.hold_months)
    exposure_ratio = overlap / max(scenario.prepay_months, 1)
    return round(max(0.0, 100.0 - exposure_ratio * 80.0), 1)


def estimated_prepay_cost(deal: Deal, scenario: LoanScenario) -> float:
    """
    Rough expected cost of prepayment exposure.
    Estimated as: overlap_months * monthly_payment * refi_probability * flat penalty factor.
    Returns dollar estimate; 0 if no overlap or no-prepay.
    """
    if scenario.prepay_months <= 0 or scenario.prepay_type == "none":
        return 0.0
    overlap = max(0, scenario.prepay_months - deal.hold_months)
    if overlap == 0:
        return 0.0
    principal = loan_amount(deal.purchase_price, deal.down_payment_percent)
    # Typical declining prepay = avg ~2% of balance during overlap
    penalty_rate = 0.02 if scenario.prepay_type == "declining" else 0.03
    raw = principal * penalty_rate * deal.refinance_probability * (overlap / scenario.prepay_months)
    return round(raw, 2)


def breakeven_months(deal: Deal, cheaper: LoanScenario, flexible: LoanScenario) -> float | None:
    """
    How many months until monthly savings from the cheaper option
    overcome its higher upfront cash-to-close.
    Returns None if cheaper option doesn't save money monthly.
    """
    cheaper_cash = total_cash_to_close(deal, cheaper)
    flexible_cash = total_cash_to_close(deal, flexible)
    cheaper_pmt = monthly_total_payment(deal, cheaper)
    flexible_pmt = monthly_total_payment(deal, flexible)
    monthly_savings = flexible_pmt - cheaper_pmt
    upfront_gap = flexible_cash - cheaper_cash
    if monthly_savings <= 0 or upfront_gap <= 0:
        return None
    return round(upfront_gap / monthly_savings, 1)


def cash_to_close_breakdown(deal: Deal, scenario: LoanScenario) -> dict:
    """Return itemized breakdown for display."""
    down = deal.purchase_price * (deal.down_payment_percent / 100)
    return {
        "Down payment": round(down, 2),
        "Points": round(points_cost(deal, scenario), 2),
        "Underwriting fee": scenario.underwriting_fee,
        "Processing fee": scenario.processing_fee,
        "Appraisal fee": scenario.appraisal_fee,
        "Title / settlement": scenario.title_fee,
        "Reserves": round(reserve_cash(deal, scenario), 2),
        "Escrow pre-fund": round(escrow_cash(deal, scenario), 2),
        "Lender credit": -scenario.lender_credit,
    }
