from __future__ import annotations
import sqlite3
from models import Deal, LoanScenario
from config import DB_PATH


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_name TEXT NOT NULL,
            property_address TEXT,
            purchase_price REAL NOT NULL,
            down_payment_percent REAL NOT NULL DEFAULT 25.0,
            monthly_rent REAL NOT NULL,
            annual_taxes REAL NOT NULL,
            annual_insurance REAL NOT NULL,
            hold_months INTEGER NOT NULL DEFAULT 60,
            refinance_probability REAL NOT NULL DEFAULT 0.30,
            objective_mode TEXT NOT NULL DEFAULT 'balanced',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS loan_scenarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id INTEGER NOT NULL,
            lender_name TEXT NOT NULL,
            program_name TEXT NOT NULL DEFAULT 'DSCR 30yr',
            rate_percent REAL NOT NULL,
            points_percent REAL NOT NULL DEFAULT 0.0,
            loan_term_months INTEGER NOT NULL DEFAULT 360,
            amortization_months INTEGER NOT NULL DEFAULT 360,
            interest_only_months INTEGER NOT NULL DEFAULT 0,
            prepay_type TEXT NOT NULL DEFAULT 'declining',
            prepay_months INTEGER NOT NULL DEFAULT 60,
            underwriting_fee REAL NOT NULL DEFAULT 1295.0,
            processing_fee REAL NOT NULL DEFAULT 895.0,
            appraisal_fee REAL NOT NULL DEFAULT 750.0,
            title_fee REAL NOT NULL DEFAULT 1800.0,
            reserve_months INTEGER NOT NULL DEFAULT 0,
            escrow_months INTEGER NOT NULL DEFAULT 0,
            lender_credit REAL NOT NULL DEFAULT 0.0,
            notes TEXT DEFAULT '',
            source_type TEXT DEFAULT 'manual',
            source_text TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(deal_id) REFERENCES deals(id) ON DELETE CASCADE
        )
    """)
    # Safe migrations for any existing DB
    _safe_add_columns(cur, "deals", {
        "created_at": "TEXT DEFAULT CURRENT_TIMESTAMP",
        "updated_at": "TEXT DEFAULT CURRENT_TIMESTAMP",
    })
    _safe_add_columns(cur, "loan_scenarios", {
        "source_type": "TEXT DEFAULT 'manual'",
        "source_text": "TEXT DEFAULT ''",
        "created_at": "TEXT DEFAULT CURRENT_TIMESTAMP",
        "updated_at": "TEXT DEFAULT CURRENT_TIMESTAMP",
    })
    conn.commit()
    conn.close()


def _safe_add_columns(cur, table: str, cols: dict) -> None:
    cur.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cur.fetchall()}
    for col, typedef in cols.items():
        if col not in existing:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}")


# ── Deal CRUD ─────────────────────────────────────────────────────────────────

def list_deals() -> list[Deal]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM deals ORDER BY updated_at DESC, id DESC").fetchall()
    conn.close()
    return [Deal(**dict(r)) for r in rows]


def get_deal(deal_id: int) -> Deal | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)).fetchone()
    conn.close()
    return Deal(**dict(row)) if row else None


def upsert_deal(deal: Deal) -> int:
    conn = get_conn()
    cur = conn.cursor()
    d = deal.model_dump(exclude={"created_at", "updated_at"})
    if deal.id:
        cur.execute("""
            UPDATE deals SET deal_name=?, property_address=?, purchase_price=?,
            down_payment_percent=?, monthly_rent=?, annual_taxes=?, annual_insurance=?,
            hold_months=?, refinance_probability=?, objective_mode=?,
            updated_at=CURRENT_TIMESTAMP WHERE id=?
        """, (d["deal_name"], d.get("property_address"), d["purchase_price"],
              d["down_payment_percent"], d["monthly_rent"], d["annual_taxes"],
              d["annual_insurance"], d["hold_months"], d["refinance_probability"],
              d["objective_mode"], deal.id))
        did = deal.id
    else:
        cur.execute("""
            INSERT INTO deals (deal_name, property_address, purchase_price, down_payment_percent,
            monthly_rent, annual_taxes, annual_insurance, hold_months, refinance_probability, objective_mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (d["deal_name"], d.get("property_address"), d["purchase_price"],
              d["down_payment_percent"], d["monthly_rent"], d["annual_taxes"],
              d["annual_insurance"], d["hold_months"], d["refinance_probability"],
              d["objective_mode"]))
        did = cur.lastrowid
    conn.commit()
    conn.close()
    return int(did)


def delete_deal(deal_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM loan_scenarios WHERE deal_id = ?", (deal_id,))
    conn.execute("DELETE FROM deals WHERE id = ?", (deal_id,))
    conn.commit()
    conn.close()


def deal_scenario_count(deal_id: int) -> int:
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) FROM loan_scenarios WHERE deal_id = ?", (deal_id,)).fetchone()
    conn.close()
    return row[0] if row else 0


# ── Scenario CRUD ─────────────────────────────────────────────────────────────

def list_scenarios(deal_id: int) -> list[LoanScenario]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM loan_scenarios WHERE deal_id = ? ORDER BY created_at ASC, id ASC",
        (deal_id,)
    ).fetchall()
    conn.close()
    return [LoanScenario(**dict(r)) for r in rows]


def get_scenario(scenario_id: int) -> LoanScenario | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM loan_scenarios WHERE id = ?", (scenario_id,)).fetchone()
    conn.close()
    return LoanScenario(**dict(row)) if row else None


def upsert_scenario(scenario: LoanScenario) -> int:
    conn = get_conn()
    cur = conn.cursor()
    d = scenario.model_dump(exclude={"created_at", "updated_at"})
    fields = ["deal_id", "lender_name", "program_name", "rate_percent", "points_percent",
              "loan_term_months", "amortization_months", "interest_only_months", "prepay_type",
              "prepay_months", "underwriting_fee", "processing_fee", "appraisal_fee",
              "title_fee", "reserve_months", "escrow_months", "lender_credit", "notes",
              "source_type", "source_text"]
    vals = tuple(d[f] for f in fields)
    if scenario.id:
        set_clause = ", ".join(f"{f}=?" for f in fields) + ", updated_at=CURRENT_TIMESTAMP"
        cur.execute(f"UPDATE loan_scenarios SET {set_clause} WHERE id=?", vals + (scenario.id,))
        sid = scenario.id
    else:
        placeholders = ", ".join("?" * len(fields))
        cur.execute(
            f"INSERT INTO loan_scenarios ({', '.join(fields)}) VALUES ({placeholders})",
            vals
        )
        sid = cur.lastrowid
    conn.commit()
    conn.close()
    return int(sid)


def delete_scenario(scenario_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM loan_scenarios WHERE id = ?", (scenario_id,))
    conn.commit()
    conn.close()


def duplicate_scenario(scenario_id: int) -> int | None:
    s = get_scenario(scenario_id)
    if not s:
        return None
    clone = s.model_copy(deep=True)
    clone.id = None
    clone.lender_name = f"{clone.lender_name} (Copy)"
    clone.source_type = "manual"
    return upsert_scenario(clone)
