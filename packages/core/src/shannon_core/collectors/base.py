"""声明式 collector 框架（host 渲染产物架构，对齐 TS collectors/）。

agent 调一组 set_* 结构化工具 → CollectorBase 收集 payload → renderer 确定性渲染 md。
write-once（重复调 DuplicateError，首次生效，对齐 TS pre-recon-collector.ts:445-451）。
skipped section 不在 get_all() 里，由 renderer 补 placeholder（不 fail activity）。
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
    """

    tool_name: str
    section_key: str
    description: str
    json_schema: dict


class CollectorBase:
    """per-agent-run 的 payload 收集器（非全局，对齐 TS per-agent collector 实例）。"""

    def __init__(self, section_schemas: list[SectionSchema]):
        self._schemas: list[SectionSchema] = list(section_schemas)
        self._by_key: dict[str, SectionSchema] = {s.section_key: s for s in self._schemas}
        self._tool_to_key: dict[str, str] = {s.tool_name: s.section_key for s in self._schemas}
        self._payloads: dict[str, dict] = {}        # section_key -> payload；absent = skipped
        self._called_tools: list[str] = []          # tool_name 调用顺序（诊断用）

    @property
    def section_schemas(self) -> list[SectionSchema]:
        return list(self._schemas)

    def tool_names(self) -> list[str]:
        return [s.tool_name for s in self._schemas]

    def set_section(self, tool_name: str, payload: dict) -> None:
        """write-once 写入一个 section 的 payload。重复调抛 DuplicateCallError（首次生效）。"""
        key = self._tool_to_key.get(tool_name)
        if key is None and tool_name in self._by_key:   # 也容忍直接传 section_key
            key = tool_name
        if key is None or key not in self._by_key:
            raise KeyError(f"unknown section tool/key: {tool_name!r}")
        if key in self._payloads:
            raise DuplicateCallError(
                f"{tool_name} has already been called. Each set_* tool may only be called once per run."
            )
        self._payloads[key] = dict(payload or {})
        self._called_tools.append(tool_name)

    def get_all(self) -> dict:
        """返回 payload bag（深拷贝；skipped section 不含键，renderer 补 placeholder）。"""
        return {key: dict(val) for key, val in self._payloads.items()}

    def get_call_status(self) -> dict[str, str]:
        """每个 tool_name -> 'called' | 'skipped'（诊断/日志，对齐 TS getCallStatus）。"""
        return {
            s.tool_name: ("called" if s.section_key in self._payloads else "skipped")
            for s in self._schemas
        }
