from pathlib import Path

# Paths
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "dscr_engine.db"
EXPORT_DIR = BASE_DIR / "exports"
UPLOAD_DIR = BASE_DIR / "uploads"
EXPORT_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)

# Fee defaults (used when fields are left blank)
FEE_DEFAULTS = {
    "underwriting_fee": 1295.0,
    "processing_fee": 895.0,
    "appraisal_fee": 750.0,
    "title_fee": 1800.0,
}

# Scoring weights per objective mode
# Positive weight = higher is better; negative = lower is better
SCORING_WEIGHTS = {
    "balanced": {
        "dscr": 40.0,
        "flexibility": 0.30,
        "payment": -0.025,
        "cash": -0.002,
        "hold_fit": 0.45,
        "prepay_risk": -0.04,
    },
    "best_long_hold": {
        "dscr": 55.0,
        "flexibility": 0.15,
        "payment": -0.020,
        "cash": -0.002,
        "hold_fit": 0.20,
        "prepay_risk": -0.02,
    },
}

OBJECTIVE_MODES = [
    "balanced",
    "best_long_hold",
    "lowest_payment",
    "lowest_cash_to_close",
    "highest_flexibility",
]

OBJECTIVE_MODE_LABELS = {
    "balanced": "Balanced — mixed DSCR, flexibility, payment, and cash",
    "best_long_hold": "Best long hold — higher DSCR weight, accepts longer prepay",
    "lowest_payment": "Lowest payment — cash-flow-first",
    "lowest_cash_to_close": "Lowest cash to close — liquidity preservation",
    "highest_flexibility": "Highest flexibility — values optionality and early exit",
}

PREPAY_TYPES = ["declining", "flat", "none"]

APP_TITLE = "DSCR Loan Option Engine"
APP_VERSION = "2.7.2"
