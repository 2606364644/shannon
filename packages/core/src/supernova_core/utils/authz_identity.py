"""Privilege-tier derivation and identity-pair matrix for authz multi-identity testing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from supernova_core.models.config import Account, AccountTier

# core config 校验的 account id 唯一事实源（config/parser._validate_accounts 使用）。
ACCOUNT_ID_RE = re.compile(r"^[a-z0-9-]+$")


def slugify_account_id(raw: str, used: set[str]) -> str:
    """清洗任意 id/role → 合法 account slug（ACCOUNT_ID_RE），冲突追加 -2/-3。

    web 侧凭据 ID 形如 cred_db4585ad78（含下划线），直接透传进 scan-config.yaml
    会被 core parser 拒绝，须在展开 accounts[] 时清洗。
    """
    base = "".join(ch if ("a" <= ch <= "z" or "0" <= ch <= "9") else "-" for ch in raw.lower())
    while "--" in base:
        base = base.replace("--", "-")
    base = base.strip("-") or "role"
    slug, n = base, 2
    while slug in used:
        slug = f"{base}-{n}"
        n += 1
    used.add(slug)
    return slug


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
