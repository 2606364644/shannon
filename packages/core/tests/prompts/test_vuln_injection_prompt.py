"""Task 6 (spec 改动 1.1 + 2): vuln-injection prompt content assertions.

Asserts the LLM-track injection prompt carries:
- 改动 1.1: a per-language ORM Raw / string-built sink checklist (highest-miss class)
  + dynamic-identifier + indirect-command guidance.
- 改动 2: a contract that derives candidate sources from the recon deliverable's
  REAL attack-surface sections + active repo-wide grep, NOT from a non-existent
  "Section 7. Injection Sources", and does NOT reference deterministic hints.

Task 7 will append further assertions to this file (改动 4b removes the
`@include(shared/_static-dataflow-hints.txt)` line); additions here are kept
as independent top-level test functions so Task 7 can append cleanly.
"""
from pathlib import Path

# Anchor on this file's location so the path resolves regardless of pytest's
# cwd. Mirrors the existing pattern in tests/prompts/test_static_hints_render.py
# (parents[4] = repo root, which holds the prompts/ dir).
PROMPT = Path(__file__).resolve().parents[4] / "prompts" / "vuln-injection.txt"


def test_prompt_has_per_language_orm_raw_checklist():
    text = PROMPT.read_text()
    # 改动 1.1：per-language ORM Raw 清单
    assert "db.Raw" in text and "gorm.Expr" in text
    assert "knex.raw" in text and "sequelize.query" in text
    assert "createNativeQuery" in text and "whereRaw" in text
    # 动态标识符
    assert "ORDER BY" in text and "identifier" in text.lower()
    # 间接命令执行
    assert "shell=True" in text and "sh -c" in text


def test_prompt_contract_does_not_reference_nonexistent_section7():
    text = PROMPT.read_text()
    # 改动 2：不再从不存在的 "Section 7. Injection Sources" 派生
    assert "7. Injection Sources" not in text
    # 改为从真实攻击面 section + grep 派生
    assert "External Entry Points" in text or "Data Flow Security" in text
    assert "grep" in text.lower()
    # 不引确定性 hints（LLM 轨自给自足）
    assert "static_dataflow_hints" not in text
