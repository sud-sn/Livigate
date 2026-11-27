"""Rules engine: deterministic business logic driven by the catalog.

Everything a decision depends on is computed here, in code, from catalog parameters — never by the LLM. Each
result carries the inputs it used (``computed``) so the UI can show where a number came from and the AI can
explain it without recomputing it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from levigate.models import WorkItemType

# ---------------------------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------------------------


def to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def to_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def add_business_days(start: date, days: int) -> date:
    current = start
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def fmt_day(d: date) -> str:
    return f"{d.day} {d:%b}"


# ---------------------------------------------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PayerRules:
    payment_window_days: int = 14
    underpay_pct: float = 0.05
    underpay_min_amount: float = 10.0
    refill_pct: float = 0.8

    @classmethod
    def from_body(cls, body: Mapping[str, Any] | None) -> PayerRules:
        body = body or {}
        return cls(
            payment_window_days=int(body.get("payment_window_days", 14)),
            underpay_pct=float(body.get("underpay_pct", 0.05)),
            underpay_min_amount=float(body.get("underpay_min_amount", 10)),
            refill_pct=float(body.get("refill_pct", 0.8)),
        )


CLAIM_STATUSES = ("paid", "underpaid", "pending", "rejected", "reversed", "unpaid_past_due")


def shortfall(row: Mapping[str, Any]) -> float:
    return round(float(row.get("EXPECTED_AMOUNT") or 0) - float(row.get("PAID_AMOUNT") or 0), 2)


def derive_status(row: Mapping[str, Any], payer: PayerRules, today: date) -> str:
    """CT-01/02/03: the one place a claim's status is decided."""
    response = row.get("RESPONSE_STATUS")
    if response == "R":
        return "rejected"
    if response == "X":
        return "reversed"
    if response == "P":
        short = shortfall(row)
        expected = float(row.get("EXPECTED_AMOUNT") or 0)
        if short >= payer.underpay_min_amount and short > expected * payer.underpay_pct:
            return "underpaid"
        return "paid"
    # Captured (accepted, not remitted) or no response yet.
    age = (today - to_date(row["FILL_DATE"])).days
    return "unpaid_past_due" if age > payer.payment_window_days else "pending"


WORK_TYPE_FOR_STATUS = {
    "rejected": WorkItemType.CLAIM_REJECTED,
    "unpaid_past_due": WorkItemType.CLAIM_UNPAID,
    "underpaid": WorkItemType.CLAIM_UNDERPAID,
}


def amount_at_risk(row: Mapping[str, Any], status: str) -> float:
    if status == "underpaid":
        return shortfall(row)
    return round(float(row.get("EXPECTED_AMOUNT") or 0), 2)


@dataclass
class Fix:
    action: str
    summary: str
    due_date: date
    owner_role: str
    approve_label: str
    steps: list[str]
    computed: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["due_date"] = self.due_date.isoformat()
        return d


def _last_dispensed_before(prior_fills: Sequence[Mapping[str, Any]], rx: str, fill_no: int) -> Mapping[str, Any] | None:
    candidates = [
        f
        for f in prior_fills
        if str(f["RX_NUMBER"]) == rx
        and int(f["FILL_NUMBER"]) < fill_no
        and f.get("STATUS") in ("picked_up", "ready_for_pickup")
    ]
    return max(candidates, key=lambda f: int(f["FILL_NUMBER"])) if candidates else None


def playbook_fix(
    entry: Mapping[str, Any],
    row: Mapping[str, Any],
    payer: PayerRules,
    today: date,
    prior_fills: Sequence[Mapping[str, Any]] = (),
) -> Fix:
    """Apply a reject-code playbook entry to one rejected claim (IF-01 / IF-02)."""
    handler = entry["handler"]
    owner = entry.get("owner_role", "billing")
    params = entry.get("params", {})

    if handler == "refill_too_soon":
        last = _last_dispensed_before(prior_fills, str(row["RX_NUMBER"]), int(row["FILL_NUMBER"]))
        if last is None:
            return Fix(
                "verify_fill_history",
                "Check fill history before resubmitting",
                today,
                owner,
                "Approve review task",
                ["No earlier dispensed fill was found for this Rx; confirm the fill history in the PMS."],
                {"reason": "no_prior_dispensed_fill"},
            )
        pct = payer.refill_pct or float(params.get("default_refill_pct", 0.8))
        last_date = to_date(last["FILL_DATE"])
        days_supply = int(last["DAYS_SUPPLY"])
        wait_days = math.ceil(days_supply * pct)
        eligible = last_date + timedelta(days=wait_days)
        resubmit = max(eligible, today)
        summary = "Resubmit today — now eligible" if eligible <= today else f"Resubmit on {fmt_day(eligible)}"
        return Fix(
            "resubmit_on_date",
            summary,
            resubmit,
            owner,
            "Approve resubmission",
            [
                f"Last fill {fmt_day(last_date)} covered {days_supply} days.",
                f"Plan allows a refill after {round(pct * 100)}% of the days supply ({wait_days} days).",
                f"Resubmit on or after {fmt_day(eligible)}; a task is scheduled for that date.",
            ],
            {
                "last_fill_number": int(last["FILL_NUMBER"]),
                "last_fill_date": last_date.isoformat(),
                "last_days_supply": days_supply,
                "refill_pct": pct,
                "eligible_date": eligible.isoformat(),
                "days_until_eligible": (eligible - today).days,
            },
        )
    if handler == "prior_auth":
        follow = add_business_days(today, int(params.get("followup_days", 2)))
        return Fix(
            "request_prior_auth",
            "Start prior authorization with the prescriber",
            today,
            owner,
            "Approve PA request",
            [
                "Send the PA request to the prescriber today (diagnosis, therapies tried, medical necessity).",
                f"Follow up on {fmt_day(follow)} if there is no decision.",
                "Resubmit once the PA is approved.",
            ],
            {"followup_date": follow.isoformat()},
        )
    if handler == "plan_limit":
        limit = int(params.get("plan_max_days_supply", 30))
        days_supply = int(row.get("DAYS_SUPPLY") or 0)
        if days_supply > limit:
            return Fix(
                "resubmit_adjusted",
                f"Resubmit at a {limit}-day supply (plan limit)",
                today,
                owner,
                "Approve adjusted resubmission",
                [
                    f"Billed {days_supply} days; the plan allows {limit}.",
                    "Confirm the change with the prescriber if the directions require it.",
                    f"Resubmit with a {limit}-day supply.",
                ],
                {"days_supply": days_supply, "plan_max_days_supply": limit},
            )
        return Fix(
            "request_override",
            "Request a quantity-limit override",
            add_business_days(today, 1),
            owner,
            "Approve override request",
            [
                "Quantity is within the days-supply limit; the plan's quantity limit applies.",
                "Request a quantity-limit override from the plan, or a PA through the prescriber.",
            ],
            {"days_supply": days_supply, "plan_max_days_supply": limit},
        )
    if handler == "not_covered":
        return Fix(
            "request_formulary_exception",
            "Ask the prescriber for a formulary exception",
            add_business_days(today, 2),
            owner,
            "Approve exception request",
            [
                "Send the prescriber a formulary-exception request with the medical-necessity letter.",
                "If the patient asks, quote the cash price. Do not suggest a therapy switch to the patient.",
            ],
        )
    if handler == "coverage_lookup":
        return Fix(
            "eligibility_check",
            "Run an eligibility check and update coverage",
            today,
            owner,
            "Approve coverage update",
            ["Run an E1 eligibility check for active coverage.", "Update the plan on file and resubmit."],
        )
    if handler == "cob_primary":
        return Fix(
            "bill_primary_first",
            "Bill the primary payer, then resubmit as secondary",
            add_business_days(today, 1),
            owner,
            "Approve COB resubmission",
            ["Submit to the primary payer first.", "Resubmit to this plan as secondary with the COB segment."],
        )
    if handler == "verify_prescriber":
        return Fix(
            "verify_prescriber_id",
            "Verify the prescriber NPI and resubmit",
            today,
            owner,
            "Approve resubmission",
            ["Check the prescriber's NPI against NPPES.", "Correct the prescriber ID on the claim and resubmit."],
            {"prescriber_id": row.get("PRESCRIBER_ID")},
        )
    if handler == "dur_review":
        return Fix(
            "pharmacist_dur_review",
            "Pharmacist DUR review",
            today,
            "pharmacist",
            "Approve DUR override",
            [
                "Review the DUR conflict.",
                "If clinically appropriate, resubmit with DUR/PPS intervention codes; otherwise call the prescriber.",
            ],
        )
    raise ValueError(f"unknown playbook handler '{handler}'")


def unpaid_fix(row: Mapping[str, Any], payer: PayerRules, today: date) -> Fix:
    age = (today - to_date(row["FILL_DATE"])).days
    return Fix(
        "payer_followup",
        "Check claim status with the payer",
        today,
        "billing",
        "Log payer follow-up",
        [
            f"No payment {age} days after fill; this payer's window is {payer.payment_window_days} days.",
            "Call or check the payer portal for claim status.",
            "If the payer has no record, resubmit; if pending, record the expected payment date.",
        ],
        {"days_since_fill": age, "payment_window_days": payer.payment_window_days},
    )


def underpaid_fix(row: Mapping[str, Any], payer: PayerRules, today: date) -> Fix:
    short = shortfall(row)
    return Fix(
        "underpayment_appeal",
        f"Appeal underpayment of ${short:,.2f}",
        add_business_days(today, 5),
        "billing",
        "Approve appeal",
        [
            f"Paid ${float(row.get('PAID_AMOUNT') or 0):,.2f} against an expected "
            f"${float(row.get('EXPECTED_AMOUNT') or 0):,.2f}.",
            "Compare with the contracted rate; file a reconsideration with the payer.",
        ],
        {
            "expected": float(row.get("EXPECTED_AMOUNT") or 0),
            "paid": float(row.get("PAID_AMOUNT") or 0),
            "shortfall": short,
            "threshold_pct": payer.underpay_pct,
            "threshold_min": payer.underpay_min_amount,
        },
    )


# ---------------------------------------------------------------------------------------------------------------
# Patient outreach (C-09 consent, C-10 refill-reminder scope)
# ---------------------------------------------------------------------------------------------------------------

LIST_TYPES = ("refill_due", "adherence_gap", "ready_for_pickup", "balance_due")


@dataclass
class Eligibility:
    eligible: bool
    reasons: list[str]


def refill_bucket(candidate: Mapping[str, Any], today: date, rule: Mapping[str, Any]) -> tuple[str | None, date]:
    """Which list a prescription belongs on by its due date, or None if it is not due yet / not refillable."""
    due = to_date(candidate["FILL_DATE"]) + timedelta(days=int(candidate["DAYS_SUPPLY"]))
    if int(candidate.get("REFILLS_REMAINING") or 0) <= 0:
        return None, due
    expires = candidate.get("RX_EXPIRES_DATE")
    if expires and to_date(expires) < today:
        return None, due
    overdue = (today - due).days
    if overdue > int(rule.get("lapse_days", 90)):
        return "lapsed", due
    if overdue >= int(rule.get("adherence_overdue_days", 8)):
        return "adherence_gap", due
    if (due - today).days <= int(rule.get("due_within_days", 7)):
        return "refill_due", due
    return None, due


def contact_eligibility(*, consent_granted: bool, opted_out: bool, lapsed: bool, has_phone: bool = True) -> Eligibility:
    reasons = []
    if lapsed:
        reasons.append("Prescription lapsed over 90 days (C-10)")
    if opted_out:
        reasons.append("Patient opted out of calls")
    elif not consent_granted:
        reasons.append("No call consent on file (C-09)")
    if not has_phone:
        reasons.append("No phone number on file — add one in the pharmacy system")
    return Eligibility(not reasons, reasons)


def outreach_priority(
    *,
    due: date,
    revenue: float,
    last_contact: date | None,
    today: date,
    weights: Mapping[str, float],
    max_revenue: float,
) -> tuple[float, dict[str, float]]:
    """PO-02: sort by due date, revenue and days since last contact. Returns the score and its parts."""
    days_to_due = (due - today).days
    urgency = max(0.0, min(1.0, (14 - days_to_due) / 21))  # overdue → 1, due in 2 weeks → ~0
    rev = min(1.0, revenue / max_revenue) if max_revenue > 0 else 0.0
    gap_days = (today - last_contact).days if last_contact else 60
    gap = min(1.0, gap_days / 60)
    parts = {
        "urgency": round(weights.get("urgency", 0.5) * urgency * 100, 1),
        "revenue": round(weights.get("revenue", 0.3) * rev * 100, 1),
        "contact_gap": round(weights.get("contact_gap", 0.2) * gap * 100, 1),
    }
    return round(sum(parts.values()), 1), parts


# ---------------------------------------------------------------------------------------------------------------
# Prescriber scoring (PR-02)
# ---------------------------------------------------------------------------------------------------------------


def score_prescribers(rows: Sequence[Mapping[str, Any]], body: Mapping[str, Any], today: date) -> list[dict[str, Any]]:
    weights = body.get("weights", {"volume": 0.5, "revenue": 0.35, "trend": 0.15})
    max_vol = max((int(r["RX_90"] or 0) for r in rows), default=0) or 1
    max_rev = max((float(r["REV_90"] or 0) for r in rows), default=0.0) or 1.0
    new_cutoff = today - timedelta(days=int(body.get("new_days", 90)))
    out: list[dict[str, Any]] = []
    for r in rows:
        rx90, prior = int(r["RX_90"] or 0), int(r["RX_PRIOR_90"] or 0)
        rev90 = float(r["REV_90"] or 0)
        trend = (rx90 - prior) / prior if prior else (1.0 if rx90 else 0.0)
        parts = {
            "volume": round(weights["volume"] * rx90 / max_vol * 100, 1),
            "revenue": round(weights["revenue"] * rev90 / max_rev * 100, 1),
            "trend": round(weights["trend"] * (max(-1.0, min(1.0, trend)) + 1) / 2 * 100, 1),
        }
        first = to_date(r["FIRST_FILL"]) if r.get("FIRST_FILL") else None
        out.append(
            {
                **dict(r),
                "TREND": round(trend, 4),  # one rounding step only, so every screen shows the same percentage
                "SCORE": round(sum(parts.values()), 1),
                "SCORE_PARTS": parts,
                "IS_NEW": bool(first and first >= new_cutoff),
                "IS_DORMANT": rx90 == 0 and prior > 0,
                "IS_DECLINING": prior >= int(body.get("declining_min_prior", 6))
                and trend <= -float(body.get("declining_drop", 0.3)),
            }
        )
    # Equal scores rank by prescriber ID, so the top-N cut never depends on source row order.
    ranked = sorted((p for p in out if int(p["RX_90"] or 0) > 0), key=lambda p: (-p["SCORE"], str(p["PRESCRIBER_ID"])))
    top_ids = {p["PRESCRIBER_ID"] for p in ranked[: int(body.get("top_n", 25))]}
    for p in out:
        p["IS_TOP"] = p["PRESCRIBER_ID"] in top_ids
    return out
