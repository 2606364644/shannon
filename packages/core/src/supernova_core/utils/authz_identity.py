"""Privilege-tier derivation and identity-pair matrix for authz multi-identity testing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from supernova_core.models.config import Account, AccountTier


def derive_privilege_tier(role: str | None, high_priv_names: list[str]) -> AccountTier:
    """Return 'high' if role (case/stripped-normalized) is in high_priv_names, else 'low'."""
    if not role:
        return "low"
    names = {n.lower().strip() for n in (high_priv_names or [])}
    return "high" if role.lower().strip() in names else "low"


@dataclass(frozen=True)
class ComparisonPair:
    attacker_id: str
    baseline_id: str
    kind: Literal["vertical", "horizontal"]


def build_comparison_matrix(identities: list[Account]) -> list[ComparisonPair]:
    """Build ordered (attacker, baseline) pairs: low×high=vertical, low×low=horizontal."""
    if len(identities) < 2:
        return []
    highs = [i for i in identities if i.tier == "high"]
    lows = [i for i in identities if i.tier == "low"]
    pairs: list[ComparisonPair] = []
    for attacker in lows:                       # vertical: low attacker × high baseline
        for baseline in highs:
            pairs.append(ComparisonPair(attacker.id, baseline.id, "vertical"))
    for i, a in enumerate(lows):                 # horizontal: low 两两（双向）
        for b in lows[i + 1:]:
            pairs.append(ComparisonPair(a.id, b.id, "horizontal"))
            pairs.append(ComparisonPair(b.id, a.id, "horizontal"))
    return pairs
