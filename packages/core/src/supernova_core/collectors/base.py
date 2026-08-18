"""声明式 collector 框架（host 渲染产物架构，对齐 TS collectors/）。

agent 调一组 set_* 结构化工具 → CollectorBase 收集 payload → renderer 确定性渲染 md。

两种 mode（SectionSchema.mode）：
- ``"set"``（默认）：write-once，重复调抛 DuplicateCallError（首次生效），对齐 TS
  pre-recon-collector.ts:445-451。skipped section 不在 get_all() 里，由 renderer 补
  placeholder（不 fail activity）。
- ``"append"``：累积，多次调 append_section 把 item 累积进 list（不抛 DuplicateError）。
  对齐 TS recon-collector.ts 的 add_endpoints（本仓命名 set_endpoints，语义是 append）。
  空时不在 get_all() 里（renderer 补 placeholder，与 set_* skipped 同语义）。
"""
from __future__ import annotations

from dataclasses import dataclass


class DuplicateCallError(Exception):
    """set_* 工具被调用超过一次（write-once）。对齐 TS DuplicateError：首次生效，重复 no-op。"""


@dataclass(frozen=True)
class SectionSchema:
    """一个 deliverable section 的声明式定义。

    Attributes:
        tool_name: 模型见到的工具名（如 "set_executive_summary"）。
        section_key: payload bag 里的键（如 "executive_summary"）。
        description: 工具描述，喂给模型当 tool description。
        json_schema: 完整 JSON Schema dict（type=object, properties...）。
            openai 侧作 FunctionTool.params_json_schema，claude 侧作 SdkMcpTool.input_schema（原样透传）。
        mode: ``"set"``（默认，write-once，重复调抛 DuplicateCallError）或
            ``"append"``（累积，多次调 append_section 把 item 累积进 list）。
            append 模式对齐 TS recon-collector.ts 的 add_endpoints（本仓叫 set_endpoints）。
    """

    tool_name: str
    section_key: str
    description: str
    json_schema: dict
    mode: str = "set"


class CollectorBase:
    """per-agent-run 的 payload 收集器（非全局，对齐 TS per-agent collector 实例）。

    支持两种 section：
    - mode="set"（默认）：write-once，``set_section`` 写入，重复抛 DuplicateCallError。
    - mode="append"：累积，``append_section`` 把 item dict 累积进 list，可多次调。
    """

    def __init__(self, section_schemas: list[SectionSchema]):
        self._schemas: list[SectionSchema] = list(section_schemas)
        self._by_key: dict[str, SectionSchema] = {s.section_key: s for s in self._schemas}
        self._tool_to_key: dict[str, str] = {s.tool_name: s.section_key for s in self._schemas}
        self._payloads: dict[str, dict] = {}        # section_key -> payload；absent = skipped (mode="set")
        self._appends: dict[str, list[dict]] = {}   # section_key -> 累积 item list；absent = 未 append (mode="append")
        self._called_tools: list[str] = []          # tool_name 调用顺序（诊断用）

    @property
    def section_schemas(self) -> list[SectionSchema]:
        return list(self._schemas)

    def tool_names(self) -> list[str]:
        return [s.tool_name for s in self._schemas]

    def _resolve_key(self, tool_name_or_key: str) -> str:
        """tool_name 或 section_key → section_key；未解析抛 KeyError。"""
        key = self._tool_to_key.get(tool_name_or_key)
        if key is None and tool_name_or_key in self._by_key:   # 也容忍直接传 section_key
            key = tool_name_or_key
        if key is None or key not in self._by_key:
            raise KeyError(f"unknown section tool/key: {tool_name_or_key!r}")
        return key

    def set_section(self, tool_name: str, payload: dict) -> None:
        """write-once 写入一个 section 的 payload。重复调抛 DuplicateCallError（首次生效）。

        仅用于 mode="set" 的 section；对 mode="append" 的 section 调用抛 TypeError（误用保护）。
        """
        key = self._resolve_key(tool_name)
        schema = self._by_key[key]
        if schema.mode != "set":
            raise TypeError(
                f"{tool_name} is mode={schema.mode!r}; use append_section() for append-mode sections"
            )
        if key in self._payloads:
            raise DuplicateCallError(
                f"{tool_name} has already been called. Each set_* tool may only be called once per run."
            )
        # 双保险：非 dict payload（如合法 JSON 数组）明确 ValueError，而非 dict() 的
        # TypeError（bridge 层已拦，此处兜漏网调用方，语义清晰可捕获）。
        if payload is not None and not isinstance(payload, dict):
            raise ValueError(
                f"{tool_name} payload must be a dict, got {type(payload).__name__}"
            )
        self._payloads[key] = dict(payload or {})
        self._called_tools.append(tool_name)

    def append_section(self, tool_name: str, item: dict) -> None:
        """累积写入一个 append section 的 item。多次调累积，不抛 DuplicateCallError。

        仅用于 mode="append" 的 section；对 mode="set" 的 section 调用抛 TypeError（误用保护）。
        """
        key = self._resolve_key(tool_name)
        schema = self._by_key[key]
        if schema.mode != "append":
            raise TypeError(
                f"{tool_name} is mode={schema.mode!r}; use set_section() for set-mode sections"
            )
        # 双保险：非 dict item（如合法 JSON 数组）明确 ValueError，而非 dict() 的 TypeError。
        if item is not None and not isinstance(item, dict):
            raise ValueError(
                f"{tool_name} item must be a dict, got {type(item).__name__}"
            )
        self._appends.setdefault(key, []).append(dict(item or {}))
        if tool_name not in self._called_tools:
            self._called_tools.append(tool_name)

    def get_all(self) -> dict:
        """返回 payload bag（深拷贝；skipped section 不含键，renderer 补 placeholder）。

        - mode="set" section：首次 set_section 后出现（dict）。
        - mode="append" section：append 过后出现（list[dict]）；未 append 过不含键。
        """
        result: dict = {key: dict(val) for key, val in self._payloads.items()}
        for key, items in self._appends.items():
            if items:  # 空 list 不含键（与 set_* skipped 同语义）
                result[key] = [dict(it) for it in items]
        return result

    def get_call_status(self) -> dict[str, str]:
        """每个 tool_name -> 'called' | 'skipped'（诊断/日志，对齐 TS getCallStatus）。

        mode="append" 工具 "called" 不意味着只调一次（可多次 append）。
        """
        status: dict[str, str] = {}
        for s in self._schemas:
            if s.mode == "append":
                status[s.tool_name] = "called" if s.section_key in self._appends and self._appends[s.section_key] else "skipped"
            else:
                status[s.tool_name] = "called" if s.section_key in self._payloads else "skipped"
        return status
