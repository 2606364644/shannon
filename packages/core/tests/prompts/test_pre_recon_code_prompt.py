"""P1 锚点:pre-recon Sink Hunter 识别 markdown 渲染 sink + 全局 autoescape 配置。

背景:NodeGoat memos stored XSS({{ marked(doc.memo) }})漏报的 recon 根因——
Sink Hunter 只区分 escaped/unescaped 模板指令语法,不识 markdown 渲染库、
不识模板函数调用形态、不查模板引擎全局 autoescape:false 配置(Swig {{}} 在
autoescape:false 下是 unescaped,但语法上像 escaped → 误判安全)。
"""
from pathlib import Path

PROMPT = Path(__file__).resolve().parents[4] / "prompts" / "pre-recon-code.txt"


def test_pre_recon_identifies_markdown_render_sinks():
    """P1:Sink Hunter 须识别 markdown 渲染库(marked/showdown)与模板函数调用
    {{ helper(var) }} 形态为 XSS sink(非仅 innerHTML/document.write)。"""
    text = PROMPT.read_text()
    assert "marked" in text
    assert "markdown" in text
    assert "showdown" in text or "markdown-it" in text


def test_pre_recon_checks_global_autoescape_config():
    """P1:模板引擎全局 autoescape:false 会使 {{}} 变 unescaped,Sink Hunter 须查
    全局配置而非只看指令语法(致 NodeGoat firstName 系列 {{}} 被误判 escaped)。"""
    text = PROMPT.read_text()
    assert "autoescape" in text.lower()
