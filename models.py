from typing import Optional
from pydantic import BaseModel, Field


class Deal(BaseModel):
    id: Optional[int] = None
    deal_name: str
    property_address: Optional[str] = None
    purchase_price: float
    down_payment_percent: float = Field(default=25.0)
    monthly_rent: float
    annual_taxes: float
    annual_insurance: float
    hold_months: int = 60
    refinance_probability: float = 0.30
    objective_mode: str = "balanced"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class LoanScenario(BaseModel):
    id: Optional[int] = None
    deal_id: int
    lender_name: str
    program_name: str = "DSCR 30yr"
    rate_percent: float
    points_percent: float = 0.0
    loan_term_months: int = 360
    amortization_months: int = 360
    interest_only_months: int = 0
    prepay_type: str = "declining"
    prepay_months: int = 60
    underwriting_fee: float = 1295.0
    processing_fee: float = 895.0
    appraisal_fee: float = 750.0
    title_fee: float = 1800.0
    reserve_months: int = 0
    escrow_months: int = 0
    lender_credit: float = 0.0
    notes: str = ""
    source_type: str = "manual"   # manual | extracted | ai_extracted
    source_text: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
