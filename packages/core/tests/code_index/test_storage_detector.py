"""Tests for storage_detector (子项⑤ Task 3) — 硬规则识别四介质读写点.

Mirror test_source_detector.py's fixture idiom (source_provider kwarg +
FuncBlock.source_code, NOT the brief's buggy _text_of/block.text form).
"""
from supernova_core.code_index.models import ParameterSource, FuncBlock
from supernova_core.code_index.parameter_models import SourcePoint
from supernova_core.code_index.storage_models import StorageMedium
from supernova_core.code_index.storage_detector import (
    detect_storage_reads, detect_storage_writes,
)


JAVA_REPO_SAVE = """\
class UserController {
  void create(User u) { repo.save(u); }
  User get(Long id) { return repo.findOneByUserId(id); }
}
"""


_EXT = {"java": "java", "python": "py", "go": "go", "php": "php"}


def _block(text, name="UserController", start=1, lang="java"):
    """Build a FuncBlock with the corrected field names (Trap 3 fix).

    ``lang`` selects the block's language (default java) so multi-language
    write-rule tests can reuse the same fixture idiom.
    """
    return FuncBlock(
        id=f"F::{name}",
        file_path=f"{name}.{_EXT.get(lang, 'txt')}",
        function_name=name,
        start_line=start,
        end_line=start + text.count("\n"),
        source_code=text,
        parameters=[],
        language=lang,
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


# ------------------------------------------------------------------
# Task 1 (2026-07-22 二阶召回强化): ORM save receiver capture +
# Python/Go/PHP write-rule coverage.
# ------------------------------------------------------------------

def test_orm_save_captures_receiver():
    """ORM save with receiver: `repo.save(u)` → callee_receiver == "repo"
    (was always None before — root cause A). written_expr must be the entity
    arg `u`, not the truncated call text."""
    block = _block("class C { void s(User u){ repo.save(u); } }\n")
    writes = detect_storage_writes([block], parser=None,
                                   entry_point_ids={block.id},
                                   source_provider=_provider(block))
    save_w = next(w for w in writes if w.rule_id == "java-orm-save")
    assert save_w.callee_receiver == "repo"
    assert save_w.callee_name == "save"
    assert save_w.written_expr == "u"


def test_orm_save_bare_no_receiver():
    """Bare `save(u)` (no receiver) → callee_receiver stays None (保守兼容)."""
    block = _block("class C { void s(User u){ save(u); } }\n", name="Bare")
    writes = detect_storage_writes([block], parser=None,
                                   entry_point_ids={block.id},
                                   source_provider=_provider(block))
    save_w = next(w for w in writes if w.rule_id == "java-orm-save")
    assert save_w.callee_receiver is None


def test_python_sqlalchemy_add_write():
    """Python SQLAlchemy ORM write: `session.add(u)` → StorageWritePoint(DB,
    callee_receiver=session, written_expr=u)."""
    block = _block("def s(u):\n    session.add(u)\n", name="PyAdd", lang="python")
    writes = detect_storage_writes([block], parser=None,
                                   entry_point_ids={block.id},
                                   source_provider=_provider(block))
    add_w = next(w for w in writes if w.rule_id == "python-sqlalchemy-add")
    assert add_w.medium is StorageMedium.DB
    assert add_w.callee_receiver == "session"
    assert add_w.written_expr == "u"


def test_python_cache_set_write():
    """Python cache write: `cache.set("user:1", val)` → literal token
    `user:1` (cache key), written_expr=val."""
    block = _block("cache.set(\"user:1\", val)\n", name="PyCache", lang="python")
    writes = detect_storage_writes([block], parser=None,
                                   entry_point_ids={block.id},
                                   source_provider=_provider(block))
    cw = next(w for w in writes if w.rule_id == "python-cache-set")
    assert cw.medium is StorageMedium.CACHE
    assert cw.storage_token == "user:1"
    assert cw.written_expr == "val"


def test_go_gorm_write():
    """Go gorm write: `db.Create(&user)` → StorageWritePoint(DB,
    callee_receiver=db)."""
    block = _block("func s() { db.Create(&user) }\n", name="GoGorm", lang="go")
    writes = detect_storage_writes([block], parser=None,
                                   entry_point_ids={block.id},
                                   source_provider=_provider(block))
    gw = next(w for w in writes if w.rule_id == "go-gorm-write")
    assert gw.medium is StorageMedium.DB
    assert gw.callee_receiver == "db"


def test_php_eloquent_create_write():
    """PHP Eloquent static write: `User::create($d)` → StorageWritePoint(DB,
    callee_receiver=User — the model class, table-name inference线索)."""
    block = _block("User::create($d);\n", name="PhpElo", lang="php")
    writes = detect_storage_writes([block], parser=None,
                                   entry_point_ids={block.id},
                                   source_provider=_provider(block))
    pw = next(w for w in writes if w.rule_id == "php-eloquent-create")
    assert pw.medium is StorageMedium.DB
    assert pw.callee_receiver == "User"


def test_php_db_table_insert_write():
    """PHP query-builder write: `DB::table("users")->insert($d)` → literal
    token `users` (table name, resolvable for join)."""
    block = _block('DB::table("users")->insert($d);\n', name="PhpQB", lang="php")
    writes = detect_storage_writes([block], parser=None,
                                   entry_point_ids={block.id},
                                   source_provider=_provider(block))
    pw = next(w for w in writes if w.rule_id == "php-db-table-insert")
    assert pw.medium is StorageMedium.DB
    assert pw.storage_token == "users"
