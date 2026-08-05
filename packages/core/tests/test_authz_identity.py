from supernova_core.utils.authz_identity import (
    derive_privilege_tier, build_comparison_matrix, ComparisonPair)
from supernova_core.models.config import Account, Credentials

class TestDerivePrivilegeTier:
    def test_admin_is_high(self):
        assert derive_privilege_tier("admin", ["admin"]) == "high"

    def test_user_is_low(self):
        assert derive_privilege_tier("user", ["admin"]) == "low"

    def test_case_insensitive(self):
        assert derive_privilege_tier(" Admin ", ["admin"]) == "high"

    def test_none_role_is_low(self):
        assert derive_privilege_tier(None, ["admin"]) == "low"

    def test_empty_high_priv_names_all_low(self):
        assert derive_privilege_tier("admin", []) == "low"

    def test_custom_high_priv_names(self):
        assert derive_privilege_tier("root", ["root", "superuser"]) == "high"

def _acct(id_, role, tier):
    return Account(id=id_, credentials=Credentials(username=id_), role=role, tier=tier)

class TestBuildComparisonMatrix:
    def test_empty_identities(self):
        assert build_comparison_matrix([]) == []

    def test_single_identity_returns_empty(self):
        assert build_comparison_matrix([_acct("admin", "admin", "high")]) == []

    def test_admin_plus_two_users(self):
        admin = _acct("admin", "admin", "high")
        u1 = _acct("user1", "user", "low")
        u2 = _acct("user2", "user", "low")
        pairs = build_comparison_matrix([admin, u1, u2])
        kinds = {(p.attacker_id, p.baseline_id, p.kind) for p in pairs}
        # 垂直: user1→admin, user2→admin
        assert ("user1", "admin", "vertical") in kinds
        assert ("user2", "admin", "vertical") in kinds
        # 水平: user1↔user2 (两个方向)
        assert ("user1", "user2", "horizontal") in kinds
        assert ("user2", "user1", "horizontal") in kinds

    def test_no_pair_within_same_tier_high(self):
        a1 = _acct("a1", "admin", "high")
        a2 = _acct("a2", "admin", "high")
        assert build_comparison_matrix([a1, a2]) == []
