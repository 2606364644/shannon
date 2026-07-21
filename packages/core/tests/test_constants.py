from supernova_core.constants import DEFAULT_DELIVERABLES_SUBDIR


def test_default_deliverables_subdir_is_string():
    assert isinstance(DEFAULT_DELIVERABLES_SUBDIR, str)
    assert len(DEFAULT_DELIVERABLES_SUBDIR) > 0


def test_default_deliverables_subdir_is_session_relative():
    """deliverables 落在 session 下，子目录名固定 'deliverables'（无 .shannon 前缀）。"""
    assert DEFAULT_DELIVERABLES_SUBDIR == "deliverables"
