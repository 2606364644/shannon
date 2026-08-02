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


def test_prompt_identifies_markdown_render_sinks():
    """P0(NodeGoat memos 回归):markdown/HTML 渲染库(marked/showdown)及模板函数调用
    {{ helper(var) }} 形态是 XSS sink,须主动识别,不只依赖 pre_recon sink 清单。"""
    text = PROMPT.read_text()
    assert "marked" in text
    assert "showdown" in text
    assert "markdown" in text


def test_prompt_warns_markdown_sanitize_not_valid_sanitizer():
    """P0:markdown 库(marked/showdown)的 sanitize/sanitize:true 选项在旧版(<0.7)可被
    markdown 语法绕过,不得当 valid sanitizer 提前终止 backward trace —— 堵 Step2
    Early Termination 误杀(致 NodeGoat memos {{marked(doc.memo)}} 漏报)。"""
    text = PROMPT.read_text()
    low = text.lower()
    assert "marked" in low
    assert (
        "do not early-terminate" in low
        or "do not treat" in low
        or "not a valid sanitizer" in low
    )
