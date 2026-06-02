from shannon_core.constants import DEFAULT_DELIVERABLES_SUBDIR


def test_default_deliverables_subdir_is_string():
    assert isinstance(DEFAULT_DELIVERABLES_SUBDIR, str)
    assert len(DEFAULT_DELIVERABLES_SUBDIR) > 0


def test_default_deliverables_subdir_starts_with_dot():
    assert DEFAULT_DELIVERABLES_SUBDIR.startswith(".")
    assert "/" in DEFAULT_DELIVERABLES_SUBDIR
