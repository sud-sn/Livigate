"""Synthetic client warehouses for the demo and for tests. 100% fictional: no real patients, prescribers or payers.

One simulated pharmacy world (`_Builder`) is written in either of two physical layouts:

* ``canonical`` — Levigate's reference layout (DIM_* / FACT_* relations). The connector reads it as-is.
* ``pms`` — a realistic layout of a pharmacy management system landed in Snowflake by Talend: normalised tables
  (patients, phones, insurance, consent, prescribers, addresses, payers, plans, contracts, formulas, ingredients,
  prescriptions, fills, claim transactions, rejects, 835 remittances), client BI views, and the mess real data has
  (reload duplicates, soft deletes, orphan claims, secondary-payer claims, extra reject codes, split remittances,
  upper-case names, mixed phone formats, an expired contract next to the current one). The ``pms_snowflake``
  source profile maps it back onto the canonical relations.

Because both layouts come from the same world, everything Levigate computes must be identical on either — the
equivalence tests rely on that. Either layout can be written ``as_of`` an earlier date (what the warehouse held that
day), which the incremental-sync tests use. Phone numbers use the reserved 555-01xx range; generation is
deterministic for a given seed and "today".
"""

from __future__ import annotations

import math
import random
import sqlite3
import string
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

LAYOUTS = ("canonical", "pms")

# ---------------------------------------------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------------------------------------------

PAYERS = [
    # id, name, plan type, remit lag mean, sd, base reject rate
    ("PY01", "Northwind Health Plan", "Commercial", 10, 3, 0.08),
    ("PY02", "Bluewater PBM", "Commercial", 12, 4, 0.10),
    ("PY03", "Summit Rx Benefits", "Commercial", 15, 4, 0.09),
    ("PY04", "Cascade Medicare Part D", "Medicare Part D", 13, 3, 0.11),
    ("PY05", "Harbor State Medicaid", "Medicaid", 24, 10, 0.12),
    ("PY06", "Pioneer Employer Plan", "Commercial", 9, 3, 0.07),
    ("PY07", "Lakeshore Workers' Comp", "Workers' comp", 35, 12, 0.10),
]
PAYER_WEIGHTS = [20, 18, 14, 16, 12, 12, 8]

# Current contract terms per payer: reimbursement = AWP x quantity x rate + dispensing fee, capped at U&C.
CONTRACTS = {
    "PY01": (0.92, 12.50),
    "PY02": (0.95, 10.00),
    "PY03": (0.90, 15.00),
    "PY04": (0.88, 9.50),
    "PY05": (0.85, 8.00),
    "PY06": (0.97, 11.00),
    "PY07": (1.00, 20.00),
}
UC_MARKUP, UC_FEE = 1.18, 15.00  # usual & customary price
# Low-cost formulas on the pharmacy's cash-price list: U&C is below what any contract pays, so the claim's expected
# reimbursement is the U&C (the "lesser of" half of the formula).
CASH_PRICE = {"DR10": (0.80, 6.00), "DR12": (0.80, 6.00)}

DRUGS = [
    # id, name, form, AWP for a 30-unit supply
    ("DR01", "Ketoprofen 10% / Lidocaine 5% cream", "Cream", 185.0),
    ("DR02", "Gabapentin 6% / Baclofen 2% cream", "Cream", 210.0),
    ("DR03", "Progesterone 100 mg SR capsule", "Capsule", 68.0),
    ("DR04", "Bi-Est 2.5 mg/g cream", "Cream", 72.0),
    ("DR05", "Testosterone 2% cream", "Cream", 95.0),
    ("DR06", "Naltrexone 4.5 mg capsule", "Capsule", 58.0),
    ("DR07", "Omeprazole 2 mg/mL oral suspension", "Suspension", 64.0),
    ("DR08", "Tretinoin 0.05% / Niacinamide 4% cream", "Cream", 120.0),
    ("DR09", "Diclofenac 10% / Cyclobenzaprine 2% gel", "Gel", 195.0),
    ("DR10", "Metformin 100 mg/mL oral suspension", "Suspension", 55.0),
    ("DR11", "Hydroquinone 6% / Tretinoin 0.025% cream", "Cream", 110.0),
    ("DR12", "Fluconazole 10 mg/mL oral suspension", "Suspension", 48.0),
]
INGREDIENTS = {
    "DR01": [("Ketoprofen USP", 10.0, "%"), ("Lidocaine HCl USP", 5.0, "%"), ("Lipoderm base", 85.0, "%")],
    "DR02": [("Gabapentin USP", 6.0, "%"), ("Baclofen USP", 2.0, "%"), ("Versapro base", 92.0, "%")],
    "DR03": [("Progesterone micronized USP", 100.0, "mg"), ("Methocel E4M", 50.0, "mg")],
    "DR04": [("Estriol USP", 2.0, "mg/g"), ("Estradiol USP", 0.5, "mg/g"), ("Versabase cream", 1.0, "g")],
    "DR05": [("Testosterone USP", 2.0, "%"), ("Versabase gel", 98.0, "%")],
    "DR06": [("Naltrexone HCl USP", 4.5, "mg"), ("Avicel PH-102", 150.0, "mg")],
    "DR07": [("Omeprazole USP", 2.0, "mg/mL"), ("Sodium bicarbonate 8.4%", 1.0, "mL")],
    "DR08": [("Tretinoin USP", 0.05, "%"), ("Niacinamide USP", 4.0, "%"), ("Cream base", 95.95, "%")],
    "DR09": [("Diclofenac sodium USP", 10.0, "%"), ("Cyclobenzaprine HCl USP", 2.0, "%"), ("PLO gel", 88.0, "%")],
    "DR10": [("Metformin HCl USP", 100.0, "mg/mL"), ("Ora-Sweet", 1.0, "mL")],
    "DR11": [("Hydroquinone USP", 6.0, "%"), ("Tretinoin USP", 0.025, "%"), ("Cream base", 93.975, "%")],
    "DR12": [("Fluconazole USP", 10.0, "mg/mL"), ("Ora-Plus", 1.0, "mL")],
}

SPECIALTIES = {
    "Pain medicine": (["DR01", "DR02", "DR09"], 16),
    "Dermatology": (["DR08", "DR11"], 14),
    "Women's health": (["DR03", "DR04"], 18),
    "Men's health": (["DR05"], 10),
    "Functional medicine": (["DR06", "DR03", "DR04"], 16),
    "Pediatrics": (["DR07", "DR10", "DR12"], 12),
    "Family medicine": (["DR01", "DR03", "DR06", "DR07"], 14),
}

REJECTS = {
    "79": ("Refill Too Soon", 22),
    "75": ("Prior Authorization Required", 18),
    "76": ("Plan Limitations Exceeded", 12),
    "70": ("Product/Service Not Covered", 9),
    "65": ("Patient Is Not Covered", 9),
    "88": ("DUR Reject Error", 7),
    "25": ("M/I Prescriber ID", 7),
    "41": ("Submit Bill To Other Processor Or Primary Payer", 5),
    "MR": ("Product Not On Formulary", 6),
}
UNKNOWN_REJECT = ("AG", "Days Supply Limitation For Product/Service")
# Secondary reject codes a payer may add to a response (NCPDP allows up to five).
SECONDARY_REJECTS = {"75": "88", "76": "AG", "70": "MR", "65": "07", "MR": "70"}
REJECT_REFERENCE = {
    **{k: v[0] for k, v in REJECTS.items()},
    UNKNOWN_REJECT[0]: UNKNOWN_REJECT[1],
    "07": "M/I Cardholder ID",
}

FIRST = (
    "James Mary Robert Patricia John Jennifer Michael Linda David Elizabeth William Barbara Richard Susan Joseph "
    "Jessica Thomas Sarah Charles Karen Daniel Lisa Matthew Nancy Anthony Betty Mark Sandra Donald Ashley Steven "
    "Kimberly Andrew Emily Paul Donna Joshua Michelle Kenneth Carol Kevin Amanda Brian Melissa George Deborah "
    "Timothy Stephanie Ronald Rebecca Jason Laura Edward Sharon Jeffrey Cynthia Ryan Kathleen Jacob Amy Gary Angela "
    "Nicholas Helen Eric Anna Jonathan Brenda Larry Pamela Justin Emma Scott Nicole Brandon Samantha Benjamin "
    "Katherine Samuel Christine Gregory Debra Alexander Rachel Patrick Carolyn Frank Janet Raymond Maria Jack "
    "Olivia Dennis Heather Jerry Diane Tyler Ruth Aaron Julie Jose Joyce Adam Virginia Nathan Victoria Henry Kelly "
    "Zachary Lauren Douglas Christina Peter Joan Kyle Evelyn Noah Judith Ethan Megan Jeremy Andrea Walter Cheryl "
    "Christian Hannah Keith Jacqueline Roger Martha Terry Gloria Austin Teresa Sean Ann Gerald Sara Carl Madison "
    "Harold Frances Dylan Kathryn Arthur Janice Lawrence Jean Jordan Abigail Jesse Alice Bryan Judy Billy Sophia"
).split()
LAST = (
    "Smith Johnson Williams Brown Jones Garcia Miller Davis Rodriguez Martinez Hernandez Lopez Gonzalez Wilson "
    "Anderson Thomas Taylor Moore Jackson Martin Lee Perez Thompson White Harris Sanchez Clark Ramirez Lewis "
    "Robinson Walker Young Allen King Wright Scott Torres Nguyen Hill Flores Green Adams Nelson Baker Hall Rivera "
    "Campbell Mitchell Carter Roberts Gomez Phillips Evans Turner Diaz Parker Cruz Edwards Collins Reyes Stewart "
    "Morris Morales Murphy Cook Rogers Gutierrez Ortiz Morgan Cooper Peterson Bailey Reed Kelly Howard Ramos Kim "
    "Cox Ward Richardson Watson Brooks Chavez Wood James Bennett Gray Mendoza Ruiz Hughes Price Alvarez Castillo "
    "Sanders Patel Myers Long Ross Foster Jimenez Powell Jenkins Perry Russell Sullivan Bell Coleman Butler"
).split()
CITIES = [
    ("Oak Park", "IL"),
    ("Evanston", "IL"),
    ("Naperville", "IL"),
    ("Skokie", "IL"),
    ("Schaumburg", "IL"),
    ("Hammond", "IN"),
    ("Gary", "IN"),
    ("Kenosha", "WI"),
    ("Joliet", "IL"),
    ("Aurora", "IL"),
]
LOCATIONS = ["Main St", "Westside"]
AREA_CODES = [312, 630, 708, 773, 847, 219, 262]

STORY_RX = "4471"
STORY_PRESCRIBER = "PR0007"


def awp_per_unit(drug_id: str) -> float:
    price = next(d[3] for d in DRUGS if d[0] == drug_id)
    return round(price / 30, 4)


def expected_reimbursement(drug_id: str, payer_id: str, quantity: float) -> tuple[float, float]:
    """(expected, usual & customary) for one fill. The PMS profile and the client BI view compute the same thing in
    SQL; keep the operation order identical so results agree to the cent."""
    ingredient = awp_per_unit(drug_id) * quantity
    rate, fee = CONTRACTS[payer_id]
    contract = ingredient * rate + fee
    markup, uc_fee = CASH_PRICE.get(drug_id, (UC_MARKUP, UC_FEE))
    uc = round(ingredient * markup + uc_fee, 2)  # stored rounded; the comparison uses the stored value
    return round(contract if contract < uc else uc, 2), uc


@dataclass
class Story:
    today: date
    rx_4471_patient: str = ""
    rx_4471_fill_id: str = ""
    declining_prescriber: str = STORY_PRESCRIBER
    unknown_code_rx: list[str] = field(default_factory=list)


@dataclass
class World:
    """What the generator produced, for the demo seed and for tests (business keys only)."""

    story: Story
    layout: str
    as_of: date
    # Fills rejected first and paid after a resubmission, within the last nine weeks (seeded as resolved work).
    resolved_history: list[dict[str, Any]] = field(default_factory=list)
    # PMS layout only: how much of each kind of real-world mess was written.
    mess: dict[str, int] = field(default_factory=dict)


def _ts(d: date, rng: random.Random, start_h: int = 9, end_h: int = 17) -> datetime:
    return datetime.combine(d, time(rng.randint(start_h, end_h - 1), rng.randint(0, 59), rng.randint(0, 59)))


def _iso(v: date | datetime | None) -> str | None:
    return v.isoformat() if v is not None else None


def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _phone_digits(rng: random.Random) -> str:
    return f"{rng.choice(AREA_CODES)}5550{rng.randint(100, 199)}"


def _fmt_phone(digits: str) -> str:
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


# ---------------------------------------------------------------------------------------------------------------
# Simulation (layout-independent)
# ---------------------------------------------------------------------------------------------------------------


class _Builder:
    def __init__(self, today: date, seed: int, n_patients: int, n_prescribers: int) -> None:
        self.today = today
        self.seed = seed
        self.rng = random.Random(seed)
        self.start = today - timedelta(days=182)
        self.n_patients = n_patients
        self.n_prescribers = n_prescribers
        self.patients: list[dict[str, Any]] = []
        self.prescribers: list[dict[str, Any]] = []
        self.fills: list[dict[str, Any]] = []
        self.claims: list[dict[str, Any]] = []
        self.consents: list[dict[str, Any]] = []
        self.patient_profile: dict[str, str] = {}
        self.prescriber_profile: dict[str, str] = {}
        self.prescriber_patients: dict[str, list[str]] = {}
        self._rx_seq = 1000
        self._claim_seq = 500_000
        self.story = Story(today=today)

    # --- dimensions -------------------------------------------------------------------------------------------
    def build_dimensions(self) -> None:
        rng = self.rng
        for i in range(1, self.n_patients + 1):
            pid = f"PT{i:05d}"
            dob = date(rng.randint(1940, 2006), rng.randint(1, 12), rng.randint(1, 28))
            first, last = rng.choice(FIRST), rng.choice(LAST)
            mobile = _phone_digits(rng)
            zip_code = f"60{rng.randint(100, 699)}"
            spanish = rng.random() < 0.12
            roll = rng.random()
            # 2% have no phone on file; 30% also have a home line. The mobile is primary.
            home = [("HOME", _phone_digits(rng))] if roll > 0.7 else []
            phones = [] if roll < 0.02 else [("MOBILE", mobile), *home]
            self.patients.append(
                {
                    "PATIENT_ID": pid,
                    "FIRST_NAME": first,
                    "LAST_NAME": last,
                    "DOB": dob.isoformat(),
                    "PHONES": phones,
                    "ZIP": zip_code,
                    "PREFERRED_LANGUAGE": "Spanish" if spanish else "English",
                }
            )
            self.patient_profile[pid] = rng.choices(["good", "fair", "poor"], [60, 25, 15])[0]

        specialties = list(SPECIALTIES)
        weights = [SPECIALTIES[s][1] for s in specialties]
        for i in range(1, self.n_prescribers + 1):
            rid = f"PR{i:04d}"
            spec = rng.choices(specialties, weights)[0]
            first, last = rng.choice(FIRST), rng.choice(LAST)
            profile = rng.choices(["steady", "declining", "growing", "new", "dormant"], [62, 12, 12, 8, 6])[0]
            if rid == STORY_PRESCRIBER:
                spec, first, last, profile = "Pain medicine", "Elena", "Marsh", "story_declining"
            city, state = rng.choice(CITIES)
            practice = (
                "Lakeview Pain & Spine"
                if rid == STORY_PRESCRIBER
                else rng.choice(
                    [f"{last} {string.capwords(spec)}", f"{city} {string.capwords(spec)} Group", f"{last} & Partners"]
                )
            )
            cred = "MD" if spec != "Women's health" or rng.random() < 0.6 else rng.choice(["DO", "NP"])
            self.prescribers.append(
                {
                    "PRESCRIBER_ID": rid,
                    "NPI": f"1{rng.randint(100_000_000, 999_999_999)}",
                    "FIRST_NAME": first,
                    "LAST_NAME": last,
                    "CREDENTIAL": cred,
                    "SPECIALTY": spec,
                    "PRACTICE_NAME": practice,
                    "PHONE": _phone_digits(rng),
                    "FAX": _phone_digits(rng),
                    "EMAIL": f"office@{last.lower()}-practice.example",
                    "CITY": city,
                    "STATE": state,
                }
            )
            self.prescriber_profile[rid] = profile
            self.prescriber_patients[rid] = []

    def build_consents(self) -> None:
        """Contact preferences as the pharmacy system holds them (its own random stream: the claims world is
        unaffected). Most patients consented to calls, fewer to texts, a few opted out, the rest never asked."""
        rng = random.Random(self.seed + 1)
        for p in self.patients:
            pid = p["PATIENT_ID"]
            recorded = datetime.combine(self.today - timedelta(days=rng.randint(90, 500)), time(10, 0))
            roll = rng.random()
            if pid == "PT00042" or roll < 0.84:
                self.consents.append(
                    {
                        "PATIENT_ID": pid,
                        "CHANNEL": "call",
                        "STATUS": "granted",
                        "CONSENT_TYPE": "prior_express",
                        "RECORDED_AT": recorded,
                    }
                )
                if rng.random() < 0.4:
                    self.consents.append(
                        {
                            "PATIENT_ID": pid,
                            "CHANNEL": "sms",
                            "STATUS": "granted",
                            "CONSENT_TYPE": "prior_express_written",
                            "RECORDED_AT": recorded,
                        }
                    )
            elif roll < 0.86:
                for ch in ("call", "sms"):
                    self.consents.append(
                        {
                            "PATIENT_ID": pid,
                            "CHANNEL": ch,
                            "STATUS": "revoked",
                            "CONSENT_TYPE": "opt_out",
                            "RECORDED_AT": recorded,
                        }
                    )

    # --- facts ------------------------------------------------------------------------------------------------
    def _next_rx(self) -> str:
        self._rx_seq += 1
        if str(self._rx_seq) == STORY_RX:
            self._rx_seq += 1
        return str(self._rx_seq)

    def _claim_id(self) -> str:
        self._claim_seq += 1
        return f"CL{self._claim_seq}"

    def build_facts(self) -> None:
        """Simulate 12 months of prescribing so the stored 6-month window is in steady state (no ramp-up)."""
        rng = self.rng
        hist_start = self.start - timedelta(days=180)
        flat = [1.0] * 12
        multipliers = {
            "steady": flat,
            "declining": [1.2] * 9 + [0.4, 0.3, 0.3],
            "story_declining": [2.8] * 9 + [0.5, 0.25, 0.2],
            "growing": [0.4, 0.45, 0.5, 0.55, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2, 1.35, 1.5],
            "new": [0.0] * 9 + [0.9, 1.2, 1.4],
            "dormant": [1.0] * 9 + [0.0, 0.0, 0.0],
        }
        base = {p["PRESCRIBER_ID"]: rng.lognormvariate(0.75, 0.6) for p in self.prescribers}
        base[STORY_PRESCRIBER] = 5.0
        unused = [p["PATIENT_ID"] for p in self.patients]
        rng.shuffle(unused)
        patient_ids = [p["PATIENT_ID"] for p in self.patients]
        for pres in self.prescribers:
            rid = pres["PRESCRIBER_ID"]
            drugs = SPECIALTIES[pres["SPECIALTY"]][0]
            profile = self.prescriber_profile[rid]
            for month in range(12):
                lam = base[rid] * multipliers[profile][month]
                for _ in range(_poisson(rng, lam)):
                    linked = self.prescriber_patients[rid]
                    if linked and rng.random() < 0.35:
                        pid = rng.choice(linked)
                    else:
                        pid = unused.pop() if unused else rng.choice(patient_ids)
                        linked.append(pid)
                    written = hist_start + timedelta(days=month * 30 + rng.randint(0, 29))
                    if written > self.today - timedelta(days=1):
                        continue
                    self._prescription(rid, pid, rng.choice(drugs), written, profile)

        self._story_rx_4471()
        self._story_unknown_code()

    def _prescription(self, rid: str, pid: str, drug_id: str, written: date, profile: str) -> None:
        rng = self.rng
        rx = self._next_rx()
        payer = PAYERS[rng.choices(range(len(PAYERS)), PAYER_WEIGHTS)[0]]
        days_supply = 90 if rng.random() < 0.15 else 30
        refills = 11 if days_supply == 30 and rng.random() < 0.3 else 5
        if days_supply == 90:
            refills = 3
        expires = written + timedelta(days=365)
        adherence = self.patient_profile[pid]
        stop_p = {"good": 0.05, "fair": 0.10, "poor": 0.18}[adherence]
        # In the last ~90 days, declining and dormant prescribers' patients drift away from the pharmacy.
        late_stop = {"declining": 0.3, "story_declining": 0.45, "dormant": 1.0}.get(profile, 0.0)
        late_cutoff = self.today - timedelta(days=92)
        location = rng.choice(LOCATIONS)
        fill_date = written + timedelta(days=rng.randint(0, 2))
        for fill_no in range(refills + 1):
            if fill_date > self.today or fill_date > expires:
                break
            if fill_date >= late_cutoff and rng.random() < late_stop:
                break
            ok = self._fill(
                rx,
                fill_no,
                pid,
                rid,
                drug_id,
                payer,
                location,
                fill_date,
                days_supply,
                refills,
                written,
                expires,
                profile,
            )
            if not ok or rng.random() < stop_p:
                break
            delay = {"good": rng.randint(-3, 4), "fair": rng.randint(0, 15), "poor": rng.randint(4, 40)}[adherence]
            fill_date = fill_date + timedelta(days=days_supply + delay)

    def _fill(
        self,
        rx: str,
        fill_no: int,
        pid: str,
        rid: str,
        drug_id: str,
        payer: tuple,
        location: str,
        fill_date: date,
        days_supply: int,
        refills: int,
        written: date,
        expires: date,
        profile: str,
        *,
        force_reject: tuple[str, str] | None = None,
        force_paid: bool = False,
    ) -> bool:
        """Create one fill and its claim history. Returns False if the fill stalled on an unresolved reject."""
        rng = self.rng
        if fill_date < self.start:  # simulated history before the stored window
            return True
        payer_id, _, _, lag_mean, lag_sd, reject_rate = payer
        quantity = float(days_supply)  # one unit per day for every formula in this pharmacy
        expected, billed = expected_reimbursement(drug_id, payer_id, quantity)
        fill_id = f"F{rx}-{fill_no:02d}"
        submitted = _ts(fill_date, rng)
        age = (self.today - fill_date).days

        if profile == "story_declining":
            reject_rate += 0.28
        rejected = force_reject is not None or (not force_paid and rng.random() < reject_rate)
        stalled = False
        claim_rows: list[dict[str, Any]] = []
        if rejected:
            if force_reject:
                code, message = force_reject
            else:
                pool = {k: v for k, v in REJECTS.items() if not (k == "79" and fill_no == 0)}
                if profile == "story_declining":
                    pool = {**pool, "75": (pool["75"][0], 60)}
                code = rng.choices(list(pool), [v[1] for v in pool.values()])[0]
                message = pool[code][0]
            adjudicated = submitted + timedelta(seconds=rng.randint(2, 40))
            claim_rows.append(
                self._claim(
                    fill_id,
                    rx,
                    fill_no,
                    payer_id,
                    submitted,
                    "R",
                    code,
                    message,
                    billed,
                    expected,
                    None,
                    adjudicated,
                    None,
                )
            )
            if force_reject:
                resolved = False
            elif age > 21:
                resolved = rng.random() < 0.88
            elif age > 3:
                resolved = rng.random() < 0.3
            else:
                resolved = False
            if resolved:
                submitted = submitted + timedelta(days=rng.randint(2, 9), hours=rng.randint(0, 5))
            elif age > 60 and rng.random() < 0.75:
                # Old rejections nobody fixed: the pharmacy reversed the claim and wrote it off.
                reversed_at = submitted + timedelta(days=rng.randint(20, 45))
                claim_rows.append(
                    self._claim(
                        fill_id,
                        rx,
                        fill_no,
                        payer_id,
                        reversed_at,
                        "X",
                        None,
                        None,
                        billed,
                        expected,
                        None,
                        reversed_at,
                        None,
                    )
                )
                stalled = True
            else:
                stalled = True

        if not stalled:
            if age <= 1 and not force_paid and rng.random() < 0.35:
                claim_rows.append(
                    self._claim(
                        fill_id, rx, fill_no, payer_id, submitted, None, None, None, billed, expected, None, None, None
                    )
                )
            else:
                adjudicated = submitted + timedelta(seconds=rng.randint(2, 40))
                lag = max(3, round(rng.gauss(lag_mean, lag_sd)))
                lost = not force_paid and rng.random() < 0.015
                remitted = None if lost else datetime.combine(submitted.date() + timedelta(days=lag), time(6, 0))
                if remitted is not None and remitted.date() > self.today:
                    remitted = None
                underpaid = not force_paid and rng.random() < 0.012
                paid = round(expected * rng.uniform(0.55, 0.85), 2) if underpaid else expected
                claim_rows.append(
                    self._claim(
                        fill_id,
                        rx,
                        fill_no,
                        payer_id,
                        submitted,
                        "P" if remitted else "C",
                        None,
                        None,
                        billed,
                        expected,
                        paid,
                        adjudicated,
                        remitted,
                    )
                )

        if stalled:
            fill_status = "on_hold"
        elif age <= 4 and not force_paid and rng.random() < 0.5:
            fill_status = "ready_for_pickup"
        else:
            fill_status = "picked_up"
        balance = round(rng.choice([15, 25, 35, 50, 75]), 2) if rng.random() < 0.035 and not stalled else 0.0
        self.fills.append(
            {
                "FILL_ID": fill_id,
                "RX_NUMBER": rx,
                "FILL_NUMBER": fill_no,
                "PATIENT_ID": pid,
                "PRESCRIBER_ID": rid,
                "DRUG_ID": drug_id,
                "PAYER_ID": payer_id,
                "LOCATION": location,
                "FILL_DATE": _iso(fill_date),
                "DAYS_SUPPLY": days_supply,
                "QUANTITY": quantity,
                "REFILLS_AUTHORIZED": refills,
                "REFILLS_REMAINING": refills - fill_no,
                "RX_WRITTEN_DATE": _iso(written),
                "RX_EXPIRES_DATE": _iso(expires),
                "STATUS": fill_status,
                "PICKUP_READY_DATE": _iso(fill_date + timedelta(days=1)) if fill_status == "ready_for_pickup" else None,
                "PATIENT_BALANCE": balance,
                "EXPECTED_AMOUNT": expected,
                "UC_AMOUNT": billed,
            }
        )
        self.claims.extend(claim_rows)
        return not stalled

    def _claim(
        self,
        fill_id: str,
        rx: str,
        fill_no: int,
        payer_id: str,
        submitted: datetime,
        status: str | None,
        code: str | None,
        message: str | None,
        billed: float,
        expected: float,
        paid: float | None,
        adjudicated: datetime | None,
        remitted: datetime | None,
    ) -> dict[str, Any]:
        updated = max(t for t in (submitted, adjudicated, remitted) if t is not None)
        return {
            "CLAIM_ID": self._claim_id(),
            "FILL_ID": fill_id,
            "RX_NUMBER": rx,
            "FILL_NUMBER": fill_no,
            "PAYER_ID": payer_id,
            "SUBMITTED_AT": submitted,
            "RESPONSE_STATUS": status,
            "REJECT_CODE": code,
            "REJECT_MESSAGE": message,
            "BILLED_AMOUNT": billed,
            "EXPECTED_AMOUNT": expected,
            "PAID_AMOUNT": paid,
            "ADJUDICATED_AT": adjudicated,
            "REMITTED_AT": remitted,
            "UPDATED_AT": updated,
        }

    # --- demo stories -----------------------------------------------------------------------------------------
    def _story_rx_4471(self) -> None:
        """Rx 4471-02: refill too soon. Last fill 9 days ago covered 30 days; the payer allows refills at 80%."""
        pid = "PT00042"
        rid = next(p["PRESCRIBER_ID"] for p in self.prescribers if p["SPECIALTY"] == "Women's health")
        payer = PAYERS[0]
        written = self.today - timedelta(days=70)
        expires = written + timedelta(days=365)
        for fill_no, offset in ((0, 39), (1, 9)):
            self._fill(
                STORY_RX,
                fill_no,
                pid,
                rid,
                "DR03",
                payer,
                "Main St",
                self.today - timedelta(days=offset),
                30,
                5,
                written,
                expires,
                "steady",
                force_paid=True,
            )
        self._fill(
            STORY_RX,
            2,
            pid,
            rid,
            "DR03",
            payer,
            "Main St",
            self.today - timedelta(days=1),
            30,
            5,
            written,
            expires,
            "steady",
            force_reject=("79", REJECTS["79"][0]),
        )
        self.story.rx_4471_patient = pid
        self.story.rx_4471_fill_id = f"F{STORY_RX}-02"

    def _story_unknown_code(self) -> None:
        """Two recent rejections with a code the playbook has never seen: they must go to review, not get a guess."""
        rng = self.rng
        for offset in (2, 4):
            rid = rng.choice([p["PRESCRIBER_ID"] for p in self.prescribers if p["SPECIALTY"] == "Pediatrics"])
            pid = rng.choice([p["PATIENT_ID"] for p in self.patients])
            written = self.today - timedelta(days=offset + 1)
            rx = self._next_rx()
            self._fill(
                rx,
                0,
                pid,
                rid,
                "DR07",
                PAYERS[3],
                "Westside",
                self.today - timedelta(days=offset),
                30,
                5,
                written,
                written + timedelta(days=365),
                "steady",
                force_reject=UNKNOWN_REJECT,
            )
            self.story.unknown_code_rx.append(rx)

    # --- views of the world -------------------------------------------------------------------------------------
    def snapshot(self, as_of: date) -> tuple[list[dict], list[dict], list[dict]]:
        """Fills, claims and consents as the source held them at the end of ``as_of``."""
        cutoff = datetime.combine(as_of, time.max)
        claims = []
        for c in self.claims:
            if c["SUBMITTED_AT"] > cutoff:
                continue
            row = dict(c)
            if row["REMITTED_AT"] is not None and row["REMITTED_AT"] > cutoff:
                row.update(RESPONSE_STATUS="C", REMITTED_AT=None, UPDATED_AT=row["ADJUDICATED_AT"])
            claims.append(row)
        last_by_fill: dict[str, dict] = {}
        for c in claims:
            prev = last_by_fill.get(c["FILL_ID"])
            if prev is None or (c["SUBMITTED_AT"], c["CLAIM_ID"]) > (prev["SUBMITTED_AT"], prev["CLAIM_ID"]):
                last_by_fill[c["FILL_ID"]] = c
        fills = []
        for f in self.fills:
            if f["FILL_DATE"] > as_of.isoformat():
                continue
            row = dict(f)
            last = last_by_fill.get(row["FILL_ID"])
            if as_of < self.today and last is not None and last["RESPONSE_STATUS"] in ("R", "X"):
                row.update(STATUS="on_hold", PICKUP_READY_DATE=None)
            fills.append(row)
        consents = [c for c in self.consents if c["RECORDED_AT"] <= cutoff]
        return fills, claims, consents

    def resolved_history(self, as_of: date) -> list[dict[str, Any]]:
        by_fill: dict[str, list[dict]] = defaultdict(list)
        for c in self.claims:
            by_fill[c["FILL_ID"]].append(c)
        fills = {f["FILL_ID"]: f for f in self.fills}
        out = []
        horizon = as_of - timedelta(days=63)
        cutoff = datetime.combine(as_of, time.max)
        for fid, rows in by_fill.items():
            rows.sort(key=lambda r: (r["SUBMITTED_AT"], r["CLAIM_ID"]))
            first, last = rows[0], rows[-1]
            f = fills[fid]
            if (
                first["RESPONSE_STATUS"] != "R"
                or last["RESPONSE_STATUS"] != "P"
                or first["REJECT_CODE"] not in REJECTS
                or f["FILL_DATE"] < horizon.isoformat()
                or last["REMITTED_AT"] > cutoff
            ):
                continue
            out.append(
                {
                    "rx_number": f["RX_NUMBER"],
                    "fill_number": f["FILL_NUMBER"],
                    "patient_id": f["PATIENT_ID"],
                    "prescriber_id": f["PRESCRIBER_ID"],
                    "payer_id": f["PAYER_ID"],
                    "location": f["LOCATION"],
                    "fill_date": date.fromisoformat(f["FILL_DATE"]),
                    "reject_code": first["REJECT_CODE"],
                    "rejected_at": first["SUBMITTED_AT"],
                    "resubmitted_at": last["SUBMITTED_AT"],
                    "paid": last["PAID_AMOUNT"],
                    "remitted_at": last["REMITTED_AT"],
                    "expected": f["EXPECTED_AMOUNT"],
                }
            )
        out.sort(key=lambda h: (h["rx_number"], h["fill_number"]))
        return out


# ---------------------------------------------------------------------------------------------------------------
# Writer 1: Levigate's canonical reference layout
# ---------------------------------------------------------------------------------------------------------------

CANONICAL_DDL = """
CREATE TABLE DIM_PATIENT (PATIENT_ID TEXT PRIMARY KEY, FIRST_NAME TEXT, LAST_NAME TEXT, DOB TEXT, PHONE TEXT,
  ZIP TEXT, PREFERRED_LANGUAGE TEXT);
CREATE TABLE DIM_PRESCRIBER (PRESCRIBER_ID TEXT PRIMARY KEY, NPI TEXT, FIRST_NAME TEXT, LAST_NAME TEXT,
  CREDENTIAL TEXT, SPECIALTY TEXT, PRACTICE_NAME TEXT, PHONE TEXT, FAX TEXT, EMAIL TEXT, CITY TEXT, STATE TEXT);
CREATE TABLE DIM_PAYER (PAYER_ID TEXT PRIMARY KEY, PAYER_NAME TEXT, BIN TEXT, PCN TEXT, PLAN_TYPE TEXT);
CREATE TABLE DIM_DRUG (DRUG_ID TEXT PRIMARY KEY, DRUG_NAME TEXT, FORM TEXT, IS_COMPOUND INTEGER, UNIT_PRICE REAL);
CREATE TABLE FACT_RXFILL (FILL_ID TEXT PRIMARY KEY, RX_NUMBER TEXT, FILL_NUMBER INTEGER, PATIENT_ID TEXT,
  PRESCRIBER_ID TEXT, DRUG_ID TEXT, PAYER_ID TEXT, LOCATION TEXT, FILL_DATE TEXT, DAYS_SUPPLY INTEGER,
  QUANTITY REAL, REFILLS_AUTHORIZED INTEGER, REFILLS_REMAINING INTEGER, RX_WRITTEN_DATE TEXT,
  RX_EXPIRES_DATE TEXT, STATUS TEXT, PICKUP_READY_DATE TEXT, PATIENT_BALANCE REAL, EXPECTED_AMOUNT REAL);
CREATE TABLE FACT_CLAIM (CLAIM_ID TEXT PRIMARY KEY, FILL_ID TEXT, RX_NUMBER TEXT, FILL_NUMBER INTEGER,
  PAYER_ID TEXT, SUBMITTED_AT TEXT, RESPONSE_STATUS TEXT, REJECT_CODE TEXT, REJECT_MESSAGE TEXT,
  BILLED_AMOUNT REAL, EXPECTED_AMOUNT REAL, PAID_AMOUNT REAL, ADJUDICATED_AT TEXT, REMITTED_AT TEXT,
  UPDATED_AT TEXT);
CREATE TABLE PATIENT_CONSENT (PATIENT_ID TEXT, CHANNEL TEXT, STATUS TEXT, CONSENT_TYPE TEXT, RECORDED_AT TEXT,
  UPDATED_AT TEXT);
CREATE INDEX IX_FILL_RX ON FACT_RXFILL (RX_NUMBER);
CREATE INDEX IX_FILL_PRESCRIBER ON FACT_RXFILL (PRESCRIBER_ID);
CREATE INDEX IX_FILL_DATE ON FACT_RXFILL (FILL_DATE);
CREATE INDEX IX_CLAIM_FILL ON FACT_CLAIM (FILL_ID);
CREATE INDEX IX_CLAIM_UPDATED ON FACT_CLAIM (UPDATED_AT);
"""

_FILL_COLS = [
    "FILL_ID",
    "RX_NUMBER",
    "FILL_NUMBER",
    "PATIENT_ID",
    "PRESCRIBER_ID",
    "DRUG_ID",
    "PAYER_ID",
    "LOCATION",
    "FILL_DATE",
    "DAYS_SUPPLY",
    "QUANTITY",
    "REFILLS_AUTHORIZED",
    "REFILLS_REMAINING",
    "RX_WRITTEN_DATE",
    "RX_EXPIRES_DATE",
    "STATUS",
    "PICKUP_READY_DATE",
    "PATIENT_BALANCE",
    "EXPECTED_AMOUNT",
]
_CLAIM_COLS = [
    "CLAIM_ID",
    "FILL_ID",
    "RX_NUMBER",
    "FILL_NUMBER",
    "PAYER_ID",
    "SUBMITTED_AT",
    "RESPONSE_STATUS",
    "REJECT_CODE",
    "REJECT_MESSAGE",
    "BILLED_AMOUNT",
    "EXPECTED_AMOUNT",
    "PAID_AMOUNT",
    "ADJUDICATED_AT",
    "REMITTED_AT",
    "UPDATED_AT",
]


def _insert(con: sqlite3.Connection, table: str, cols: list[str], rows: list[tuple]) -> None:
    if rows:
        sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"  # noqa: S608  # nosec B608
        con.executemany(sql, rows)


def _write_canonical(b: _Builder, con: sqlite3.Connection, as_of: date) -> dict[str, int]:
    fills, claims, consents = b.snapshot(as_of)
    con.executescript(CANONICAL_DDL)
    _insert(
        con,
        "DIM_PATIENT",
        ["PATIENT_ID", "FIRST_NAME", "LAST_NAME", "DOB", "PHONE", "ZIP", "PREFERRED_LANGUAGE"],
        [
            (
                p["PATIENT_ID"],
                p["FIRST_NAME"],
                p["LAST_NAME"],
                p["DOB"],
                _fmt_phone(p["PHONES"][0][1]) if p["PHONES"] else None,
                p["ZIP"],
                p["PREFERRED_LANGUAGE"],
            )
            for p in b.patients
        ],
    )
    _insert(
        con,
        "DIM_PRESCRIBER",
        [
            "PRESCRIBER_ID",
            "NPI",
            "FIRST_NAME",
            "LAST_NAME",
            "CREDENTIAL",
            "SPECIALTY",
            "PRACTICE_NAME",
            "PHONE",
            "FAX",
            "EMAIL",
            "CITY",
            "STATE",
        ],
        [
            (
                p["PRESCRIBER_ID"],
                p["NPI"],
                p["FIRST_NAME"],
                p["LAST_NAME"],
                p["CREDENTIAL"],
                p["SPECIALTY"],
                p["PRACTICE_NAME"],
                _fmt_phone(p["PHONE"]),
                _fmt_phone(p["FAX"]),
                p["EMAIL"],
                p["CITY"],
                p["STATE"],
            )
            for p in b.prescribers
        ],
    )
    _insert(
        con,
        "DIM_PAYER",
        ["PAYER_ID", "PAYER_NAME", "BIN", "PCN", "PLAN_TYPE"],
        [(p[0], p[1], f"0{i}4471", f"PCN{i}", p[2]) for i, p in enumerate(PAYERS, 1)],
    )
    _insert(
        con,
        "DIM_DRUG",
        ["DRUG_ID", "DRUG_NAME", "FORM", "IS_COMPOUND", "UNIT_PRICE"],
        [(d[0], d[1], d[2], 1, round(awp_per_unit(d[0]) * 30, 4)) for d in DRUGS],
    )
    _insert(con, "FACT_RXFILL", _FILL_COLS, [tuple(f[c] for c in _FILL_COLS) for f in fills])
    _insert(
        con,
        "FACT_CLAIM",
        _CLAIM_COLS,
        [tuple(_iso(c[k]) if isinstance(c[k], datetime) else c[k] for k in _CLAIM_COLS) for c in claims],
    )
    _insert(
        con,
        "PATIENT_CONSENT",
        ["PATIENT_ID", "CHANNEL", "STATUS", "CONSENT_TYPE", "RECORDED_AT", "UPDATED_AT"],
        [
            (
                c["PATIENT_ID"],
                c["CHANNEL"],
                c["STATUS"],
                c["CONSENT_TYPE"],
                _iso(c["RECORDED_AT"]),
                _iso(c["RECORDED_AT"]),
            )
            for c in consents
        ],
    )
    return {}


# ---------------------------------------------------------------------------------------------------------------
# Writer 2: a pharmacy management system landed in Snowflake by Talend (normalised, with real-world mess)
# ---------------------------------------------------------------------------------------------------------------

PMS_DDL = """
CREATE TABLE STORE (STORE_ID INTEGER PRIMARY KEY, STORE_NAME TEXT, NCPDP_ID TEXT, STATE_CD TEXT);
CREATE TABLE PATIENT (PATIENT_KEY INTEGER PRIMARY KEY, PATIENT_NO TEXT, FIRST_NM TEXT, LAST_NM TEXT, BIRTH_DT TEXT,
  GENDER_CD TEXT, LANGUAGE_CD TEXT, ZIP_CD TEXT, IS_DELETED TEXT, LOAD_TS TEXT);
CREATE TABLE PATIENT_PHONE (PATIENT_KEY INTEGER, PHONE_TYPE TEXT, PHONE_NBR TEXT, IS_PRIMARY TEXT, LOAD_TS TEXT);
CREATE TABLE PATIENT_COMM_PREF (PATIENT_KEY INTEGER, CHANNEL_CD TEXT, OPT_IN_FLAG TEXT, CONSENT_TYPE_CD TEXT,
  CONSENT_DT TEXT, SOURCE_CD TEXT, UPDATED_TS TEXT);
CREATE TABLE PAYER (PAYER_KEY INTEGER PRIMARY KEY, PAYER_CD TEXT, PAYER_NM TEXT, PLAN_TYPE_CD TEXT);
CREATE TABLE PLAN (PLAN_KEY INTEGER PRIMARY KEY, PAYER_KEY INTEGER, BIN_NBR TEXT, PCN TEXT, GROUP_NBR TEXT,
  PLAN_NM TEXT, COVERAGE_ROLE TEXT);
CREATE TABLE PLAN_CONTRACT (PLAN_KEY INTEGER, CONTRACT_RATE REAL, DISPENSING_FEE REAL, EFF_DT TEXT, TERM_DT TEXT);
CREATE TABLE PATIENT_INSURANCE (PATIENT_KEY INTEGER, PLAN_KEY INTEGER, CARDHOLDER_ID TEXT, PERSON_CD TEXT,
  COVERAGE_ORDER INTEGER, EFF_DT TEXT, TERM_DT TEXT);
CREATE TABLE PRESCRIBER (PRESCRIBER_KEY INTEGER PRIMARY KEY, PRESCRIBER_CD TEXT, NPI TEXT, FIRST_NM TEXT,
  LAST_NM TEXT, CREDENTIAL_CD TEXT, SPECIALTY_DESC TEXT, PRACTICE_NM TEXT, EMAIL_ADDR TEXT, IS_DELETED TEXT);
CREATE TABLE PRESCRIBER_ADDRESS (PRESCRIBER_KEY INTEGER, ADDR_TYPE TEXT, CITY_NM TEXT, STATE_CD TEXT,
  PHONE_NBR TEXT, FAX_NBR TEXT);
CREATE TABLE FORMULA (FORMULA_KEY INTEGER PRIMARY KEY, FORMULA_CD TEXT, FORMULA_NM TEXT, DOSAGE_FORM TEXT,
  AWP_PER_UNIT REAL, IS_COMPOUND TEXT);
CREATE TABLE FORMULA_INGREDIENT (FORMULA_KEY INTEGER, SEQ_NBR INTEGER, INGREDIENT_NDC TEXT, INGREDIENT_NM TEXT,
  QTY REAL, UOM TEXT);
CREATE TABLE RX (RX_KEY INTEGER PRIMARY KEY, RX_NBR TEXT, STORE_ID INTEGER, PATIENT_KEY INTEGER,
  PRESCRIBER_KEY INTEGER, FORMULA_KEY INTEGER, WRITTEN_DT TEXT, EXPIRE_DT TEXT, REFILLS_AUTH INTEGER,
  SIG_TEXT TEXT, RX_STATUS_CD TEXT, LOAD_TS TEXT);
CREATE TABLE RX_FILL (FILL_KEY INTEGER, RX_KEY INTEGER, FILL_NBR INTEGER, FILL_DT TEXT, DAYS_SUPPLY INTEGER,
  QTY_DISPENSED REAL, REFILLS_REMAINING INTEGER, FILL_STATUS_CD TEXT, WAITING_BIN_DT TEXT, SOLD_DT TEXT,
  PLAN_KEY_BILLED INTEGER, USUAL_CUSTOMARY_AMT REAL, PATIENT_PAY_AMT REAL, PATIENT_PAID_AMT REAL, IS_DELETED TEXT,
  LOAD_TS TEXT);
CREATE TABLE CLAIM_TXN (TXN_KEY INTEGER PRIMARY KEY, FILL_KEY INTEGER, PLAN_KEY INTEGER, TXN_TYPE_CD TEXT,
  TXN_TS TEXT, RESPONSE_TS TEXT, RESPONSE_STATUS_CD TEXT, SUBMITTED_AMT REAL, TOTAL_PAID_AMT REAL, PATIENT_PAY_AMT REAL,
  AUTH_NBR TEXT, LOAD_TS TEXT);
CREATE TABLE CLAIM_REJECT (TXN_KEY INTEGER, REJECT_SEQ INTEGER, REJECT_CD TEXT);
CREATE TABLE REJECT_CODE_REF (REJECT_CD TEXT PRIMARY KEY, REJECT_DESC TEXT);
CREATE TABLE REMIT_835 (REMIT_LINE_KEY INTEGER PRIMARY KEY, CHECK_EFT_NBR TEXT, PAYER_KEY INTEGER, PAYMENT_DT TEXT,
  RX_NBR TEXT, FILL_NBR INTEGER, CLAIM_PAID_AMT REAL, ADJ_GROUP_CD TEXT, ADJ_REASON_CD TEXT, ADJ_AMT REAL,
  LOAD_TS TEXT);
CREATE INDEX IX_RX_FILL_KEY ON RX_FILL (FILL_KEY);
CREATE INDEX IX_RX_FILL_RX ON RX_FILL (RX_KEY);
CREATE INDEX IX_RX_NBR ON RX (RX_NBR);
CREATE INDEX IX_TXN_FILL ON CLAIM_TXN (FILL_KEY);
CREATE INDEX IX_TXN_LOAD ON CLAIM_TXN (LOAD_TS);
CREATE INDEX IX_REJECT_TXN ON CLAIM_REJECT (TXN_KEY);
CREATE INDEX IX_REMIT_RX ON REMIT_835 (RX_NBR, FILL_NBR);
CREATE INDEX IX_PHONE_PATIENT ON PATIENT_PHONE (PATIENT_KEY);
"""

# The client's BI layer (what the Qlik model reads). Written with the client's own business rules, which are not
# always Levigate's: unpaid after a flat 30 days for every payer, and prescriber volume counts sold fills only.
# '{as_of}' stands in for CURRENT_DATE() in the client's Snowflake view.
PMS_BI_VIEWS = """
CREATE VIEW VW_CLAIM_STATUS AS
WITH fill AS (
  SELECT * FROM (SELECT x.*, ROW_NUMBER() OVER (PARTITION BY FILL_KEY ORDER BY LOAD_TS DESC) AS RN FROM RX_FILL x) t
  WHERE RN = 1 AND IS_DELETED = 'N'
), txn AS (
  SELECT t.*, ROW_NUMBER() OVER (PARTITION BY t.FILL_KEY ORDER BY t.TXN_TS DESC, t.TXN_KEY DESC) AS RN
  FROM CLAIM_TXN t JOIN fill f ON f.FILL_KEY = t.FILL_KEY AND f.PLAN_KEY_BILLED = t.PLAN_KEY
), remit AS (
  SELECT RX_NBR, FILL_NBR, SUM(CLAIM_PAID_AMT) AS PAID_TO_DATE, MAX(PAYMENT_DT) AS LAST_PAYMENT_DT
  FROM REMIT_835 GROUP BY RX_NBR, FILL_NBR
)
SELECT f.FILL_KEY, r.RX_NBR, f.FILL_NBR, f.FILL_DT,
  CASE WHEN t.TXN_TYPE_CD = 'B2' THEN 'REVERSED'
       WHEN t.RESPONSE_STATUS_CD = 'R' THEN 'REJECTED'
       WHEN m.PAID_TO_DATE IS NOT NULL THEN 'PAID'
       WHEN t.RESPONSE_STATUS_CD = 'P' AND f.FILL_DT < '{unpaid_cutoff}' THEN 'UNPAID'
       ELSE 'PENDING' END AS CURRENT_STATUS,
  ROUND(CASE WHEN fo.AWP_PER_UNIT * f.QTY_DISPENSED * c.CONTRACT_RATE + c.DISPENSING_FEE < f.USUAL_CUSTOMARY_AMT
             THEN fo.AWP_PER_UNIT * f.QTY_DISPENSED * c.CONTRACT_RATE + c.DISPENSING_FEE
             ELSE f.USUAL_CUSTOMARY_AMT END, 2) AS EXPECTED_REIMB,
  m.PAID_TO_DATE,
  m.LAST_PAYMENT_DT,
  CAST(julianday(m.LAST_PAYMENT_DT) - julianday(f.FILL_DT) AS INTEGER) AS DAYS_TO_PAY
FROM fill f
JOIN RX r ON r.RX_KEY = f.RX_KEY
JOIN FORMULA fo ON fo.FORMULA_KEY = r.FORMULA_KEY
LEFT JOIN PLAN_CONTRACT c ON c.PLAN_KEY = f.PLAN_KEY_BILLED AND f.FILL_DT BETWEEN c.EFF_DT AND c.TERM_DT
LEFT JOIN txn t ON t.FILL_KEY = f.FILL_KEY AND t.RN = 1
LEFT JOIN remit m ON m.RX_NBR = r.RX_NBR AND m.FILL_NBR = f.FILL_NBR AND t.RESPONSE_STATUS_CD = 'P';

CREATE VIEW VW_PRESCRIBER_MONTHLY AS
WITH fill AS (
  SELECT * FROM (SELECT x.*, ROW_NUMBER() OVER (PARTITION BY FILL_KEY ORDER BY LOAD_TS DESC) AS RN FROM RX_FILL x) t
  WHERE RN = 1 AND IS_DELETED = 'N'
)
SELECT p.PRESCRIBER_CD, SUBSTR(f.FILL_DT, 1, 7) AS FILL_MONTH, COUNT(*) AS RX_COUNT
FROM fill f JOIN RX r ON r.RX_KEY = f.RX_KEY JOIN PRESCRIBER p ON p.PRESCRIBER_KEY = r.PRESCRIBER_KEY
WHERE f.FILL_STATUS_CD = 'S'
GROUP BY p.PRESCRIBER_CD, SUBSTR(f.FILL_DT, 1, 7);
"""

_STATUS_CD = {"picked_up": "S", "ready_for_pickup": "W", "on_hold": "H"}
_LANG_CD = {"English": "ENG", "Spanish": "SPA"}


def _pms_phone(digits: str, rng: random.Random) -> str:
    """Phone numbers as different PMS screens and imports stored them over the years."""
    return rng.choice(
        [
            _fmt_phone(digits),
            digits,
            f"{digits[:3]}.{digits[3:6]}.{digits[6:]}",
            f"{digits[:3]}-{digits[3:6]}-{digits[6:]}",
            f"+1{digits}",
        ]
    )


# The PMS keeps plan types as codes; the mapping translates them to Levigate's labels.
_PLAN_TYPE_CD = {
    "Commercial": "COMM",
    "Medicare Part D": "MCR_D",
    "Medicaid": "MCD",
    "Workers' comp": "WC",
}


def _write_pms(b: _Builder, con: sqlite3.Connection, as_of: date) -> dict[str, int]:
    rng = random.Random(b.seed + 2)  # row order only (tables are unordered); never decides content
    fills, claims, consents = b.snapshot(as_of)
    mess: dict[str, int] = defaultdict(int)

    # Every surrogate key and every piece of mess is a fixed property of the record, as in a real source: the
    # warehouse built for a later day holds the same rows with the same keys, plus that day's new and changed rows.
    # (Incremental-sync tests rebuild the warehouse day by day and rely on this.)
    def chance(*key: object) -> random.Random:
        return random.Random(f"{b.seed}:" + ":".join(map(str, key)))

    fill_index = {f["FILL_ID"]: i for i, f in enumerate(b.fills, 1)}
    claim_index = {c["CLAIM_ID"]: i for i, c in enumerate(b.claims, 1)}
    rx_index: dict[str, int] = {}
    for f in b.fills:
        rx_index.setdefault(f["RX_NUMBER"], len(rx_index) + 1)
    con.executescript(PMS_DDL)
    load = lambda ts: _iso(ts + timedelta(minutes=20))  # noqa: E731 - Talend lands rows ~20 minutes after the event

    stores = {name: i for i, name in enumerate(LOCATIONS, 1)}
    _insert(
        con,
        "STORE",
        ["STORE_ID", "STORE_NAME", "NCPDP_ID", "STATE_CD"],
        [(i, name, f"14{i}4471", "IL") for name, i in stores.items()],
    )

    patient_key = {p["PATIENT_ID"]: int(p["PATIENT_ID"][2:]) for p in b.patients}
    epoch = datetime.combine(b.start, time(1, 0))
    _insert(
        con,
        "PATIENT",
        [
            "PATIENT_KEY",
            "PATIENT_NO",
            "FIRST_NM",
            "LAST_NM",
            "BIRTH_DT",
            "GENDER_CD",
            "LANGUAGE_CD",
            "ZIP_CD",
            "IS_DELETED",
            "LOAD_TS",
        ],
        [
            (
                patient_key[p["PATIENT_ID"]],
                p["PATIENT_ID"],
                p["FIRST_NAME"].upper(),
                p["LAST_NAME"].upper(),
                p["DOB"],
                chance("sex", p["PATIENT_ID"]).choice(["F", "M", "U"]),
                _LANG_CD[p["PREFERRED_LANGUAGE"]],
                p["ZIP"],
                "N",
                _iso(epoch),
            )
            for p in b.patients
        ],
    )
    phone_rows = []
    for p in b.patients:
        for i, (ptype, digits) in enumerate(p["PHONES"]):
            phone_rows.append(
                (
                    patient_key[p["PATIENT_ID"]],
                    ptype,
                    _pms_phone(digits, chance("phone", p["PATIENT_ID"], i)),
                    "Y" if i == 0 else "N",
                    _iso(epoch),
                )
            )
        if not p["PHONES"]:
            mess["patients_without_phone"] += 1
    rng.shuffle(phone_rows)  # the primary flag, not row order, decides
    _insert(con, "PATIENT_PHONE", ["PATIENT_KEY", "PHONE_TYPE", "PHONE_NBR", "IS_PRIMARY", "LOAD_TS"], phone_rows)

    channel_cd = {"call": "PHONE", "sms": "SMS"}
    consent_cd = {
        "prior_express": "PRIOR_EXPRESS",
        "prior_express_written": "PRIOR_EXPRESS_WRITTEN",
        "opt_out": "OPT_OUT",
    }
    pref_rows = [
        (
            patient_key[c["PATIENT_ID"]],
            channel_cd[c["CHANNEL"]],
            "Y" if c["STATUS"] == "granted" else "N",
            consent_cd[c["CONSENT_TYPE"]],
            c["RECORDED_AT"].date().isoformat(),
            "PMS_INTAKE",
            _iso(c["RECORDED_AT"]),
        )
        for c in consents
    ]
    for c in consents:  # e-mail preferences exist too; Levigate does not use them
        if chance("email", c["PATIENT_ID"], c["CHANNEL"], c["RECORDED_AT"]).random() >= 1 / 9:
            continue
        pref_rows.append(
            (
                patient_key[c["PATIENT_ID"]],
                "EMAIL",
                "Y",
                "PRIOR_EXPRESS",
                c["RECORDED_AT"].date().isoformat(),
                "PMS_INTAKE",
                _iso(c["RECORDED_AT"]),
            )
        )
        mess["email_preferences_ignored"] += 1
    _insert(
        con,
        "PATIENT_COMM_PREF",
        ["PATIENT_KEY", "CHANNEL_CD", "OPT_IN_FLAG", "CONSENT_TYPE_CD", "CONSENT_DT", "SOURCE_CD", "UPDATED_TS"],
        pref_rows,
    )

    payer_key = {p[0]: i for i, p in enumerate(PAYERS, 1)}
    _insert(
        con,
        "PAYER",
        ["PAYER_KEY", "PAYER_CD", "PAYER_NM", "PLAN_TYPE_CD"],
        [(payer_key[p[0]], p[0], p[1], _PLAN_TYPE_CD[p[2]]) for p in PAYERS],
    )
    primary_plan = {p[0]: payer_key[p[0]] * 10 + 1 for p in PAYERS}
    secondary_plan = {p[0]: payer_key[p[0]] * 10 + 2 for p in PAYERS}
    plan_rows, contract_rows = [], []
    contract_switch = b.today - timedelta(days=400)
    for i, payer in enumerate(PAYERS, 1):
        plan_rows.append(
            (primary_plan[payer[0]], payer_key[payer[0]], f"0{i}4471", f"PCN{i}", f"GRP{i:03d}", payer[1], "PRIMARY")
        )
        plan_rows.append(
            (
                secondary_plan[payer[0]],
                payer_key[payer[0]],
                f"0{i}4472",
                f"PCN{i}S",
                f"GRP{i:03d}S",
                f"{payer[1]} Supplemental",
                "SECONDARY",
            )
        )
        rate, fee = CONTRACTS[payer[0]]
        contract_rows.append(
            (
                primary_plan[payer[0]],
                round(rate + 0.03, 2),
                round(fee - 2, 2),
                "2024-01-01",
                (contract_switch - timedelta(days=1)).isoformat(),
            )
        )
        contract_rows.append((primary_plan[payer[0]], rate, fee, contract_switch.isoformat(), "9999-12-31"))
        contract_rows.append((secondary_plan[payer[0]], 0.5, 0.0, "2024-01-01", "9999-12-31"))
    mess["expired_contract_rows"] = len(PAYERS)
    _insert(
        con, "PLAN", ["PLAN_KEY", "PAYER_KEY", "BIN_NBR", "PCN", "GROUP_NBR", "PLAN_NM", "COVERAGE_ROLE"], plan_rows
    )
    _insert(con, "PLAN_CONTRACT", ["PLAN_KEY", "CONTRACT_RATE", "DISPENSING_FEE", "EFF_DT", "TERM_DT"], contract_rows)

    prescriber_key = {p["PRESCRIBER_ID"]: int(p["PRESCRIBER_ID"][2:]) for p in b.prescribers}
    _insert(
        con,
        "PRESCRIBER",
        [
            "PRESCRIBER_KEY",
            "PRESCRIBER_CD",
            "NPI",
            "FIRST_NM",
            "LAST_NM",
            "CREDENTIAL_CD",
            "SPECIALTY_DESC",
            "PRACTICE_NM",
            "EMAIL_ADDR",
            "IS_DELETED",
        ],
        [
            (
                prescriber_key[p["PRESCRIBER_ID"]],
                p["PRESCRIBER_ID"],
                p["NPI"],
                p["FIRST_NAME"],
                p["LAST_NAME"],
                p["CREDENTIAL"],
                p["SPECIALTY"],
                p["PRACTICE_NAME"],
                p["EMAIL"],
                "N",
            )
            for p in b.prescribers
        ],
    )
    _insert(
        con,
        "PRESCRIBER_ADDRESS",
        ["PRESCRIBER_KEY", "ADDR_TYPE", "CITY_NM", "STATE_CD", "PHONE_NBR", "FAX_NBR"],
        [
            (
                prescriber_key[p["PRESCRIBER_ID"]],
                "PRACTICE",
                p["CITY"],
                p["STATE"],
                _fmt_phone(p["PHONE"]),
                _fmt_phone(p["FAX"]),
            )
            for p in b.prescribers
        ]
        + [
            (prescriber_key[p["PRESCRIBER_ID"]], "MAILING", p["CITY"], p["STATE"], None, None)
            for p in b.prescribers[::4]
        ],
    )

    formula_key = {d[0]: int(d[0][2:]) for d in DRUGS}
    _insert(
        con,
        "FORMULA",
        ["FORMULA_KEY", "FORMULA_CD", "FORMULA_NM", "DOSAGE_FORM", "AWP_PER_UNIT", "IS_COMPOUND"],
        [(formula_key[d[0]], d[0], d[1], d[2].upper(), awp_per_unit(d[0]), "Y") for d in DRUGS],
    )
    _insert(
        con,
        "FORMULA_INGREDIENT",
        ["FORMULA_KEY", "SEQ_NBR", "INGREDIENT_NDC", "INGREDIENT_NM", "QTY", "UOM"],
        [
            (formula_key[d], i, f"38779-{formula_key[d]:04d}-{i:02d}", name, qty, uom)
            for d, items in INGREDIENTS.items()
            for i, (name, qty, uom) in enumerate(items, 1)
        ],
    )

    # Prescriptions and fills
    rx_key: dict[str, int] = {}
    rx_rows, fill_rows, insurance = [], [], set()
    fill_key: dict[str, int] = {}
    for f in fills:
        if f["RX_NUMBER"] not in rx_key:
            rx_key[f["RX_NUMBER"]] = rx_index[f["RX_NUMBER"]]
            rx_rows.append(
                (
                    rx_key[f["RX_NUMBER"]],
                    f["RX_NUMBER"],
                    stores[f["LOCATION"]],
                    patient_key[f["PATIENT_ID"]],
                    prescriber_key[f["PRESCRIBER_ID"]],
                    formula_key[f["DRUG_ID"]],
                    f["RX_WRITTEN_DATE"],
                    f["RX_EXPIRES_DATE"],
                    f["REFILLS_AUTHORIZED"],
                    "Apply as directed",
                    "A",
                    f["RX_WRITTEN_DATE"] + "T09:00:00",
                )
            )
        fill_key[f["FILL_ID"]] = 100_000 + fill_index[f["FILL_ID"]]
        balance = f["PATIENT_BALANCE"] or 0.0
        sold = f["FILL_DATE"] if f["STATUS"] == "picked_up" else None
        row = (
            fill_key[f["FILL_ID"]],
            rx_key[f["RX_NUMBER"]],
            f["FILL_NUMBER"],
            f["FILL_DATE"],
            f["DAYS_SUPPLY"],
            f["QUANTITY"],
            f["REFILLS_REMAINING"],
            _STATUS_CD[f["STATUS"]],
            f["PICKUP_READY_DATE"],
            sold,
            primary_plan[f["PAYER_ID"]],
            f["UC_AMOUNT"],
            round(balance + 5.0, 2) if balance else 0.0,
            5.0 if balance else 0.0,
            "N",
            f["FILL_DATE"] + "T18:30:00",
        )
        fill_rows.append(row)
        insurance.add((f["PATIENT_ID"], f["PAYER_ID"]))
        if chance("reload", f["FILL_ID"]).random() < 0.007:  # a Talend reload landed an older copy of the row
            stale = list(row)
            stale[7], stale[9], stale[15] = "W", None, f["FILL_DATE"] + "T09:15:00"
            fill_rows.append(tuple(stale))
            mess["duplicate_fill_rows"] += 1
    for f in fills:  # entered in error, then voided
        if chance("void", f["FILL_ID"]).random() >= 1 / 300:
            continue
        fill_rows.append(
            (
                900_000 + fill_index[f["FILL_ID"]],
                rx_key[f["RX_NUMBER"]],
                f["FILL_NUMBER"] + 50,
                f["FILL_DATE"],
                f["DAYS_SUPPLY"],
                f["QUANTITY"],
                0,
                "H",
                None,
                None,
                primary_plan[f["PAYER_ID"]],
                f["UC_AMOUNT"],
                0.0,
                0.0,
                "Y",
                f["FILL_DATE"] + "T18:30:00",
            )
        )
        mess["soft_deleted_fills"] += 1
    rng.shuffle(fill_rows)
    _insert(
        con,
        "RX",
        [
            "RX_KEY",
            "RX_NBR",
            "STORE_ID",
            "PATIENT_KEY",
            "PRESCRIBER_KEY",
            "FORMULA_KEY",
            "WRITTEN_DT",
            "EXPIRE_DT",
            "REFILLS_AUTH",
            "SIG_TEXT",
            "RX_STATUS_CD",
            "LOAD_TS",
        ],
        rx_rows,
    )
    _insert(
        con,
        "RX_FILL",
        [
            "FILL_KEY",
            "RX_KEY",
            "FILL_NBR",
            "FILL_DT",
            "DAYS_SUPPLY",
            "QTY_DISPENSED",
            "REFILLS_REMAINING",
            "FILL_STATUS_CD",
            "WAITING_BIN_DT",
            "SOLD_DT",
            "PLAN_KEY_BILLED",
            "USUAL_CUSTOMARY_AMT",
            "PATIENT_PAY_AMT",
            "PATIENT_PAID_AMT",
            "IS_DELETED",
            "LOAD_TS",
        ],
        fill_rows,
    )
    _insert(
        con,
        "PATIENT_INSURANCE",
        ["PATIENT_KEY", "PLAN_KEY", "CARDHOLDER_ID", "PERSON_CD", "COVERAGE_ORDER", "EFF_DT", "TERM_DT"],
        [
            (
                patient_key[pid],
                primary_plan[payer],
                f"ZX{chance('card', pid, payer).randint(10_000_000, 99_999_999)}",
                "01",
                1,
                "2024-01-01",
                "9999-12-31",
            )
            for pid, payer in sorted(insurance)
        ],
    )

    # Claim transactions, rejects, remittances
    txn_rows, reject_rows, remit_rows = [], [], []
    fills_by_id = {f["FILL_ID"]: f for f in fills}
    for c in claims:
        # Keys follow the claim's place in the source's history: a transaction, then room for its COB follow-up.
        txn_seq = 7_000_000 + 2 * claim_index[c["CLAIM_ID"]]
        f = fills_by_id[c["FILL_ID"]]
        status = c["RESPONSE_STATUS"]
        ttype = "B2" if status == "X" else "B1"
        resp = {"R": "R", "P": "P", "C": "P", "X": "A", None: None}[status]
        txn_rows.append(
            (
                txn_seq,
                fill_key[c["FILL_ID"]],
                primary_plan[c["PAYER_ID"]],
                ttype,
                _iso(c["SUBMITTED_AT"]),
                _iso(c["ADJUDICATED_AT"]) if c["ADJUDICATED_AT"] else None,
                resp,
                c["BILLED_AMOUNT"],
                c["PAID_AMOUNT"],
                5.0 if resp == "P" else None,
                f"A{txn_seq}" if resp == "P" else None,
                load(c["UPDATED_AT"]),
            )
        )
        if status == "R":
            reject_rows.append((txn_seq, 1, c["REJECT_CODE"]))
            extra = SECONDARY_REJECTS.get(c["REJECT_CODE"])
            if extra and chance("reject2", c["CLAIM_ID"]).random() < 0.15:
                reject_rows.append((txn_seq, 2, extra))
                mess["secondary_reject_codes"] += 1
        if status == "P":
            remitted, paid = c["REMITTED_AT"], c["PAID_AMOUNT"]
            parts = [paid]
            if chance("split", c["CLAIM_ID"]).random() < 0.1:  # the payer split the payment across two 835s
                first = round(paid * 0.6, 2)
                parts = [first, round(paid - first, 2)]
                mess["split_remittances"] += 1
            for i, amount in enumerate(parts):
                remit_seq = 2 * claim_index[c["CLAIM_ID"]] + i
                pay_date = (remitted - timedelta(days=7 * (len(parts) - 1 - i))).date()
                remit_rows.append(
                    (
                        remit_seq,
                        f"EFT{payer_key[c['PAYER_ID']]}{pay_date:%y%m%d}",
                        payer_key[c["PAYER_ID"]],
                        pay_date.isoformat(),
                        f["RX_NUMBER"],
                        f["FILL_NUMBER"],
                        amount,
                        None,
                        None,
                        None,
                        _iso(datetime.combine(pay_date, time(6, 30))),
                    )
                )
            # Secondary payer billed after the primary paid (COB) — outside the pilot's scope
            if chance("cob", c["CLAIM_ID"]).random() < 0.01:
                txn_seq += 1
                cob_ts = c["SUBMITTED_AT"] + timedelta(hours=2)
                txn_rows.append(
                    (
                        txn_seq,
                        fill_key[c["FILL_ID"]],
                        secondary_plan[c["PAYER_ID"]],
                        "B1",
                        _iso(cob_ts),
                        _iso(cob_ts + timedelta(seconds=4)),
                        "P",
                        5.0,
                        5.0,
                        0.0,
                        f"S{txn_seq}",
                        load(cob_ts),
                    )
                )
                mess["secondary_payer_claims"] += 1
    for n in range(max(1, len(claims) // 500)):  # claim landed before its fill (late-arriving fill row)
        txn_seq = 7_900_000 + n
        ts = datetime.combine(as_of, time(16, 0))
        txn_rows.append(
            (
                txn_seq,
                990_000 + mess["orphan_claims"],
                11,
                "B1",
                _iso(ts),
                _iso(ts + timedelta(seconds=3)),
                "P",
                100.0,
                80.0,
                5.0,
                f"O{txn_seq}",
                load(ts),
            )
        )
        mess["orphan_claims"] += 1
    rng.shuffle(txn_rows)
    _insert(
        con,
        "CLAIM_TXN",
        [
            "TXN_KEY",
            "FILL_KEY",
            "PLAN_KEY",
            "TXN_TYPE_CD",
            "TXN_TS",
            "RESPONSE_TS",
            "RESPONSE_STATUS_CD",
            "SUBMITTED_AMT",
            "TOTAL_PAID_AMT",
            "PATIENT_PAY_AMT",
            "AUTH_NBR",
            "LOAD_TS",
        ],
        txn_rows,
    )
    _insert(con, "CLAIM_REJECT", ["TXN_KEY", "REJECT_SEQ", "REJECT_CD"], reject_rows)
    _insert(con, "REJECT_CODE_REF", ["REJECT_CD", "REJECT_DESC"], sorted(REJECT_REFERENCE.items()))
    _insert(
        con,
        "REMIT_835",
        [
            "REMIT_LINE_KEY",
            "CHECK_EFT_NBR",
            "PAYER_KEY",
            "PAYMENT_DT",
            "RX_NBR",
            "FILL_NBR",
            "CLAIM_PAID_AMT",
            "ADJ_GROUP_CD",
            "ADJ_REASON_CD",
            "ADJ_AMT",
            "LOAD_TS",
        ],
        remit_rows,
    )

    con.executescript(PMS_BI_VIEWS.replace("{unpaid_cutoff}", (as_of - timedelta(days=30)).isoformat()))
    # The story's fill as this layout identifies it (FILL_ID is the PMS surrogate key here).
    b.story.rx_4471_fill_id = str(fill_key[b.story.rx_4471_fill_id])
    return dict(mess)


# ---------------------------------------------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------------------------------------------


def build_warehouse(
    path: Path,
    today: date,
    *,
    layout: str = "canonical",
    as_of: date | None = None,
    seed: int = 4471,
    n_patients: int = 2000,
    n_prescribers: int = 150,
) -> World:
    """Simulate the pharmacy up to ``today`` and write what the warehouse held at the end of ``as_of``."""
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout '{layout}'")
    as_of = as_of or today
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    b = _Builder(today, seed, n_patients, n_prescribers)
    b.build_dimensions()
    b.build_facts()
    b.build_consents()
    con = sqlite3.connect(path)
    try:
        mess = _write_canonical(b, con, as_of) if layout == "canonical" else _write_pms(b, con, as_of)
        con.commit()
    finally:
        con.close()
    return World(story=b.story, layout=layout, as_of=as_of, resolved_history=b.resolved_history(as_of), mess=mess)


def _next_load_ts(con: sqlite3.Connection, layout: str, loaded_at: datetime | None) -> str:
    if loaded_at is not None:
        return loaded_at.replace(tzinfo=None).isoformat(timespec="seconds")
    if layout == "canonical":
        newest = con.execute("SELECT MAX(UPDATED_AT) FROM FACT_CLAIM").fetchone()[0]
    else:
        newest = con.execute(
            "SELECT MAX(ts) FROM (SELECT MAX(LOAD_TS) AS ts FROM CLAIM_TXN "
            "UNION ALL SELECT MAX(LOAD_TS) FROM REMIT_835) x"
        ).fetchone()[0]
    landed = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    if newest:
        landed = max(landed, datetime.fromisoformat(str(newest)) + timedelta(minutes=1))
    return landed.isoformat(timespec="seconds")


def record_upstream_payment(
    path: Path,
    layout: str,
    rx_number: str,
    fill_number: int,
    amount: float,
    paid_at: datetime,
    loaded_at: datetime | None = None,
) -> None:
    """Test/demo helper: the payer pays a resubmitted claim, as the next Talend load would land it.

    ``loaded_at`` is when the row lands in the warehouse (the change marker). By default it is the next load: now,
    or one minute after the newest row already landed if that is later (demo data can be stamped later today)."""
    con = sqlite3.connect(path)
    try:
        landed = _next_load_ts(con, layout, loaded_at)
        if layout == "canonical":
            fill_id, payer_id = con.execute(
                "SELECT FILL_ID, PAYER_ID FROM FACT_RXFILL WHERE RX_NUMBER = ? AND FILL_NUMBER = ?",
                (rx_number, fill_number),
            ).fetchone()
            ts = paid_at.isoformat(timespec="seconds")
            con.execute(
                "INSERT INTO FACT_CLAIM VALUES (?,?,?,?,?,?,'P',NULL,NULL,?,?,?,?,?,?)",
                (
                    f"CL-UP-{rx_number}-{fill_number}",
                    fill_id,
                    rx_number,
                    fill_number,
                    payer_id,
                    ts,
                    amount,
                    amount,
                    amount,
                    ts,
                    ts,
                    landed,
                ),
            )
        else:
            fill_key, plan_key, uc = con.execute(
                "SELECT f.FILL_KEY, f.PLAN_KEY_BILLED, f.USUAL_CUSTOMARY_AMT FROM RX_FILL f JOIN RX r ON r.RX_KEY = "
                "f.RX_KEY WHERE r.RX_NBR = ? AND f.FILL_NBR = ? AND f.IS_DELETED = 'N' ORDER BY f.LOAD_TS DESC",
                (rx_number, fill_number),
            ).fetchone()
            payer_key = plan_key // 10
            ts = paid_at.isoformat(timespec="seconds")
            txn = con.execute("SELECT COALESCE(MAX(TXN_KEY), 0) + 1 FROM CLAIM_TXN").fetchone()[0]
            con.execute(
                "INSERT INTO CLAIM_TXN VALUES (?,?,?,'B1',?,?,'P',?,?,5.0,?,?)",
                (txn, fill_key, plan_key, ts, ts, uc, amount, f"A{txn}", landed),
            )
            line = con.execute("SELECT COALESCE(MAX(REMIT_LINE_KEY), 0) + 1 FROM REMIT_835").fetchone()[0]
            con.execute(
                "INSERT INTO REMIT_835 VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,?)",
                (
                    line,
                    f"EFT{payer_key}UP",
                    payer_key,
                    paid_at.date().isoformat(),
                    rx_number,
                    fill_number,
                    amount,
                    landed,
                ),
            )
        con.commit()
    finally:
        con.close()
