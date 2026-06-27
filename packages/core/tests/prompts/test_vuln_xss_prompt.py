"""B1 补回：vuln-xss prompt 方法论锚点。

Asserts the LLM-track xss prompt carries the server-rendered templates note
(reflected XSS in render calls + JSON.stringify/</script> bypass), restored
from the original TS project (apps/worker/prompts/vuln-xss.txt:138).
"""
from pathlib import Path

PROMPT = Path(__file__).resolve().parents[4] / "prompts" / "vuln-xss.txt"


def test_prompt_has_server_rendered_templates_note():
    """B1 补回：render-context reflected XSS 方法论 + JSON.stringify 绕过示例。"""
    text = PROMPT.read_text()
    assert "server-rendered templates" in text
    assert "ctx.render" in text and "res.render" in text
    assert "JSON.stringify" in text and "</script>" in text
