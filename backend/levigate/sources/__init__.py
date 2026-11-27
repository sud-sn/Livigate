"""Registered source profiles. A new client mapping is a new module here, added through a reviewed pull request."""

from __future__ import annotations

from levigate.sources import canonical, pms_snowflake
from levigate.sources.base import (
    CANONICAL_CHECKS,
    CANONICAL_RELATIONS,
    RELATION_MEANING,
    VOCABULARY,
    QualityCheck,
    SourceProfile,
    compose,
)

PROFILES: dict[str, SourceProfile] = {
    p.name: p for p in (canonical.PROFILE, pms_snowflake.PROFILE, pms_snowflake.PROFILE_BI)
}

# Which synthetic warehouse layout each profile reads (demo and tests only).
DEMO_LAYOUT = {"canonical": "canonical", "pms_snowflake": "pms", "pms_snowflake_bi": "pms"}


def get_profile(name: str) -> SourceProfile:
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError(f"unknown source profile '{name}'; registered: {sorted(PROFILES)}") from None


__all__ = [
    "CANONICAL_CHECKS",
    "CANONICAL_RELATIONS",
    "DEMO_LAYOUT",
    "PROFILES",
    "RELATION_MEANING",
    "VOCABULARY",
    "QualityCheck",
    "SourceProfile",
    "compose",
    "get_profile",
]
