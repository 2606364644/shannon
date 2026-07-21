from supernova_core.code_index.patterns import OWNERSHIP_PREDICATE_RE


def test_ownership_predicate_matches_user_id_where():
    src = "const item = await Model.where('user_id', req.user.id)"
    assert OWNERSHIP_PREDICATE_RE.search(src) is not None


def test_ownership_predicate_matches_find_by_owner():
    src = "await repo.findByOwnerId(ctx.state.user.id)"
    assert OWNERSHIP_PREDICATE_RE.search(src) is not None


def test_ownership_predicate_no_false_positive_on_plain_code():
    src = "function add(a, b) { return a + b; }"
    assert OWNERSHIP_PREDICATE_RE.search(src) is None
