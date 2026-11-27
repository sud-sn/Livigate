"""Synthetic client warehouse for the demo and for tests. 100% fictional: no real patients, prescribers or payers.

Shape mirrors the client's Snowflake tables (DIM_* and FACT_*), so the connector, the rules engine and the
screens run against the same column names they will see in the pilot. Phone numbers use the reserved 555-01xx
range. Generation is deterministic for a given seed and "today".
"""

from __future__ import annotations

import math
import random
import sqlite3
import string
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

DDL = """
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
CREATE INDEX IX_FILL_RX ON FACT_RXFILL (RX_NUMBER);
CREATE INDEX IX_FILL_PRESCRIBER ON FACT_RXFILL (PRESCRIBER_ID);
CREATE INDEX IX_FILL_DATE ON FACT_RXFILL (FILL_DATE);
CREATE INDEX IX_CLAIM_FILL ON FACT_CLAIM (FILL_ID);
CREATE INDEX IX_CLAIM_UPDATED ON FACT_CLAIM (UPDATED_AT);
"""

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

DRUGS = [
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

STORY_RX = "4471"
STORY_PRESCRIBER = "PR0007"


@dataclass
class Story:
    today: date
    rx_4471_patient: str = ""
    rx_4471_fill_id: str = ""
    declining_prescriber: str = STORY_PRESCRIBER
    unknown_code_fill_ids: list[str] = field(default_factory=list)


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


def _phone(rng: random.Random) -> str:
    return f"({rng.choice([312, 630, 708, 773, 847, 219, 262])}) 555-01{rng.randint(0, 99):02d}"


class _Builder:
    def __init__(self, today: date, seed: int, n_patients: int, n_prescribers: int) -> None:
        self.today = today
        self.rng = random.Random(seed)
        self.start = today - timedelta(days=182)
        self.n_patients = n_patients
        self.n_prescribers = n_prescribers
        self.patients: list[tuple] = []
        self.prescribers: list[tuple] = []
        self.fills: list[dict] = []
        self.claims: list[dict] = []
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
            self.patients.append(
                (
                    pid,
                    rng.choice(FIRST),
                    rng.choice(LAST),
                    dob.isoformat(),
                    _phone(rng),
                    f"60{rng.randint(100, 699)}",
                    "Spanish" if rng.random() < 0.12 else "English",
                )
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
                (
                    rid,
                    f"1{rng.randint(100_000_000, 999_999_999)}",
                    first,
                    last,
                    cred,
                    spec,
                    practice,
                    _phone(rng),
                    _phone(rng),
                    f"office@{last.lower()}-practice.example",
                    city,
                    state,
                )
            )
            self.prescriber_profile[rid] = profile
            self.prescriber_patients[rid] = []

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
        base = {rid: rng.lognormvariate(0.75, 0.6) for rid, *_ in self.prescribers}
        base[STORY_PRESCRIBER] = 5.0
        unused = [p[0] for p in self.patients]
        rng.shuffle(unused)
        patient_ids = [p[0] for p in self.patients]
        for rid, *rest in self.prescribers:
            spec = rest[4]
            drugs = SPECIALTIES[spec][0]
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
        payer_idx = rng.choices(range(len(PAYERS)), PAYER_WEIGHTS)[0]
        payer = PAYERS[payer_idx]
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
        price = next(d[3] for d in DRUGS if d[0] == drug_id)
        expected = round(price * days_supply / 30 * rng.uniform(0.92, 1.06), 2)
        billed = round(expected * 1.18, 2)
        fill_id = f"F{rx}-{fill_no:02d}"
        submitted = _ts(fill_date, rng)
        payer_id, _, _, lag_mean, lag_sd, reject_rate = payer
        age = (self.today - fill_date).days

        if profile == "story_declining":
            reject_rate += 0.28
        rejected = force_reject is not None or (not force_paid and rng.random() < reject_rate)
        stalled = False
        claim_rows: list[dict] = []
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
                status = "P" if remitted else "C"
                claim_rows.append(
                    self._claim(
                        fill_id,
                        rx,
                        fill_no,
                        payer_id,
                        submitted,
                        status,
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
                "QUANTITY": 30.0 if days_supply == 30 else 90.0,
                "REFILLS_AUTHORIZED": refills,
                "REFILLS_REMAINING": refills - fill_no,
                "RX_WRITTEN_DATE": _iso(written),
                "RX_EXPIRES_DATE": _iso(expires),
                "STATUS": fill_status,
                "PICKUP_READY_DATE": _iso(fill_date + timedelta(days=1)) if fill_status == "ready_for_pickup" else None,
                "PATIENT_BALANCE": balance,
                "EXPECTED_AMOUNT": expected,
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
    ) -> dict:
        updated = max(t for t in (submitted, adjudicated, remitted) if t is not None)
        return {
            "CLAIM_ID": self._claim_id(),
            "FILL_ID": fill_id,
            "RX_NUMBER": rx,
            "FILL_NUMBER": fill_no,
            "PAYER_ID": payer_id,
            "SUBMITTED_AT": _iso(submitted),
            "RESPONSE_STATUS": status,
            "REJECT_CODE": code,
            "REJECT_MESSAGE": message,
            "BILLED_AMOUNT": billed,
            "EXPECTED_AMOUNT": expected,
            "PAID_AMOUNT": paid,
            "ADJUDICATED_AT": _iso(adjudicated),
            "REMITTED_AT": _iso(remitted),
            "UPDATED_AT": _iso(updated),
        }

    # --- demo stories -----------------------------------------------------------------------------------------
    def _story_rx_4471(self) -> None:
        """Rx 4471-02: refill too soon. Last fill 9 days ago covered 30 days; the payer allows refills at 80%."""
        pid = "PT00042"
        rid = next(p[0] for p in self.prescribers if p[5] == "Women's health")
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
            rid = rng.choice([p[0] for p in self.prescribers if p[5] == "Pediatrics"])
            pid = rng.choice([p[0] for p in self.patients])
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
            self.story.unknown_code_fill_ids.append(f"F{rx}-00")


def build_warehouse(
    path: Path, today: date, *, seed: int = 4471, n_patients: int = 2000, n_prescribers: int = 150
) -> Story:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    b = _Builder(today, seed, n_patients, n_prescribers)
    b.build_dimensions()
    b.build_facts()
    con = sqlite3.connect(path)
    try:
        con.executescript(DDL)
        con.executemany("INSERT INTO DIM_PATIENT VALUES (?,?,?,?,?,?,?)", b.patients)
        con.executemany("INSERT INTO DIM_PRESCRIBER VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", b.prescribers)
        con.executemany(
            "INSERT INTO DIM_PAYER VALUES (?,?,?,?,?)",
            [(p[0], p[1], f"0{i}4471", f"PCN{i}", p[2]) for i, p in enumerate(PAYERS, 1)],
        )
        con.executemany("INSERT INTO DIM_DRUG VALUES (?,?,?,?,?)", [(d[0], d[1], d[2], 1, d[3]) for d in DRUGS])
        cols = list(b.fills[0])
        con.executemany(
            f"INSERT INTO FACT_RXFILL ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",  # noqa: S608  # nosec B608
            [tuple(f[c] for c in cols) for f in b.fills],
        )
        cols = list(b.claims[0])
        con.executemany(
            f"INSERT INTO FACT_CLAIM ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",  # noqa: S608  # nosec B608
            [tuple(c[k] for k in cols) for c in b.claims],
        )
        con.commit()
    finally:
        con.close()
    return b.story
