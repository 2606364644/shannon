"""RichConsoleRenderer — renders DisplayEvents to Rich live terminal output.

Uses Panel for per-agent grouping, Progress for parallel agent tracking, and
a category->style map for consistent color semantics.
"""
from __future__ import annotations

import os

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from supernova_core.display.formatters import (
    TOOL_LLM_INDENT, agent_body, agent_title, format_duration,
    format_error_block, gitnexus_body, humanize_tool_call, first_nonempty_line,
    pad_rule, phase_body, step_body, tag, wrap_body,
)

from supernova_core.agents.pricing import currency_symbol
from supernova_core.display.symbols import (
    AUDIT_COMPLETE_FAIL, AUDIT_COMPLETE_OK, SUMMARY_OK, SUMMARY_FAIL,
)


class RichConsoleRenderer:
    STYLE_MAP = {
        "PHASE": "bold cyan",
        "AGENT": "blue",
        "TOOL": "yellow",
        "LLM": "magenta",
        "ERROR": "bold red",
        "RESUME": "dim yellow",
    }

    def __init__(self, console: Console | None = None, show_phase: bool = True,
                 show_steps: bool = True, show_tools: bool = True) -> None:
        self._console = console or Console()
        self._show_phase = show_phase
        self._show_steps = show_steps
        self._show_tools = show_tools
        # spec 2026-07-14 §模块4：SUPERNOVA_LOG_VERBOSE=0 时诊断 LogEvent 不上终端
        # （仍落 diagnostic.log，DiagnosticLogRenderer 独立不受影响）。默认开（全量整洁）。
        self._verbose = os.getenv("SUPERNOVA_LOG_VERBOSE", "1") != "0"

    async def render(self, event) -> None:
        from supernova_core.display.events import (
            AgentEvent, ErrorEvent, GitnexusLlmEvent, InfoEvent, LogEvent,
            LlmTurnEvent,
            PhaseEvent, ResumeEvent, StepEvent, SummaryEvent, ToolCallEvent,
            WorkflowHeader,
        )
        match event:
            case WorkflowHeader(): self._render_header(event)
            case PhaseEvent():
                if self._show_phase:
                    self._render_phase(event)
            case StepEvent():
                if self._show_steps:
                    self._render_step(event)
            case AgentEvent(): self._render_agent(event)
            case ToolCallEvent():
                if self._show_tools:
                    self._render_tool(event)
            case LlmTurnEvent(): self._render_llm(event)
            case GitnexusLlmEvent(): self._render_gitnexus(event)
            case InfoEvent(): self._render_info(event)
            case LogEvent(): self._render_log(event)
            case ErrorEvent(): self._render_error(event)
            case SummaryEvent(): self._render_summary(event)
            case ResumeEvent(): self._render_resume(event)

    def _render_header(self, e) -> None:
        lines = []
        if getattr(e, "repo_path", None):
            lines.append(f"Repository: {e.repo_path}")
        if e.target_url:
            lines.append(f"Target:     {e.target_url}")
        mode = getattr(e, "mode", None)
        if mode and not e.target_url:
            lines.append(f"Mode:       {mode}")
        lines.append(f"Started:    {e.timestamp}")
        web_ui = getattr(e, "web_ui_url", None)
        logs_cmd = getattr(e, "logs_cmd", None)
        if web_ui or logs_cmd:
            lines.append("")
            lines.append("Monitor:")
            if web_ui:
                lines.append(f"  Web UI: {web_ui}")
            if logs_cmd:
                lines.append(f"  Logs:   {logs_cmd}")
        body = "\n".join(lines)
        self._console.print(Panel(body, title="Supernova Pentest", border_style="cyan"))

    def _render_step(self, e) -> None:
        self._console.print(
            f"[{e.timestamp}] [cyan]{tag('STEP')}[/]  {step_body(e)}", highlight=False)

    def _width(self) -> int:
        """终端宽度（MagicMock console / 异常时兜底 80，永不出错）。"""
        w = self._console.width
        return w if isinstance(w, int) else 80

    def _emit(self, first_prefix: str, body: str, cont_style: str | None = None) -> None:
        """统一换行+续行缩进打印（spec 2026-07-14 §模块3）。

        用 Text.from_markup 量 first_prefix 的真实显示宽度（含 ts 方括号/tag/logger，
        不含 markup）作 prefix_w：avail=width-1-prefix_w（-1 safety 防 Rich 边界重换行），
        续行 pad(prefix_w) 对齐到 body 起点。cont_style 给续行裹 markup（如诊断 dim）。
        短消息不换行时输出与单行 print 一致。body 须纯文本。
        """
        width = self._width()
        prefix_w = Text.from_markup(first_prefix).cell_len
        parts = wrap_body(body, max(1, width - 1), indent=prefix_w)
        self._console.print(f"{first_prefix}{parts[0]}", highlight=False)
        if len(parts) > 1:
            for cont in parts[1:]:
                if cont_style:
                    self._console.print(f"[{cont_style}]{cont}[/]", highlight=False)
                else:
                    self._console.print(cont, highlight=False)

    def _render_info(self, e) -> None:
        if e.level == "warning":
            self._emit(f"[{e.timestamp}] [yellow]{tag('WARNING')}[/]  ", e.message)
        else:
            self._emit(f"[{e.timestamp}] [cyan]{tag('INFO')}[/]  ", e.message)

    @staticmethod
    def _log_color(level: str) -> str:
        # spec §4：诊断 logging 用级别配色，不套扫描符号。
        if level in ("ERROR", "CRITICAL"):
            return "bold red"
        if level == "WARNING":
            return "yellow"
        if level == "INFO":
            return "cyan"
        return ""  # DEBUG / 未知级别：纯 dim（由 _render_log 外层叠加）

    def _render_log(self, e) -> None:
        if not self._verbose:
            return  # spec 2026-07-14 §模块4：verbose=0 时诊断 LogEvent 不上终端
        # spec 2026-07-14 §模块2：诊断 logging 行 dim 降级（背景化）+ 保留级别色调；
        # 长消息经 _emit 续行缩进到 body 起点列（Text.from_markup 量 prefix_w），不再 Rich 硬换行顶格。
        color = self._log_color(e.level)
        style = f"dim {color}".strip()  # INFO→dim cyan / WARNING→dim yellow / DEBUG→dim
        # tag 后用 2 空格分隔（与 PHASE/STEP/AGENT/InfoEvent 的 '[/]  {body}' 一致），
        # 让 dim 诊断行的 logger_name 起点列对齐到亮色 structured event 的 body 起点列
        # （22 [ts] + 5 tag + 2 分隔 = 29），不再整体左移 1 列。
        first_prefix = f"[{e.timestamp}] [{style}]{tag(e.level)}[/]  {e.logger_name}: "
        self._emit(first_prefix, e.message, cont_style=style)
        if e.exc_txt:
            prefix_w = Text.from_markup(first_prefix).cell_len
            pad = " " * prefix_w
            parts = wrap_body(e.exc_txt, max(1, self._width() - 1), indent=prefix_w)
            self._console.print(f"[{style}]{pad}{parts[0]}[/]", highlight=False)
            for ln in parts[1:]:
                self._console.print(f"[{style}]{ln}[/]", highlight=False)

    def _render_phase(self, e) -> None:
        body = pad_rule(phase_body(e))
        self._console.print(
            f"[{e.timestamp}] [bold cyan]{tag('PHASE')}[/]  {body}",
            highlight=False,
        )

    def _render_agent(self, e) -> None:
        body = agent_body(e)
        if e.event == "start":
            self._console.print(
                f"[{e.timestamp}] [blue]{tag('AGENT')}[/]  {body}", highlight=False)
            return
        if e.success is False:
            self._console.print(
                f"[{e.timestamp}] [blue]{tag('AGENT')}[/]  [red]{body}[/]", highlight=False)
            return
        self._console.print(
            f"[{e.timestamp}] [blue]{tag('AGENT')}[/]  [green]{body}[/]", highlight=False)

    def _render_tool(self, e) -> None:
        params = humanize_tool_call(e.tool_name, e.parameters if isinstance(e.parameters, dict) else {})
        who = agent_title(e.agent_name)
        self._console.print(
            f"[{e.timestamp}] [yellow]{TOOL_LLM_INDENT}🔧 {who}: {e.tool_name}({params})[/]",
            highlight=False)

    def _render_llm(self, e) -> None:
        line = first_nonempty_line(e.content) or "(无文本)"
        self._console.print(
            f"[{e.timestamp}] [magenta]{TOOL_LLM_INDENT}💭 {agent_title(e.agent_name)} "
            f"Turn {e.turn}: {line}[/]", highlight=False)

    def _render_gitnexus(self, e) -> None:
        self._console.print(
            f"[{e.timestamp}] [cyan]🔍 [GitNexus] {gitnexus_body(e)}[/]",
            highlight=False)

    def _render_error(self, e) -> None:
        # category 由 workflow_logger.log_error 按 retryable+attempt 决定:
        # WARNING=attempt 级可恢复(Temporal 会重试,对齐 TS logger.warn),
        # ERROR=用尽/non-retryable 最终失败。标签与颜色据此区分,不再硬编码 ERROR。
        is_warning = e.category == "WARNING"
        tag, color = ("WARNING", "yellow") if is_warning else ("ERROR", "bold red")
        line = f"[{e.timestamp}] [{color}]{tag}[/]  {e.error_type}: {e.message}"
        if e.context:
            line += f" (context: {e.context})"
        if e.classified:
            if e.display_retryable:
                suffix = (
                    f"将重试 {e.attempt}/{e.max_attempts}"
                    if (e.attempt and e.max_attempts) else "将重试"
                )
                line += f" [{e.classified} · {suffix}]"
            else:
                line += f" [{e.classified} · 不可重试]"
        if e.detail_path:
            line += f"  (详细堆栈见 {e.detail_path})"
        self._console.print(line, highlight=False)

    def _render_summary(self, e) -> None:
        from rich.table import Table
        ok = e.status == "completed"
        status = e.status.upper()
        prefix = f"{AUDIT_COMPLETE_OK if ok else AUDIT_COMPLETE_FAIL} "
        self._console.print(Panel.fit(
            f"{prefix}Workflow [bold]{status}[/]\n"
            f"Duration: {format_duration(e.total_duration_ms)}    "
            f"Total Cost: {currency_symbol(e.cost_currency)}{e.total_cost_usd:.4f}",
            border_style="green" if ok else "red",
        ))
        if e.agents:
            table = Table(show_header=True, header_style="bold")
            table.add_column("Status")
            table.add_column("Agent")
            table.add_column("Duration")
            table.add_column("Cost")
            table.add_column("Tokens")
            for m in e.agents:
                mark = SUMMARY_OK if m.success else SUMMARY_FAIL
                cost = f"{currency_symbol(m.cost_currency)}{m.cost_usd:.4f}" if m.cost_usd is not None else "—"
                tokens = f"{m.input_tokens or 0}/{m.output_tokens or 0}"
                table.add_row(mark, m.name, format_duration(m.duration_ms), cost, tokens)
            self._console.print(table)
        if e.error:
            self._console.print(f"[red]{format_error_block(e.error)}[/]", highlight=False)

    def _render_resume(self, e) -> None:
        self._console.print(
            f"[dim yellow][{e.timestamp}] [RESUME] Resuming workflow[/]\n"
            f"  Previous: {e.previous_workflow_id}    New: {e.new_workflow_id}",
            highlight=False,
        )
