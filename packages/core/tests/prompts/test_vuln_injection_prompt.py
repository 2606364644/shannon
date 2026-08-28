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


def test_prompt_queue_includes_cross_service_findings():
    text = PROMPT.read_text()
    # 改动 3a：externally_exploitable 是可达性标签，不挡入队
    assert "EVERY" in text and "vulnerable" in text
    # 不再含旧闸门措辞
    assert "ONLY include vulnerabilities where `externally_exploitable = true`" not in text


def test_prompt_step5_marks_cross_service_as_vulnerable():
    text = PROMPT.read_text()
    # 改动 3b：跨服务转发 = vulnerable
    assert "downstream" in text.lower() or "cross-service" in text.lower()
    assert "externally_exploitable=false" in text or "externally_exploitable = false" in text


def test_prompt_does_not_include_static_dataflow_hints():
    text = PROMPT.read_text()
    # 改动 4b：移除 hints include（LLM 轨自给自足）
    assert "@include(shared/_static-dataflow-hints.txt)" not in text


def test_prompt_has_branch_path_exhaustion():
    """B1 补回：分支独立 trace 方法论（防漏报分支间校验不一致的注入）。"""
    text = PROMPT.read_text()
    assert "Branch Path Exhaustion" in text
    assert "conditional branches" in text
    assert "trace every branch independently" in text


def test_prompt_has_blind_extraction_discipline():
    """盲信道提取效率纪律（2026-08-28 NodeGoat-20260828-054537 实证：
    agent 把数值 oracle 布尔化 ?1:0、urllib 无复用、串行逐位、cat maxlen=6000、
    单条命令 timeout 250s×2 段——脚本范式决定了它必须定长超时。纪律治本：
    快信道优先 / ETA 算账 / 信道打包 / 传输提速 / 少提取。）"""
    text = PROMPT.read_text()
    assert "<blind_extraction_discipline>" in text
    # 1. 快信道优先：盲注是 fallback 不是默认
    assert "one-shot exfil" in text
    assert "fallback, not the default" in text
    # 2. 算账：ETA 超 2 分钟换策略，不许定长超时硬跑
    assert "ETA" in text and "Never set a long timeout and grind" in text
    # 3. 信道打包：数值信道别塌缩成 1-bit
    assert "Never collapse it to" in text and "1-bit" in text
    # 4. 传输提速：连接复用 + 删 sleep + 有界并发
    assert "requests.Session" in text and "gratuitous sleeps" in text
    # 5. 少提取：元探测一次拿存在性+大小
    assert "wc -c" in text and "grep -c" in text
