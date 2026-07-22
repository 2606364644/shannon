"""Tests for second_order_join: bipartite (medium, token) join of storage
writes × storage-read chains (spec §3.3, Task 6)."""
from supernova_core.code_index.storage_models import StorageWritePoint, StorageMedium
from supernova_core.code_index.parameter_models import SourcePoint
from supernova_core.code_index.models import ParameterSource
from supernova_core.code_index.chain_verdict import CandidateChain
from supernova_core.code_index.second_order_join import (
    extract_second_order_candidates,
    is_resolvable_token,
    _resolve_write_token,
    _resolve_read_table,
)


def _make_write(token: str, *, wid: str = "w1", callee_receiver: str | None = None) -> StorageWritePoint:
    return StorageWritePoint(
        id=wid, caller_id="A", callee_name="save",
        callee_receiver=callee_receiver,
        medium=StorageMedium.DB, storage_token=token,
        written_expr="user.name", file_path="a", line=1, rule_id="r",
    )


def _make_read_src(param_name: str = "users", *, expression: str | None = None) -> SourcePoint:
    return SourcePoint(
        id=f"ep::{param_name}::1",
        entry_point_id="ep",
        param_name=param_name,
        source_type=ParameterSource.STORAGE,
        expression=expression if expression is not None else param_name,
        file_path="b", line=2, rule_id="r",
    )


def _make_chain(source_param: str = "users") -> CandidateChain:
    return CandidateChain(
        vuln_class="xss", flow_id="f1", entry_point_id="ep",
        source_param=source_param, source_type="storage",
        sink_call_site_id="render:1", sink_slot="html_context",
        propagation_steps=[], sanitizer_annotations=[],
        direction_hint="backward", post_sanitize_concat=False,
    )


def test_dynamic_token_unresolvable_not_joined():
    w = _make_write("unresolvable")
    assert not is_resolvable_token(w.storage_token)
    # join with a matching read should produce nothing (unresolvable skipped)
    cands = extract_second_order_candidates([w], [], reads_by_id={})
    assert cands == []


def test_literal_token_joins_write_and_read():
    w = _make_write("users")
    src = _make_read_src("users")
    chain = _make_chain("users")
    cands = extract_second_order_candidates(
        [w], [chain], reads_by_id={"users": src},
    )
    assert len(cands) == 1
    c = cands[0]
    assert c.write is w
    assert c.read is src
    assert c.read_side_chain is chain
    assert c.storage_token == ("db", "users")


def test_cartesian_product_same_token():
    """2 writes × 2 reads with the same literal token → 4 candidates."""
    w1 = _make_write("users", wid="w1")
    w2 = _make_write("users", wid="w2")
    src1 = _make_read_src("users")
    src2 = SourcePoint(
        id="ep::users::9", entry_point_id="ep", param_name="users",
        source_type=ParameterSource.STORAGE, expression="users",
        file_path="b", line=9, rule_id="r",
    )
    # chain keyed by source_param; both reads have param_name="users" so the
    # caller indexes them under the same token via reads_by_id. Exercise the
    # multi-read branch by passing two chains whose source_param disambiguates.
    chain1 = CandidateChain(
        vuln_class="xss", flow_id="f1", entry_point_id="ep",
        source_param="users", source_type="storage",
        sink_call_site_id="render:1", sink_slot="html_context",
        propagation_steps=[], sanitizer_annotations=[],
        direction_hint="backward", post_sanitize_concat=False,
    )
    chain2 = CandidateChain(
        vuln_class="xss", flow_id="f2", entry_point_id="ep",
        source_param="users_alt", source_type="storage",
        sink_call_site_id="render:2", sink_slot="html_context",
        propagation_steps=[], sanitizer_annotations=[],
        direction_hint="backward", post_sanitize_concat=False,
    )
    # reads_by_id keyed by param_name; chain.source_param points at the key
    reads_by_id = {"users": src1, "users_alt": src2}
    # But both must resolve to the same literal token "users" to join w1/w2.
    # src2 has param_name="users_alt" → _read_token falls back to param_name
    # which is "users_alt" ≠ "users". To test the cartesian product on the
    # SAME token, give both reads an expression that resolves to "users".
    src2_lit = SourcePoint(
        id="ep::users_alt::9", entry_point_id="ep", param_name="users_alt",
        source_type=ParameterSource.STORAGE, expression='"users"',
        file_path="b", line=9, rule_id="r",
    )
    reads_by_id = {"users": src1, "users_alt": src2_lit}
    cands = extract_second_order_candidates(
        [w1, w2], [chain1, chain2], reads_by_id=reads_by_id,
    )
    assert len(cands) == 4
    # every candidate pairs one of {w1,w2} with one of {chain1,chain2}
    writes_seen = {c.write.id for c in cands}
    chains_seen = {c.read_side_chain.flow_id for c in cands}
    assert writes_seen == {"w1", "w2"}
    assert chains_seen == {"f1", "f2"}


# ------------------------------------------------------------------
# Task 2 (2026-07-22): write-side table-name resolution
# (_resolve_write_token — @Table annotation / naming convention / receiver).
# ------------------------------------------------------------------

def test_resolve_write_token_from_table_annotation():
    """@Table(name="users") class User + repo.save(u) (generic receiver) →
    single-entity-file heuristic resolves to table `users`."""
    src = (
        '@Entity\n'
        '@Table(name = "users")\n'
        'public class User {\n'
        '}\n'
    )
    w = _make_write("unresolvable", callee_receiver="repo")
    assert _resolve_write_token(w, src) == "users"


def test_resolve_write_token_by_naming_convention():
    """No annotation; receiver `userRepository` → strip Repository → User →
    naming convention (camelCase→snake_case + plural) → users."""
    w = _make_write("unresolvable", callee_receiver="userRepository")
    assert _resolve_write_token(w, None) == "users"


def test_resolve_write_token_unresolvable_when_no_context():
    """Bare save(u) — no receiver, no annotation → keep original token
    (保守: do not fabricate a table name)."""
    w = _make_write("unresolvable", callee_receiver=None)
    assert _resolve_write_token(w, None) == "unresolvable"


def test_resolve_write_token_multi_entity_no_guess():
    """File with TWO @Table mappings + generic-receiver save → do NOT guess
    (保守漏召 > 误连): ambiguous → keep original token."""
    src = (
        '@Table(name = "users")\npublic class User {}\n'
        '@Table(name = "orders")\npublic class Order {}\n'
    )
    w = _make_write("unresolvable", callee_receiver="repo")
    assert _resolve_write_token(w, src) == "unresolvable"


def test_resolve_write_token_keeps_literal_token():
    """A write that already has a literal token (cache.set("user:1")) must
    pass through unchanged — resolution is only for ORM-style unresolvable
    tokens."""
    w = _make_write("user:1", callee_receiver="cache")
    assert _resolve_write_token(w, None) == "user:1"


# ------------------------------------------------------------------
# Task 3 (2026-07-22): read-side table-name resolution (_resolve_read_table).
# ------------------------------------------------------------------

def test_resolve_read_table_from_sql_from():
    """Raw SQL read: `SELECT * FROM users WHERE id = ?` → table `users`
    (distinct param_name proves it's FROM extraction, not the fallback)."""
    src = _make_read_src(param_name="x", expression="SELECT * FROM users WHERE id = ?")
    assert _resolve_read_table(src) == "users"


def test_resolve_read_table_from_sql_into():
    """INSERT INTO users (...) → table `users`."""
    src = _make_read_src(param_name="x", expression="INSERT INTO users (name) VALUES (?)")
    assert _resolve_read_table(src) == "users"


def test_resolve_read_table_orm_returns_param():
    """ORM read (findOneByName) carries no table in its expression → return
    param_name (property name); aligned to the write side via normalisation
    (Task 4). Does not fabricate a table name."""
    src = _make_read_src(param_name="Name", expression="findOneByName(")
    assert _resolve_read_table(src) == "Name"
