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


def repair_truncated_json(payload: str | None) -> str | None:
    """尾部截断的 JSON 补闭合修复；救不回返 None（spec 2026-08-19 §3.1）。

    只处理「尾部不完整」一种畸形（网关流中断在 LLM 最终消息的实际形态），
    不做任意畸形修复。loads 当裁判：任何产出必须能 json.loads 通过才返回；
    完整 JSON / 空串 / 救不回 → None（调用方走 validator 防线重试）。

    算法（转义感知栈扫描，语义等同 spec 描述的 raw_decode 定位失败点）：

    1. ``json.loads`` 能过 → None（不归本函数管）。
    2. 单遍扫描记录候选点（``}``/``]`` pop 时刻）及该位置的嵌套栈快照；
       **只记录元素完整/根层边界候选**——元素完整边界（pop 后栈顶是数组
       容器，该元素是数组直接子元素且刚完整闭合）或根层 key-value 边界
       （pop 后栈深 1，根 object 的一个 value 刚完整闭合）。元素内嵌套
       容器闭合点不记录：那种 candidate 补全后会救出缺字段的部分元素，
       违反「元素内部截断连同残缺元素丢弃」。
    3. 候选 1（从晚到早）：截到候选点 + 按栈快照补闭合 → loads 验证。
       截断在元素内部（字符串中途/字段残缺）时，残缺元素连同其后内容被
       丢弃——回溯到上一个完整元素边界，救回 N-1 条。
    4. 候选 2（末尾补全）：扫描结束不在字符串字面量内时，按末尾栈整体补
       闭合（object 根尾部值完整、只缺 ``}`` 的形态）。字符串内截断不猜
       （补 ``"`` 会静默截短 notes 内容）。

    前缀垃圾容忍：输入带围栏/叙述前缀（未闭合 ```json fence 半截直连）
    时，从首个结构性 ``{``/``[`` 起参与扫描与裁判——前缀留在裁判串里会让
    所有 candidate 必然 loads 失败。真实链路调用方传入 ``_extract_json_payload``
    的 payload 子串（本就无前缀），此步对既有输入是幂等 no-op。
    """
    if not payload or not payload.strip():
        return None
    s = payload.strip()
    # 前缀垃圾容忍（见 docstring）：从首个结构性 {/[ 起。
    first_obj, first_arr = s.find("{"), s.find("[")
    starts = [p for p in (first_obj, first_arr) if p != -1]
    if starts:
        s = s[min(starts):]
    try:
        json.loads(s)
        return None
    except (json.JSONDecodeError, ValueError):
        pass

    stack: list[str] = []
    in_string = False
    escape = False
    candidates: list[tuple[int, list[str]]] = []  # (cut_pos, stack_snapshot)
    for i, ch in enumerate(s):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
            # 元素完整边界（pop 后栈顶是数组容器——该元素是数组直接子元素且已完整闭合）
            # 或根层 key-value 边界（pop 后栈深 1——根 object 的一个 value 刚完整闭合）。
            # 排除「元素内嵌套容器闭合点」：那种 candidate 补全后会救出缺字段的部分元素，
            # 违反 spec「元素内部截断连同残缺元素丢弃」。
            if stack and stack[-1] == "[" or len(stack) == 1:
                candidates.append((i + 1, list(stack)))

    for cut, st in reversed(candidates):
        closer = "".join("}" if o == "{" else "]" for o in reversed(st))
        candidate = s[:cut] + closer
        try:
            json.loads(candidate)
            return candidate
        except (json.JSONDecodeError, ValueError):
            continue

    if not in_string:
        closer = "".join("}" if o == "{" else "]" for o in reversed(stack))
        candidate = s + closer
        try:
            json.loads(candidate)
            return candidate
        except (json.JSONDecodeError, ValueError):
            return None
    return None
