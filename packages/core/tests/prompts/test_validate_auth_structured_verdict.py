"""validate-authentication prompt 必须引导 agent 产出结构化 login_success verdict。

背景（2026-08-14 NodeGoat 组合扫描假阴性，scan NodeGoat-20260814-054629）：GLM
（openai 引擎）登录操作完全成功（4 milestone 全 complete + auth-state.json 已保存），
但 prompt 无结构化收尾指令 + 原 line38「Do NOT hand-write a summary JSON」抑制 JSON
输出 → agent 输出 Markdown 总结 → map_run_result 提取 structured_output=None →
validate_authentication 判「no structured login_success verdict」→ t0 预验证误判
失败 fail-fast，白盒/黑盒都没跑。

此 prompt 由三处共享：t0 组合扫描预验证 / t2 黑盒 auth 阶段 / 认证管理页「测试登录」。
本测试锁定 prompt 的结构化输出契约，防止措辞回退再次抑制 verdict JSON 输出。
"""
from pathlib import Path

PROMPT = Path(__file__).resolve().parents[4] / "prompts" / "validate-authentication.txt"


def test_prompt_has_final_output_verdict_instruction():
    """prompt 必须有明确的最终输出指令：最终回复是纯 JSON verdict（含
    login_success），无 Markdown/表格/额外散文——否则 openai 引擎 L0/L1 提取
    不到 structured_output，登录成功也会被判失败。"""
    text = PROMPT.read_text()
    assert "<final_output>" in text, (
        "validate-authentication prompt 缺 <final_output> 结构化收尾指令段——"
        "agent 会以自由文本收尾，structured_output=None 假阴性复发"
    )
    final_section = text.split("<final_output>", 1)[1]
    assert "login_success" in final_section, (
        "<final_output> 段必须指示输出 login_success 字段（AUTH_VALIDATION_SCHEMA 对齐）"
    )
    assert "JSON" in final_section, (
        "<final_output> 段必须明确要求 JSON 格式"
    )


def test_prompt_does_not_suppress_json_verdict_output():
    """不得保留「Do NOT hand-write a summary JSON」类无差别抑制 JSON 的措辞——
    GLM 会把它泛化成「不要输出 JSON」，与结构化 verdict 要求直接矛盾。
    （auth-state.json 的「别手写、要跑命令」要求应明确限定在 auth-state 文件上。）"""
    text = PROMPT.read_text()
    assert "Do NOT hand-write a summary JSON" not in text, (
        "「Do NOT hand-write a summary JSON」措辞会抑制 verdict JSON 输出"
        "（2026-08-14 NodeGoat 假阴性根因之一）；限定为 auth-state 文件的措辞"
    )
