"""Mapping for a pharmacy management system landed in Snowflake by Talend (normalised PMS tables).

This is the template for a real client mapping. Each canonical relation is plain ANSI SQL over the client's tables,
and every choice a real data team has to make is written down here, next to the SQL that makes it:

* Talend reloads can land the same fill twice → keep the latest ``LOAD_TS`` per ``FILL_KEY`` (``SRC_FILL``).
* Voided fills are soft-deleted (``IS_DELETED = 'Y'``) → excluded.
* A response can carry up to five reject codes → the playbook routes on the first (``REJECT_SEQ = 1``).
* Secondary-payer (COB) claims are out of scope for the pilot → only claims to the plan billed on the fill.
* "Paid" means money arrived: a payable response becomes Paid once an 835 remittance line exists, else Captured.
  Split 835 payments are summed; the latest payment date is the remittance date.
* The change marker is Talend's ``LOAD_TS`` (when a row landed), not the PMS event time, so a row that lands late
  is never skipped by the incremental pull.
* Expected reimbursement ports the client's BI formula (the lesser of U&C and AWP x quantity x contract rate +
  dispensing fee, using the contract in force on the fill date). The ``pms_snowflake_bi`` variant instead takes the
  value straight from the client's own ``VW_CLAIM_STATUS`` view — the parity report shows the two agree.
* Consent comes from the PMS communication preferences; e-mail preferences are not used.
"""

from __future__ import annotations

from levigate.sources.base import QualityCheck, SourceProfile

_EXPECTED_FORMULA = """ROUND(CASE
      WHEN fo.AWP_PER_UNIT * f.QTY_DISPENSED * c.CONTRACT_RATE + c.DISPENSING_FEE < f.USUAL_CUSTOMARY_AMT
      THEN fo.AWP_PER_UNIT * f.QTY_DISPENSED * c.CONTRACT_RATE + c.DISPENSING_FEE
      ELSE f.USUAL_CUSTOMARY_AMT END, 2)"""

HELPERS = {
    # Latest landed copy of each fill, voids excluded.
    "SRC_FILL": """
SELECT d.* FROM (
  SELECT x.*, ROW_NUMBER() OVER (PARTITION BY x.FILL_KEY ORDER BY x.LOAD_TS DESC) AS SRC_RN FROM RX_FILL x
) d WHERE d.SRC_RN = 1 AND d.IS_DELETED = 'N'""",
    # Fill with everything the canonical relations need: keys, contract in force, expected reimbursement.
    "SRC_FILL_EXPECTED": f"""
SELECT f.FILL_KEY, f.PLAN_KEY_BILLED, {_EXPECTED_FORMULA} AS EXPECTED_AMOUNT
FROM SRC_FILL f
JOIN RX r ON r.RX_KEY = f.RX_KEY
JOIN FORMULA fo ON fo.FORMULA_KEY = r.FORMULA_KEY
LEFT JOIN PLAN_CONTRACT c ON c.PLAN_KEY = f.PLAN_KEY_BILLED AND f.FILL_DT BETWEEN c.EFF_DT AND c.TERM_DT""",  # noqa: S608
    # 835 payments per fill (split payments summed).
    "SRC_REMIT": """
SELECT RX_NBR, FILL_NBR, SUM(CLAIM_PAID_AMT) AS PAID_TO_DATE, MAX(PAYMENT_DT) AS LAST_PAYMENT_DT,
       MAX(LOAD_TS) AS LAST_LOAD_TS
FROM REMIT_835 GROUP BY RX_NBR, FILL_NBR""",
    # One phone per patient: the primary flag first, then mobile, then the most recently loaded.
    "SRC_PHONE": """
SELECT d.PATIENT_KEY, d.PHONE_NBR FROM (
  SELECT p.*, ROW_NUMBER() OVER (
    PARTITION BY p.PATIENT_KEY
    ORDER BY CASE WHEN p.IS_PRIMARY = 'Y' THEN 0 ELSE 1 END, CASE WHEN p.PHONE_TYPE = 'MOBILE' THEN 0 ELSE 1 END,
             p.LOAD_TS DESC) AS SRC_RN
  FROM PATIENT_PHONE p
) d WHERE d.SRC_RN = 1""",
    # The client's BI views, for the parity report.
    "BI_CLAIM_STATUS": """
SELECT RX_NBR AS RX_NUMBER, FILL_NBR AS FILL_NUMBER, FILL_DT AS FILL_DATE, CURRENT_STATUS AS BI_STATUS,
       EXPECTED_REIMB AS BI_EXPECTED, PAID_TO_DATE AS BI_PAID, DAYS_TO_PAY AS BI_DAYS_TO_PAY
FROM VW_CLAIM_STATUS""",
    "BI_PRESCRIBER_MONTHLY": """
SELECT PRESCRIBER_CD AS PRESCRIBER_ID, FILL_MONTH, RX_COUNT FROM VW_PRESCRIBER_MONTHLY""",
}


def _fact_rxfill(expected_sql: str, expected_join: str) -> str:
    # Called only at import time with the constants below; no runtime input reaches this SQL.
    return f"""
SELECT CAST(f.FILL_KEY AS VARCHAR) AS FILL_ID, r.RX_NBR AS RX_NUMBER, f.FILL_NBR AS FILL_NUMBER,
       pt.PATIENT_NO AS PATIENT_ID, pr.PRESCRIBER_CD AS PRESCRIBER_ID, fo.FORMULA_CD AS DRUG_ID,
       py.PAYER_CD AS PAYER_ID, s.STORE_NAME AS LOCATION, f.FILL_DT AS FILL_DATE, f.DAYS_SUPPLY,
       f.QTY_DISPENSED AS QUANTITY, r.REFILLS_AUTH AS REFILLS_AUTHORIZED, f.REFILLS_REMAINING,
       r.WRITTEN_DT AS RX_WRITTEN_DATE, r.EXPIRE_DT AS RX_EXPIRES_DATE,
       CASE f.FILL_STATUS_CD WHEN 'S' THEN 'picked_up' WHEN 'W' THEN 'ready_for_pickup' ELSE 'on_hold' END AS STATUS,
       CASE WHEN f.FILL_STATUS_CD = 'W' THEN f.WAITING_BIN_DT END AS PICKUP_READY_DATE,
       f.PATIENT_PAY_AMT - f.PATIENT_PAID_AMT AS PATIENT_BALANCE,
       {expected_sql} AS EXPECTED_AMOUNT
FROM SRC_FILL f
JOIN RX r ON r.RX_KEY = f.RX_KEY
JOIN PATIENT pt ON pt.PATIENT_KEY = r.PATIENT_KEY
JOIN PRESCRIBER pr ON pr.PRESCRIBER_KEY = r.PRESCRIBER_KEY
JOIN FORMULA fo ON fo.FORMULA_KEY = r.FORMULA_KEY
JOIN STORE s ON s.STORE_ID = r.STORE_ID
JOIN PLAN pl ON pl.PLAN_KEY = f.PLAN_KEY_BILLED
JOIN PAYER py ON py.PAYER_KEY = pl.PAYER_KEY
{expected_join}"""  # noqa: S608


def _fact_claim(expected_source: str) -> str:
    # Called only at import time with the constants below; no runtime input reaches this SQL.
    return f"""
SELECT CAST(t.TXN_KEY AS VARCHAR) AS CLAIM_ID, CAST(t.FILL_KEY AS VARCHAR) AS FILL_ID, r.RX_NBR AS RX_NUMBER,
       f.FILL_NBR AS FILL_NUMBER, py.PAYER_CD AS PAYER_ID, t.TXN_TS AS SUBMITTED_AT,
       CASE WHEN t.TXN_TYPE_CD = 'B2' AND t.RESPONSE_STATUS_CD = 'A' THEN 'X'
            WHEN t.RESPONSE_STATUS_CD = 'R' THEN 'R'
            WHEN t.RESPONSE_STATUS_CD = 'P' AND m.LAST_PAYMENT_DT IS NOT NULL THEN 'P'
            WHEN t.RESPONSE_STATUS_CD = 'P' THEN 'C' END AS RESPONSE_STATUS,
       rj.REJECT_CD AS REJECT_CODE, ref.REJECT_DESC AS REJECT_MESSAGE, t.SUBMITTED_AMT AS BILLED_AMOUNT,
       e.EXPECTED_AMOUNT, t.TOTAL_PAID_AMT AS PAID_AMOUNT,
       t.RESPONSE_TS AS ADJUDICATED_AT,
       CASE WHEN t.RESPONSE_STATUS_CD = 'P' THEN m.LAST_PAYMENT_DT END AS REMITTED_AT,
       CASE WHEN t.RESPONSE_STATUS_CD = 'P' AND m.LAST_LOAD_TS > t.LOAD_TS THEN m.LAST_LOAD_TS
            ELSE t.LOAD_TS END AS UPDATED_AT
FROM CLAIM_TXN t
JOIN SRC_FILL f ON f.FILL_KEY = t.FILL_KEY AND f.PLAN_KEY_BILLED = t.PLAN_KEY
JOIN RX r ON r.RX_KEY = f.RX_KEY
JOIN PLAN pl ON pl.PLAN_KEY = t.PLAN_KEY
JOIN PAYER py ON py.PAYER_KEY = pl.PAYER_KEY
{expected_source}
LEFT JOIN CLAIM_REJECT rj ON rj.TXN_KEY = t.TXN_KEY AND rj.REJECT_SEQ = 1
LEFT JOIN REJECT_CODE_REF ref ON ref.REJECT_CD = rj.REJECT_CD
LEFT JOIN SRC_REMIT m ON m.RX_NBR = r.RX_NBR AND m.FILL_NBR = f.FILL_NBR"""  # noqa: S608


_DIMENSIONS = {
    "DIM_PATIENT": """
SELECT pt.PATIENT_NO AS PATIENT_ID, pt.FIRST_NM AS FIRST_NAME, pt.LAST_NM AS LAST_NAME, pt.BIRTH_DT AS DOB,
       ph.PHONE_NBR AS PHONE, pt.ZIP_CD AS ZIP,
       CASE pt.LANGUAGE_CD WHEN 'SPA' THEN 'Spanish' WHEN 'ENG' THEN 'English' ELSE pt.LANGUAGE_CD END
         AS PREFERRED_LANGUAGE
FROM PATIENT pt LEFT JOIN SRC_PHONE ph ON ph.PATIENT_KEY = pt.PATIENT_KEY
WHERE pt.IS_DELETED = 'N'""",
    "DIM_PRESCRIBER": """
SELECT p.PRESCRIBER_CD AS PRESCRIBER_ID, p.NPI, p.FIRST_NM AS FIRST_NAME, p.LAST_NM AS LAST_NAME,
       p.CREDENTIAL_CD AS CREDENTIAL, p.SPECIALTY_DESC AS SPECIALTY, p.PRACTICE_NM AS PRACTICE_NAME,
       a.PHONE_NBR AS PHONE, a.FAX_NBR AS FAX, p.EMAIL_ADDR AS EMAIL, a.CITY_NM AS CITY, a.STATE_CD AS STATE
FROM PRESCRIBER p LEFT JOIN PRESCRIBER_ADDRESS a ON a.PRESCRIBER_KEY = p.PRESCRIBER_KEY AND a.ADDR_TYPE = 'PRACTICE'
WHERE p.IS_DELETED = 'N'""",
    "DIM_PAYER": """
SELECT py.PAYER_CD AS PAYER_ID, py.PAYER_NM AS PAYER_NAME, pl.BIN_NBR AS BIN, pl.PCN,
       CASE py.PLAN_TYPE_CD WHEN 'COMM' THEN 'Commercial' WHEN 'MCR_D' THEN 'Medicare Part D'
            WHEN 'MCD' THEN 'Medicaid' WHEN 'WC' THEN 'Workers'' comp' ELSE 'Other' END AS PLAN_TYPE
FROM PAYER py JOIN PLAN pl ON pl.PAYER_KEY = py.PAYER_KEY AND pl.COVERAGE_ROLE = 'PRIMARY'""",
    "DIM_DRUG": """
SELECT FORMULA_CD AS DRUG_ID, FORMULA_NM AS DRUG_NAME, DOSAGE_FORM AS FORM,
       CASE WHEN IS_COMPOUND = 'Y' THEN 1 ELSE 0 END AS IS_COMPOUND, AWP_PER_UNIT * 30 AS UNIT_PRICE
FROM FORMULA""",
    "PATIENT_CONSENT": """
SELECT pt.PATIENT_NO AS PATIENT_ID, CASE c.CHANNEL_CD WHEN 'PHONE' THEN 'call' ELSE 'sms' END AS CHANNEL,
       CASE WHEN c.OPT_IN_FLAG = 'Y' THEN 'granted' ELSE 'revoked' END AS STATUS,
       LOWER(c.CONSENT_TYPE_CD) AS CONSENT_TYPE, c.UPDATED_TS AS RECORDED_AT, c.UPDATED_TS AS UPDATED_AT
FROM PATIENT_COMM_PREF c JOIN PATIENT pt ON pt.PATIENT_KEY = c.PATIENT_KEY
WHERE c.CHANNEL_CD IN ('PHONE', 'SMS')""",
}

RAW_CHECKS = (
    QualityCheck(
        "reload_duplicates_removed",
        "Duplicate fill rows from reloads (latest copy kept)",
        "info",
        "SELECT COUNT(*) - COUNT(DISTINCT FILL_KEY) AS N FROM RX_FILL",
        "Talend reloads landed older copies of these fills; the mapping keeps the latest LOAD_TS.",
    ),
    QualityCheck(
        "soft_deleted_fills",
        "Voided fills excluded",
        "info",
        "SELECT COUNT(*) AS N FROM RX_FILL WHERE IS_DELETED = 'Y'",
        "Fills entered in error and voided in the PMS are not worked.",
    ),
    QualityCheck(
        "fills_dropped_by_mapping",
        "Fills the mapping could not link to a patient, prescriber or plan",
        "error",
        "SELECT (SELECT COUNT(*) FROM SRC_FILL) - (SELECT COUNT(*) FROM FACT_RXFILL) AS N",
        "A missing patient, prescriber, formula, store or plan row drops the fill from every screen.",
    ),
    QualityCheck(
        "orphan_claim_transactions",
        "Claim transactions whose fill has not landed yet",
        "warning",
        "SELECT COUNT(*) AS N FROM CLAIM_TXN t WHERE NOT EXISTS "
        "(SELECT 1 FROM RX_FILL f WHERE f.FILL_KEY = t.FILL_KEY)",
        "Usually the fill row arrives in a later Talend load; these claims are picked up then.",
    ),
    QualityCheck(
        "secondary_payer_claims",
        "Secondary-payer (COB) claims excluded",
        "info",
        "SELECT COUNT(*) AS N FROM CLAIM_TXN t JOIN SRC_FILL f ON f.FILL_KEY = t.FILL_KEY "
        "WHERE t.PLAN_KEY <> f.PLAN_KEY_BILLED",
        "Coordination of benefits is out of scope for the pilot; only the billed (primary) plan is worked.",
    ),
    QualityCheck(
        "secondary_reject_codes",
        "Rejections with more than one reject code",
        "info",
        "SELECT COUNT(DISTINCT TXN_KEY) AS N FROM CLAIM_REJECT WHERE REJECT_SEQ > 1",
        "The playbook routes on the first code; the others are kept in the source.",
    ),
    QualityCheck(
        "split_remittances",
        "Fills paid across more than one 835 payment",
        "info",
        "SELECT COUNT(*) AS N FROM (SELECT RX_NBR, FILL_NBR FROM REMIT_835 GROUP BY RX_NBR, FILL_NBR "
        "HAVING COUNT(*) > 1) d",
        "Payments are summed; the last payment date counts as the remittance date.",
    ),
    QualityCheck(
        "remits_without_fill",
        "835 payment lines that match no fill",
        "warning",
        "SELECT COUNT(*) AS N FROM REMIT_835 m WHERE NOT EXISTS (SELECT 1 FROM SRC_FILL f "
        "JOIN RX r ON r.RX_KEY = f.RX_KEY WHERE r.RX_NBR = m.RX_NBR AND f.FILL_NBR = m.FILL_NBR)",
        "Money received for a fill Levigate cannot see — check the Rx and fill numbers on the 835.",
    ),
    QualityCheck(
        "fills_without_contract",
        "Fills with no contract in force on the fill date",
        "error",
        "SELECT COUNT(*) AS N FROM SRC_FILL f WHERE NOT EXISTS (SELECT 1 FROM PLAN_CONTRACT c "
        "WHERE c.PLAN_KEY = f.PLAN_KEY_BILLED AND f.FILL_DT BETWEEN c.EFF_DT AND c.TERM_DT)",
        "Expected reimbursement cannot be computed without contract terms.",
    ),
    QualityCheck(
        "remit_amount_mismatch",
        "Fills where 835 payments differ from the adjudicated amount",
        "warning",
        "SELECT COUNT(*) AS N FROM FACT_CLAIM c JOIN SRC_REMIT m ON m.RX_NBR = c.RX_NUMBER "
        "AND m.FILL_NBR = c.FILL_NUMBER WHERE c.RESPONSE_STATUS = 'P' "
        "AND (m.PAID_TO_DATE - c.PAID_AMOUNT > 0.01 OR c.PAID_AMOUNT - m.PAID_TO_DATE > 0.01)",
        "Partial payments or recoupments: worth a look before counting dollars as recovered.",
    ),
    QualityCheck(
        "email_preferences_ignored",
        "E-mail contact preferences (not used)",
        "info",
        "SELECT COUNT(*) AS N FROM PATIENT_COMM_PREF WHERE CHANNEL_CD = 'EMAIL'",
        "Levigate uses call and text consent only in the pilot.",
    ),
)

SOURCE_TABLES = {
    "FACT_RXFILL": ("RX_FILL", "RX", "PATIENT", "PRESCRIBER", "FORMULA", "STORE", "PLAN", "PAYER", "PLAN_CONTRACT"),
    "FACT_CLAIM": (
        "CLAIM_TXN",
        "CLAIM_REJECT",
        "REJECT_CODE_REF",
        "REMIT_835",
        "RX_FILL",
        "RX",
        "PLAN",
        "PAYER",
        "FORMULA",
        "PLAN_CONTRACT",
    ),
    "DIM_PATIENT": ("PATIENT", "PATIENT_PHONE"),
    "DIM_PRESCRIBER": ("PRESCRIBER", "PRESCRIBER_ADDRESS"),
    "DIM_PAYER": ("PAYER", "PLAN"),
    "DIM_DRUG": ("FORMULA",),
    "PATIENT_CONSENT": ("PATIENT_COMM_PREF", "PATIENT"),
}

_CONTRACT_JOIN = (
    "LEFT JOIN PLAN_CONTRACT c ON c.PLAN_KEY = f.PLAN_KEY_BILLED AND f.FILL_DT BETWEEN c.EFF_DT AND c.TERM_DT"
)

PROFILE = SourceProfile(
    name="pms_snowflake",
    title="Pharmacy system via Talend (formula mapped)",
    description="Normalised PMS tables landed in Snowflake by Talend; expected reimbursement ports the client's "
    "BI formula into the mapping.",
    relations={
        "FACT_RXFILL": _fact_rxfill(_EXPECTED_FORMULA, _CONTRACT_JOIN),
        "FACT_CLAIM": _fact_claim("JOIN SRC_FILL_EXPECTED e ON e.FILL_KEY = t.FILL_KEY"),
        **_DIMENSIONS,
    },
    helpers=HELPERS,
    source_tables=SOURCE_TABLES,
    quality_checks=RAW_CHECKS,
    parity=("BI_CLAIM_STATUS", "BI_PRESCRIBER_MONTHLY"),
    notes=(
        "Duplicates from Talend reloads: latest LOAD_TS per FILL_KEY wins.",
        "Rejections route on the first reject code; up to four more stay in the source.",
        "Only claims to the plan billed on the fill are used (no COB in the pilot).",
        "Paid = an 835 payment exists; otherwise a payable response is Captured.",
        "Expected reimbursement = lesser of U&C and AWP x quantity x contract rate + dispensing fee.",
    ),
)

# Same mapping, but expected reimbursement is read from the client's own BI view instead of re-computed.
PROFILE_BI = SourceProfile(
    name="pms_snowflake_bi",
    title="Pharmacy system via Talend (expected amount from the client's BI view)",
    description="Like pms_snowflake, but EXPECTED_AMOUNT comes straight from VW_CLAIM_STATUS.EXPECTED_REIMB — "
    "Levigate reuses the client's BI logic instead of re-implementing it.",
    relations={
        "FACT_RXFILL": _fact_rxfill("bi.EXPECTED_AMOUNT", "LEFT JOIN BI_EXPECTED bi ON bi.FILL_KEY = f.FILL_KEY"),
        "FACT_CLAIM": _fact_claim("JOIN BI_EXPECTED e ON e.FILL_KEY = t.FILL_KEY"),
        **_DIMENSIONS,
    },
    helpers={
        **HELPERS,
        # The client's own expected-reimbursement logic, read from their BI view (one row per fill).
        "BI_EXPECTED": "SELECT FILL_KEY, EXPECTED_REIMB AS EXPECTED_AMOUNT FROM VW_CLAIM_STATUS",
    },
    source_tables={
        **SOURCE_TABLES,
        "FACT_RXFILL": (*SOURCE_TABLES["FACT_RXFILL"], "VW_CLAIM_STATUS"),
        "FACT_CLAIM": (*SOURCE_TABLES["FACT_CLAIM"], "VW_CLAIM_STATUS"),
    },
    quality_checks=RAW_CHECKS,
    parity=("BI_CLAIM_STATUS", "BI_PRESCRIBER_MONTHLY"),
    notes=(*PROFILE.notes[:-1], "Expected reimbursement = VW_CLAIM_STATUS.EXPECTED_REIMB (client BI view)."),
)
