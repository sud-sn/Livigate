"""Levigate's reference layout: the warehouse already holds the canonical relations under their own names.

Used by the built-in demo warehouse, and by any client whose data team publishes views with these exact names and
columns (the simplest way to onboard: build the seven views in Snowflake, point Levigate at them, done).
"""

from levigate.sources.base import SourceProfile

PROFILE = SourceProfile(
    name="canonical",
    title="Levigate reference layout",
    description="The warehouse exposes Levigate's seven canonical relations directly (tables or views).",
    source_tables={
        name: (name,)
        for name in (
            "FACT_RXFILL",
            "FACT_CLAIM",
            "DIM_PATIENT",
            "DIM_PRESCRIBER",
            "DIM_PAYER",
            "DIM_DRUG",
            "PATIENT_CONSENT",
        )
    },
)
