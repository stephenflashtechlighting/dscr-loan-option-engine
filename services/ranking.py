from __future__ import annotations
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SCORING_WEIGHTS
from models import Deal, LoanScenario
from services.calculations import (
    monthly_total_payment,
    total_cash_to_close,
    dscr,
    prepay_flexibility_score,
    hold_period_alignment_score,
    estimated_prepay_cost,
)


def score_scenario(
    deal: Deal,
    scenario: LoanScenario,
    objective_mode: str,
    weight_overrides: dict | None = None,
) -> dict:
    payment = monthly_total_payment(deal, scenario)
    cash = total_cash_to_close(deal, scenario)
    coverage = dscr(deal, scenario)
    flexibility = prepay_flexibility_score(scenario)
    hold_fit = hold_period_alignment_score(deal, scenario)
    prepay_risk = estimated_prepay_cost(deal, scenario)

    if objective_mode == "lowest_payment":
        score = -payment
    elif objective_mode == "lowest_cash_to_close":
        score = -cash
    elif objective_mode == "highest_flexibility":
        score = flexibility * 1.5 + hold_fit * 0.5 - prepay_risk * 0.03
    else:
        mode_key = "best_long_hold" if objective_mode == "best_long_hold" else "balanced"
        w = weight_overrides if weight_overrides else SCORING_WEIGHTS[mode_key]
        score = (
            coverage * w["dscr"]
            + flexibility * w["flexibility"]
            + payment * w["payment"]
            + cash * w["cash"]
            + hold_fit * w.get("hold_fit", 0.45)
            + prepay_risk * w.get("prepay_risk", -0.04)
        )

    return {
        "scenario": scenario,
        "score": round(score, 2),
        "monthly_total": round(payment, 2),
        "cash_to_close": round(cash, 2),
        "dscr": round(coverage, 3),
        "flexibility": round(flexibility, 1),
        "hold_fit": round(hold_fit, 1),
        "prepay_risk": round(prepay_risk, 2),
    }


def rank_scenarios(
    deal: Deal,
    scenarios: list[LoanScenario],
    objective_mode: str,
    weight_overrides: dict | None = None,
) -> list[dict]:
    ranked = [score_scenario(deal, s, objective_mode, weight_overrides) for s in scenarios]
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked


def explain_recommendation(ranked: list[dict], objective_mode: str) -> str:
    """Return a plain-English explanation of the top recommendation."""
    if not ranked:
        return "No scenarios to rank."
    top = ranked[0]
    s = top["scenario"]
    runner = ranked[1] if len(ranked) > 1 else None

    lines = []
    lines.append(
        f"**{s.lender_name} / {s.program_name}** ranks first under the **{objective_mode}** objective."
    )

    if objective_mode == "lowest_payment":
        lines.append(f"It has the lowest total monthly payment at **${top['monthly_total']:,.2f}**.")
    elif objective_mode == "lowest_cash_to_close":
        lines.append(f"It requires the least cash to close at **${top['cash_to_close']:,.2f}**.")
    elif objective_mode == "highest_flexibility":
        lines.append(
            f"It scores highest on prepayment flexibility ({top['flexibility']:.0f}/100) "
            f"and hold-period alignment ({top['hold_fit']:.0f}/100)."
        )
    else:
        lines.append(
            f"It balances DSCR ({top['dscr']:.3f}), flexibility ({top['flexibility']:.0f}/100), "
            f"monthly payment (${top['monthly_total']:,.2f}), and cash to close (${top['cash_to_close']:,.2f})."
        )
        if s.prepay_months > 0 and top["prepay_risk"] > 0:
            lines.append(
                f"Estimated prepay risk exposure is **${top['prepay_risk']:,.0f}** given your hold period and refinance probability."
            )

    if runner:
        s2 = runner["scenario"]
        score_gap = round(top["score"] - runner["score"], 2)
        lines.append(
            f"The runner-up is **{s2.lender_name} / {s2.program_name}** "
            f"(score gap: {score_gap}). "
            f"It carries {'higher' if runner['monthly_total'] > top['monthly_total'] else 'lower'} monthly costs "
            f"and {'more' if runner['flexibility'] > top['flexibility'] else 'less'} prepay flexibility."
        )

    return " ".join(lines)
