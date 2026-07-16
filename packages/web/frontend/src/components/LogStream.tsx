import { useEffect, useRef } from "react";
import { FixedSizeList } from "react-window";
import type { NdjsonEvent, EventCategory } from "../api/types";
import { humanizeToolCall, firstNonemptyLine } from "../state/formatters";
import { fmtCost } from "../utils/currency";

const CAT_CLASS: Partial<Record<EventCategory, string>> = {
  PHASE: "ev-phase", STEP: "ev-info", AGENT: "ev-agent", TOOL: "ev-tool",
  LLM: "ev-llm", ERROR: "ev-error", INFO: "ev-info", WARN: "ev-warn",
  RESUME: "ev-info", SUMMARY: "ev-phase", HEADER: "trace", GITNEXUS: "ev-info",
  CONTROL: "trace",
};

function tsClock(ts: string): string {
  const m = /T(\d{2}:\d{2}:\d{2})/.exec(ts);
  return m ? m[1] : ts;
}

// ── helpers ──

/** 对齐 CLI formatters.py:51-62 _AGENT_PREFIXES */
const AGENT_PREFIX: Record<string, string> = {
  "injection-vuln": "[Injection]", "injection-exploit": "[Injection]",
  "xss-vuln": "[XSS]", "xss-exploit": "[XSS]",
  "authz-vuln": "[Authz]", "authz-exploit": "[Authz]",
  "auth-vuln": "[Auth]", "auth-exploit": "[Auth]",
  "ssrf-vuln": "[SSRF]", "ssrf-exploit": "[SSRF]",
};

function agentPrefix(name: string): string {
  return AGENT_PREFIX[name] ?? "[Agent]";
}

/** 对齐 CLI formatters.py:21-29 format_duration */
function fmtDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  return `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s`;
}

function fmtTokens(input?: number, output?: number): string {
  if (input == null && output == null) return "";
  const i = input != null ? `${input}` : "?";
  const o = output != null ? `${output}` : "?";
  return `${i}/${o} tok`;
}

function summarize(e: NdjsonEvent): string {
  switch (e.type) {
    case "PhaseEvent":
      return `${e.event === "start" ? "Starting" : "Complete"} ${e.phase}`;

    case "StepEvent": {
      const label = e.intent || e.name;
      if (e.event === "start") return `○ ${label}`;
      if (e.error) return `✗ ${label}  — ${e.error}`;
      const dur = e.duration_ms != null ? `  ${fmtDuration(e.duration_ms)}` : "";
      return `✓ ${label}${dur}`;
    }

    case "AgentEvent": {
      const pfx = agentPrefix(e.agent_name);
      if (e.event === "start") {
        return `▶ ${pfx} ${e.agent_name} started (attempt ${e.attempt})`;
      }
      if (e.success === false) {
        const dur = e.duration_ms != null ? fmtDuration(e.duration_ms) : "?";
        const err = e.error ? ` — ${e.error}` : "";
        return `✗ ${pfx} ${e.agent_name} failed (${dur})${err}`;
      }
      const parts: string[] = [];
      if (e.duration_ms != null) parts.push(fmtDuration(e.duration_ms));
      if (e.cost_usd != null) parts.push(fmtCost(e.cost_usd, e.cost_currency));
      const toks = fmtTokens(e.input_tokens, e.output_tokens);
      if (toks) parts.push(toks);
      const metrics = parts.length ? ` (${parts.join(", ")})` : "";
      return `✓ ${pfx} ${e.agent_name} Completed${metrics}`;
    }

    case "ToolCallEvent": {
      const pfx = agentPrefix(e.agent_name);
      const params = humanizeToolCall(e.tool_name, e.parameters ?? {});
      return `🔧 ${pfx} ${e.tool_name}${params ? `: ${params}` : ""}`;
    }

    case "LlmTurnEvent": {
      const pfx = agentPrefix(e.agent_name);
      const line = firstNonemptyLine(e.content);
      return `💭 ${pfx} Turn ${e.turn}${line ? `: ${line}` : ""}`;
    }

    case "GitnexusLlmEvent": {
      const e2 = e as unknown as Record<string, unknown>;
      const phase = String(e2.phase ?? "?");
      const kind = String(e2.kind ?? "progress");
      if (kind === "hit") return `🔍 [GitNexus] ${phase}  ✓ ${e2.detail ?? ""}`;
      if (kind === "summary") return `🔍 [GitNexus] ${phase}  done ${e2.done}/${e2.total} → ${e2.detail ?? ""}`;
      if (kind === "note") return `🔍 [GitNexus] ${phase}  ⚠ ${e2.detail ?? ""}`;
      return `🔍 [GitNexus] ${phase}  ${e2.done}/${e2.total}  · ${e2.hits} hits`;
    }

    case "WorkflowHeader": {
      const e2 = e as unknown as Record<string, unknown>;
      const parts: string[] = [];
      if (e2.repo_path) parts.push(`repo: ${e2.repo_path}`);
      if (e2.target_url) parts.push(`target: ${e2.target_url}`);
      if (e2.mode) parts.push(`mode: ${e2.mode}`);
      return parts.join("  ");
    }

    case "ErrorEvent": {
      let msg = `${e.error_type}: ${e.message}`;
      if (e.context) msg += ` (context: ${e.context})`;
      if (e.classified) {
        if (e.display_retryable && e.attempt && e.max_attempts) {
          msg += ` [${e.classified} · retry ${e.attempt}/${e.max_attempts}]`;
        } else {
          msg += ` [${e.classified}]`;
        }
      }
      return msg;
    }

    case "SummaryEvent": {
      const parts: string[] = [e.status];
      if (e.total_duration_ms != null) parts.push(fmtDuration(e.total_duration_ms));
      if (e.total_cost_usd != null) parts.push(fmtCost(e.total_cost_usd, e.cost_currency));
      if (e.agents?.length) parts.push(`${e.agents.length} agents`);
      return parts.join("  ");
    }

    case "LogEvent": {
      const line = `[${e.level}] ${e.logger_name}: ${e.message}`;
      return e.exc_txt ? `${line}\n${e.exc_txt}` : line;
    }
    case "ResumeEvent":
      return `resume ← ${e.previous_workflow_id}`;
    case "InfoEvent":
      return e.message;
    default:
      return e.type;
  }
}

/** 单行 CSS class：base category class + Agent end 成功/失败追加色。 */
function rowClass(e: NdjsonEvent): string {
  if (e.type === "LogEvent") {
    if (e.level === "ERROR") return "ev-error";
    if (e.level === "WARNING") return "ev-warn";
    return "text-muted-foreground";  // INFO/DEBUG/NOTSET 灰显
  }
  const base = CAT_CLASS[e.category] ?? "text-muted-foreground";
  if (e.type === "AgentEvent" && e.event === "end") {
    if (e.success === false) return `${base} ev-agent-fail`;
    return `${base} ev-agent-ok`;
  }
  return base;
}

const ROW_HEIGHT = 20;
const VIRTUAL_THRESHOLD = 500;

function Row({ index, style, data }: { index: number; style: React.CSSProperties; data: NdjsonEvent[] }) {
  const e = data[index];
  return (
    <div style={style} className={`whitespace-nowrap overflow-hidden text-ellipsis ${rowClass(e)}`}>
      <span className="text-muted-foreground">[{tsClock(e.ts)}]</span>{" "}
      <span className="text-muted-foreground">{e.type}</span>{" "}
      {summarize(e)}
    </div>
  );
}

export function LogStream({ events }: { events: NdjsonEvent[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<FixedSizeList>(null);

  // 自动滚底：新事件到达时滚到最底行
  useEffect(() => {
    if (events.length > VIRTUAL_THRESHOLD) {
      listRef.current?.scrollToItem(events.length - 1, "end");
    } else if (containerRef.current) {
      // requestAnimationFrame 确保 DOM 已更新再滚底（浏览器真实环境生效；
      // jsdom 中 rAF 异步可能导致 scrollTop 在测试中不立即反映，但组件不崩）。
      requestAnimationFrame(() => {
        if (containerRef.current) {
          containerRef.current.scrollTop = containerRef.current.scrollHeight;
        }
      });
    }
  }, [events]);

  if (events.length > VIRTUAL_THRESHOLD) {
    return (
      <div className="h-[400px] overflow-y-auto rounded-md border border-border bg-background p-2 font-mono text-xs" aria-live="polite">
        <FixedSizeList
          ref={listRef}
          height={400}
          width="100%"
          itemCount={events.length}
          itemSize={ROW_HEIGHT}
          itemData={events}
        >
          {Row}
        </FixedSizeList>
      </div>
    );
  }
  return (
    <div ref={containerRef} className="max-h-[480px] space-y-0 overflow-y-auto rounded-md border border-border bg-background p-2 font-mono text-xs" aria-live="polite">
      {events.map((e, i) => (
        <div key={i} style={{ lineHeight: "20px" }} className={`whitespace-nowrap overflow-hidden text-ellipsis ${rowClass(e)}`}>
          <span className="text-muted-foreground">[{tsClock(e.ts)}]</span>{" "}
          <span className="text-muted-foreground">{e.type}</span>{" "}
          {summarize(e)}
        </div>
      ))}
    </div>
  );
}
