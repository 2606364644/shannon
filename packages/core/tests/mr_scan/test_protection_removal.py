"""mr_scan.protection_removal — 删防护 LLM 判定 + 降级（spec 2026-09-03 §5.1）。

对齐 llm_taint_analyzer 先例：LLMClient 注入、内联 prompt builder、手写
schema dict（output_format 通道）、失败降级不阻塞。
"""

from supernova_core.mr_scan.protection_removal import (
    build_protection_removal_prompt, detect_removed_protections,
)

_DIFF_SAMPLE = """\
diff --git a/app/utils.py b/app/utils.py
@@ -40,2 +40,1 @@ def handler(req):
-    q = sanitize(q)
     db.execute(q)
"""

_VALID_JSON = """\
{"removed_protections": [
  {"file_path": "app/utils.py", "base_line_no": 41,
   "removed_text": "    q = sanitize(q)", "function_name": "handler",
   "protection_kind": "sanitize", "rationale": "输入清洗被删",
   "confidence": 0.9}
]}"""


async def test_detect_parses_llm_json_into_removed_protections():
    async def client(prompt, **kwargs):
        assert "sanitize" in prompt          # prompt 携带 diff
        return _VALID_JSON

    outcome = await detect_removed_protections(_DIFF_SAMPLE, llm_client=client)

    assert outcome.degraded is False
    assert len(outcome.protections) == 1
    p = outcome.protections[0]
    assert p.file_path == "app/utils.py"
    assert p.base_line_no == 41
    assert p.function_name == "handler"
    assert p.protection_kind == "sanitize"


async def test_detect_degrades_to_empty_when_llm_fails():
    async def client(prompt, **kwargs):
        raise TimeoutError("llm down")

    outcome = await detect_removed_protections(_DIFF_SAMPLE, llm_client=client,
                                               retry_count=0)

    assert outcome.degraded is True
    assert outcome.protections == []


async def test_detect_degrades_when_llm_returns_garbage():
    async def client(prompt, **kwargs):
        return "这不是 JSON"

    outcome = await detect_removed_protections(_DIFF_SAMPLE, llm_client=client)

    assert outcome.degraded is True
    assert outcome.protections == []


async def test_detect_without_client_degrades_silently():
    outcome = await detect_removed_protections(_DIFF_SAMPLE, llm_client=None)

    assert outcome.degraded is True
    assert outcome.protections == []
