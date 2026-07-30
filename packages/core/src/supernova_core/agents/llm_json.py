"""LLM 输出 JSON 提取工具（无 SDK 依赖）。

从 LLM 返回的文本里抠出合法 JSON 字符串。模拟 Claude SDK「把 LLM 文本变成合法
JSON」的契约（TS 侧 SDK 免费；两个引擎的 Python provider 自己补——anthropic 经
Claude Code CLI 子进程、openai-agents 无内置工具层）。本模块纯字符串/正则工具，
不 import openai-agents / claude-agent-sdk，故 code_index 等核心层可安全引用。

历史：原 `_extract_json_payload` 与 openai-agents 适配器同住在 openai_output_schema.py
（`from agents import AgentOutputSchemaBase` 拖 SDK）。提出独立模块后，code_index 的
轻量 LLM parse 点（taint / sink·source·storage discovery / chain_verdict）得以复用，
不必拖 SDK 进 import 链（code_index 已有 `import agents.model_caps` 先例）。
"""
from __future__ import annotations

import json
import re

# 预编译：匹配所有 ``` 代码围栏块（可选语言标签），取围栏内容（DOTALL 跨行）。
# 用于从「Markdown 分析 + ```java 代码示例 + 末尾 ```json 结果」里精准定位 JSON 块。
_FENCE_RE = re.compile(r"```[a-zA-Z]*\s*\n?(.*?)```", re.DOTALL)


def _extract_json_payload(text: str) -> str | None:
    """从 LLM 输出文本提取 JSON 字符串（双引擎 provider + code_index parse 复用）。

    处理 GLM 常见收尾形态（按优先级）：

      1. 纯 JSON 直通（最快路径，含 JSON array 根 ``[...]``）。
      2. 所有 ``` 代码围栏块，**从后往前**取首个能 ``json.loads`` 的。
         GLM 常在 Markdown 分析 + ``\\`\\`\\`java`` 代码示例（含 ``{}``）之后用
         ``\\`\\`\\`json`` 包裹结果；从后往前取，避免前面代码块的 ``{}`` 污染
         「首 ``{`` 末 ``}``」提取（旧实现常在此被截断成畸形子串）。
      3. 回退：首个 ``{``/``[`` 到末个 ``}``/``]`` 的子串，优先返回能
         ``json.loads`` 成功的（object 或 array 根）；都不合法时保留旧的「首 ``{``
         末 ``}``」object 子串语义（向后兼容）。这一步同时认 ``[``/``]`` 起止，
         修复旧实现只 ``find('{')``/``rfind('}')`` 导致 **array 根被截断成单个
         object** 的隐藏 bug。

    全无 ``{``/``[`` → 返回 ``None``（调用方据此走 fallback / 抛
    StructuredOutputParseError）。

    向后兼容：现有 ``test_openai_output_schema.py`` 的 8 个用例（纯 JSON / ``\\`\\`\\`json``
    fence / ``\\`\\`\\` `` fence / 前导叙述 / 空 / 无 ``{}``）全部等价通过。
    """
    if not text or not text.strip():
        return None
    s = text.strip()

    # 1. 纯 JSON 直通（含 array 根）。
    try:
        json.loads(s)
        return s
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. 所有 ```fence 块，从后往前取首个合法 JSON（object 或 array 根均可）。
    for body in reversed(_FENCE_RE.findall(s)):
        body = body.strip()
        try:
            json.loads(body)
            return body
        except (json.JSONDecodeError, ValueError):
            continue

    # 3. 回退：首个 {/ 到末个 }/] 的子串（旧「首{末}」逻辑 + array 根支持）。
    #    优先返回能 json.loads 成功的候选；都不合法时保留旧的「首 { 末 }」语义
    #    （向后兼容：旧实现只认 object 子串，array 根靠上面第 1/2 步覆盖）。
    start_obj, end_obj = s.find("{"), s.rfind("}")
    start_arr, end_arr = s.find("["), s.rfind("]")
    obj_sub = s[start_obj : end_obj + 1] if start_obj != -1 and end_obj > start_obj else None
    arr_sub = s[start_arr : end_arr + 1] if start_arr != -1 and end_arr > start_arr else None
    for sub in (obj_sub, arr_sub):
        if sub is None:
            continue
        try:
            json.loads(sub)
            return sub
        except (json.JSONDecodeError, ValueError):
            continue
    # 都不合法：回退 object 子串（旧行为，调用方 loads 失败再各自 fallback）。
    return obj_sub


def repair_json_arguments(args: str | None) -> str | None:
    """把可能非法的 tool_call ``arguments`` 串修成**合法 JSON 串**；修不好返 ``None``。

    第三方 openai 兼容端点（火山方舟 ARK 等）消费侧校验「tool_call.function.arguments
    必须是合法 JSON」，而 GLM 等模型偶发吐残缺/markdown 围栏的 arguments；openai-agents
    的 Chat Completions 模式无状态全量重发，会把这条非法 assistant message 原样塞回 history
    再次发送 → 端点 400 ``Invalid request body``。

    本函数复用 ``_extract_json_payload``（处理 markdown 围栏 / 首末括号子串），但
    ``_extract_json_payload`` 在「子串仍非法」时为向后兼容会返回 ``obj_sub``（非 None），
    故这里对返回值**再 ``json.loads`` 验证一次**——只有能真正 parse 的才算修好，否则 None。

    两道防线共用（DRY）：
    - 防线1 ``bridge._on_invoke_set``：修不好 → 返错误串让模型重发、不收空数据；
    - 防线2 ``providers_openai`` 发包前清洗：修不好 → 兜底 ``"{}"`` 止血 400。
    """
    if not isinstance(args, str) or not args.strip():
        return None
    candidate = _extract_json_payload(args)
    if candidate is None:
        return None
    try:
        json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    return candidate
