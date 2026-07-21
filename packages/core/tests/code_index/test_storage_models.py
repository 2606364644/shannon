from supernova_core.code_index.models import ParameterSource, CodeIndex
from supernova_core.code_index.storage_models import StorageWritePoint, StorageMedium


def test_parameter_source_has_storage_flavor():
    assert ParameterSource.STORAGE.value == "storage"


def test_storage_write_point_roundtrip():
    w = StorageWritePoint(
        id="F1::save::7",
        caller_id="entry::UserController.create",
        callee_name="save",
        callee_receiver="repo",
        medium=StorageMedium.DB,
        storage_token="users",
        written_expr="user.name",
        file_path="UserController.java", line=7, column=4,
        rule_id="java-orm-save",
    )
    dumped = w.model_dump_json()
    restored = StorageWritePoint.model_validate_json(dumped)
    assert restored.medium is StorageMedium.DB
    assert restored.storage_token == "users"


def test_code_index_carries_storage_write_points():
    ci = CodeIndex(
        repository="test",
        language="java",
        total_blocks=0,
        total_entry_points=0,
        total_chains=0,
        blocks=[],
        edges=[],
        entry_points=[],
        chains=[],
    )
    assert ci.storage_write_points == []
