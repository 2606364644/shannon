"""Tests for storage_detector (子项⑤ Task 3) — 硬规则识别四介质读写点.

Mirror test_source_detector.py's fixture idiom (source_provider kwarg +
FuncBlock.source_code, NOT the brief's buggy _text_of/block.text form).
"""
from shannon_core.code_index.models import ParameterSource, FuncBlock
from shannon_core.code_index.parameter_models import SourcePoint
from shannon_core.code_index.storage_models import StorageMedium
from shannon_core.code_index.storage_detector import (
    detect_storage_reads, detect_storage_writes,
)


JAVA_REPO_SAVE = """\
class UserController {
  void create(User u) { repo.save(u); }
  User get(Long id) { return repo.findOneByUserId(id); }
}
"""


def _block(text, name="UserController", start=1):
    """Build a FuncBlock with the corrected field names (Trap 3 fix).

    Brief's _block used non-existent `text=` kwarg; models.py:24-36 defines
    `source_code: str`. Also drops the brief's `name=` (not a FuncBlock field)
    in favor of `function_name=`.
    """
    return FuncBlock(
        id=f"F::{name}",
        file_path=f"{name}.java",
        function_name=name,
        start_line=start,
        end_line=start + text.count("\n"),
        source_code=text,
        parameters=[],
        language="java",
    )


def _provider(block):
    """source_provider kwarg (Trap 2 fix): bytes -> decode in detector."""
    return lambda b: block.source_code.encode("utf-8") if b.id == block.id else None


def test_detect_db_read_source_point():
    """DB read: `findOneByUserId(id)` → SourcePoint(source_type=STORAGE,
    rule_id=java-orm-find, param_name=UserId)."""
    block = _block(JAVA_REPO_SAVE)
    reads = detect_storage_reads([block], parser=None,
                                 entry_point_ids={block.id},
                                 source_provider=_provider(block))
    assert any(
        r.source_type is ParameterSource.STORAGE
        and "UserId" in (r.param_name or r.expression)
        and r.rule_id == "java-orm-find"
        for r in reads
    ), f"no DB read source found; got {reads}"


def test_detect_db_write_storage_write_point():
    """DB write: `repo.save(u)` → StorageWritePoint(medium=DB, callee=save)."""
    block = _block(JAVA_REPO_SAVE)
    writes = detect_storage_writes([block], parser=None,
                                   entry_point_ids={block.id},
                                   source_provider=_provider(block))
    assert any(
        w.medium is StorageMedium.DB
        and w.callee_name == "save"
        and w.rule_id == "java-orm-save"
        for w in writes
    ), f"no DB write point found; got {writes}"


def test_detect_config_read():
    """Config medium read: getProperty("auth.timeout") → SourcePoint.

    Plan Self-Review §1 notes Config medium lacked a standalone unit test.
    Medium tracks via rule_id; SourcePoint itself doesn't carry medium.
    """
    src = 'class C { String t = System.getProperty("auth.timeout"); }\n'
    block = _block(src, name="Config")
    reads = detect_storage_reads([block], parser=None,
                                 entry_point_ids={block.id},
                                 source_provider=_provider(block))
    match = next((r for r in reads if r.rule_id == "java-getproperty"), None)
    assert match is not None, f"no config read found; got {reads}"
    assert match.source_type is ParameterSource.STORAGE
    assert "auth.timeout" in match.expression
    assert match.param_name == "auth.timeout"
