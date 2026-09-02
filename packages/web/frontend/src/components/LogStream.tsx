import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { FixedSizeList } from "react-window";
import type { NdjsonEvent, EventCategory } from "../api/types";
import { humanizeToolCall, firstNonemptyLine } from "../state/formatters";
import { fmtCost } from "../utils/currency";
import { parseEventTs, fmtClock } from "../utils/eventTs";

const CAT_CLASS: Partial<Record<EventCategory, string>> = {
  PHASE: "ev-phase", STEP: "ev-info", AGENT: "ev-agent", TOOL: "ev-tool",
  LLM: "ev-llm", ERROR: "ev-error", INFO: "ev-info", WARN: "ev-warn",
  RESUME: "ev-info", SUMMARY: "ev-phase", HEADER: "trace", GITNEXUS: "ev-info",
  CONTROL: "trace",
};

function tsClock(ts: string): string {
  // ts 经 parseEventTs -> UTC epoch -> fmtClock 渲染浏览器本地时区时分秒。
  // 旧实现只正则抠 ts 的 HH:MM:SS 原样显示 = worker 容器 UTC 墙钟，对 UTC+8 用户差 8h
  // （2026-08-06 hk-user-view live 页日志行 04:20:20 实为本地 12:20:20）。
  // 占位符 / 异常 ts（parseEventTs 返 NaN，如测试 "t1"）回退原串，不阻断渲染。
  const ms = parseEventTs(ts);
  return Number.isNaN(ms) ? ts : fmtClock(ms);
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
  if (isGnDiscoveryAgent(name)) return "[GitNexus]";
  return AGENT_PREFIX[name] ?? "[Agent]";
}

/** 对齐 CLI formatters.py agent_title：'[Prefix] name'，未知 agent 直接 name。
 *  TOOL/LLM 行用它显示 agent 身份——chain-verdict-* 等未入表的 agent 原先只有
 *  [Agent] 占位，并发交错时无法分辨归属（2026-09-02）。 */
function agentTitle(name: string): string {
  const pfx = agentPrefix(name);
  return pfx === "[Agent]" ? name : `${pfx} ${name}`;
}

/** gn-discovery-*（code_index 内 LLM 补召回子 agent，gn-discovery-sink-001 等
 *  带序号）失败=跳过该 chunk 走纯规则降级，非 activity 级失败——渲染分流用
 *  （2026-08-29 网关抖动事故：同款红色 ✗ 误导用户以为扫描出大问题）。 */
function isGnDiscoveryAgent(name: string | undefined): boolean {
  return !!name && name.startsWith("gn-discovery-");
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

/** 单行结构化描述：icon/tag 放固定列对齐，body 是主体，metrics 右对齐拆出。
 *  取代旧版 summarize() 的「图标+type+全文」挤一个 nowrap 串导致的列参差。 */
type RowDesc = { icon: string; tag: string; body: string; metrics?: string };

function describe(e: NdjsonEvent): RowDesc {
  switch (e.type) {
    case "PhaseEvent":
      return { icon: "◆", tag: "PHASE", body: `${e.event === "start" ? "Starting" : "Complete"} ${e.phase}` };

    case "StepEvent": {
      const label = e.intent || e.name;
      if (e.event === "start") return { icon: "○", tag: "STEP", body: label };
      if (e.error) return { icon: "✗", tag: "STEP", body: `${label} — ${e.error}` };
      return { icon: "✓", tag: "STEP", body: label, metrics: e.duration_ms != null ? fmtDuration(e.duration_ms) : undefined };
    }

    case "AgentEvent": {
      const pfx = agentPrefix(e.agent_name);
      if (e.event === "start") {
        return { icon: "▶", tag: "AGENT", body: `${pfx} ${e.agent_name} started (attempt ${e.attempt})` };
      }
      if (e.success === false) {
        const err = e.error ? ` — ${e.error}` : "";
        // discovery 补召回降级（非致命）：⚠ + (recall skipped)，区别于 activity 级红 ✗。
        if (isGnDiscoveryAgent(e.agent_name)) {
          return { icon: "⚠", tag: "AGENT", body: `${pfx} ${e.agent_name} failed${err} (recall skipped)`, metrics: e.duration_ms != null ? fmtDuration(e.duration_ms) : undefined };
        }
        return { icon: "✗", tag: "AGENT", body: `${pfx} ${e.agent_name} failed${err}`, metrics: e.duration_ms != null ? fmtDuration(e.duration_ms) : undefined };
      }
      const parts: string[] = [];
      if (e.duration_ms != null) parts.push(fmtDuration(e.duration_ms));
      if (e.cost_usd != null) parts.push(fmtCost(e.cost_usd, e.cost_currency));
      const toks = fmtTokens(e.input_tokens, e.output_tokens);
      if (toks) parts.push(toks);
      return { icon: "✓", tag: "AGENT", body: `${pfx} ${e.agent_name} Completed`, metrics: parts.join(" · ") || undefined };
    }

    case "ToolCallEvent": {
      const who = agentTitle(e.agent_name);
      const params = humanizeToolCall(e.tool_name, e.parameters ?? {});
      return { icon: "↳", tag: "TOOL", body: `${who} ${e.tool_name}${params ? `: ${params}` : ""}` };
    }

    case "LlmTurnEvent": {
      const who = agentTitle(e.agent_name);
      const line = firstNonemptyLine(e.content);
      return { icon: "›", tag: "LLM", body: `${who} Turn ${e.turn}${line ? `: ${line}` : ""}` };
    }

    case "GitnexusLlmEvent": {
      const e2 = e as unknown as Record<string, unknown>;
      const phase = String(e2.phase ?? "?");
      const kind = String(e2.kind ?? "progress");
      if (kind === "hit") return { icon: "◎", tag: "GITNX", body: `${phase} ✓ ${e2.detail ?? ""}` };
      if (kind === "summary") return { icon: "◎", tag: "GITNX", body: `${phase} done ${e2.done}/${e2.total} → ${e2.detail ?? ""}` };
      if (kind === "note") return { icon: "◎", tag: "GITNX", body: `${phase} ⚠ ${e2.detail ?? ""}` };
      return { icon: "◎", tag: "GITNX", body: `${phase} ${e2.done}/${e2.total} · ${e2.hits} hits` };
    }

    case "WorkflowHeader": {
      const e2 = e as unknown as Record<string, unknown>;
      const parts: string[] = [];
      if (e2.repo_path) parts.push(`repo: ${e2.repo_path}`);
      if (e2.target_url) parts.push(`target: ${e2.target_url}`);
      if (e2.mode) parts.push(`mode: ${e2.mode}`);
      return { icon: "#", tag: "META", body: parts.join("  ") };
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
      return { icon: "✗", tag: "ERROR", body: msg };
    }

    case "SummaryEvent": {
      const parts: string[] = [];
      if (e.total_duration_ms != null) parts.push(fmtDuration(e.total_duration_ms));
      if (e.total_cost_usd != null) parts.push(fmtCost(e.total_cost_usd, e.cost_currency));
      if (e.agents?.length) parts.push(`${e.agents.length} agents`);
      return { icon: "■", tag: "DONE", body: e.status, metrics: parts.join(" · ") || undefined };
    }

    case "LogEvent": {
      const line = `[${e.level}] ${e.logger_name}: ${e.message}`;
      return { icon: "·", tag: "LOG", body: e.exc_txt ? `${line}\n${e.exc_txt}` : line };
    }

    case "ResumeEvent":
      return { icon: "↺", tag: "RESUME", body: `resume ← ${e.previous_workflow_id}` };

    case "run_end":
      return { icon: "◆", tag: "RUN", body: `${e.run} · ${e.status}` };

    case "correlation_progress": {
      // 跨仓关联三段接力进度（web CorrelationEventWriter，CONTROL）：node=repo/phase/edge。
      const icon = e.status === "failed" ? "✗" : e.status === "completed" ? "✓" : "○";
      return { icon, tag: "CORR", body: `${e.node} ${e.name} ${e.status}${e.detail ? ` — ${e.detail}` : ""}` };
    }

    case "InfoEvent":
      return { icon: "·", tag: "INFO", body: e.message };

    default:
      return { icon: "·", tag: e.type, body: e.type };
  }
}

/** 行 CSS class：base category class + Agent end 成功/失败追加色。 */
function rowClass(e: NdjsonEvent): string {
  if (e.type === "LogEvent") {
    if (e.level === "ERROR") return "ev-error";
    if (e.level === "WARNING") return "ev-warn";
    return "text-muted-foreground";  // INFO/DEBUG/NOTSET 灰显
  }
  const base = CAT_CLASS[e.category] ?? "text-muted-foreground";
  if (e.type === "AgentEvent" && e.event === "end") {
    if (e.success === false) {
      // discovery 补召回降级走 warn 色（非致命，区别于 activity 级失败红）。
      return isGnDiscoveryAgent(e.agent_name) ? `${base} ev-warn` : `${base} ev-agent-fail`;
    }
    return `${base} ev-agent-ok`;
  }
  return base;
}

const ROW_HEIGHT = 20;
const VIRTUAL_THRESHOLD = 500;

/** TOOL/LLM 行缩进一级（对齐 CLI TOOL_LLM_INDENT）：AGENT start/end 行顶格作锚点，
 *  其间的 TOOL/LLM 行 body 缩进表达"agent 执行期内"，归属靠行内 agentTitle 全名。 */
function isAgentScoped(e: NdjsonEvent): boolean {
  return e.type === "ToolCallEvent" || e.type === "LlmTurnEvent";
}

/** 单事件行：固定列网格（色边|时间|图标|标签|主体|metrics）。
 *  - ev-* 色留在行容器（测试 ROW_SELECTOR 不变量）；ts/tag/metrics 降级 muted + normal。
 *  - data-type 保留 type 身份（hover tooltip + 测试 hook），替代旧版裸 type 名文本。 */
function LogRow({ e, style }: { e: NdjsonEvent; style?: CSSProperties }) {
  const { icon, tag, body, metrics } = describe(e);
  // hover title 带完整 ts：窄列只显 HH:MM:SS，悬停看完整 "2026-07-31 10:53:53"。
  const title = [e.ts, body, metrics].filter(Boolean).join("  ");
  const bodyCls = `log-body${isAgentScoped(e) ? " log-body--indent" : ""}`;
  return (
    <div style={style} className={`log-row ${rowClass(e)}`} data-type={e.type} title={title}>
      <span className="log-gutter" aria-hidden />
      <span className="log-ts">{tsClock(e.ts)}</span>
      <span className="log-icon" aria-hidden>{icon}</span>
      <span className="log-tag">{tag}</span>
      <span className={bodyCls}>{body}</span>
      <span className="log-metrics">{metrics ?? ""}</span>
    </div>
  );
}

function VirtualRow({ index, style, data }: { index: number; style: CSSProperties; data: NdjsonEvent[] }) {
  return <LogRow e={data[index]} style={style} />;
}

export function LogStream({ events, fill }: { events: NdjsonEvent[]; fill?: boolean }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<FixedSizeList>(null);
  const virtual = events.length > VIRTUAL_THRESHOLD;

  // react-window FixedSizeList 需要像素高度。测容器内容区高（clientHeight 减 p-2 上下 padding 共 16px），
  // 容器随视口弹性变化时（fill 模式）实时跟随。jsdom 无 ResizeObserver → guard 跳过、回退初值
  // （测试只验渲染与不崩，不验像素布局，故回退值不影响断言）。
  const [listH, setListH] = useState(300);
  useEffect(() => {
    const el = containerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const measure = () => setListH(Math.max(1, Math.floor(el.clientHeight) - 16));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // 自动滚底：新事件到达时滚到最底行
  useEffect(() => {
    if (virtual) {
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
  }, [events, virtual]);

  // fill：撑满 flex 父级剩余空间（实时页控制台布局，min-h-0 允许被上方指标卡压缩并自身滚动，
  // 让整页只剩这一条滚动条）。非 fill（默认，DevComponentsPage）：独立固定面板，min-h/max-h 自带滚动。
  const sizeCls = fill ? "flex-1 min-h-0" : "min-h-[160px] max-h-[480px]";
  return (
    <div
      ref={containerRef}
      className={`rounded-md border border-border bg-background p-2 font-mono text-xs ${sizeCls} ${
        virtual ? "overflow-hidden" : "overflow-y-auto"
      }`}
      aria-live="polite"
    >
      {virtual ? (
        <FixedSizeList
          ref={listRef}
          height={listH}
          width="100%"
          itemCount={events.length}
          itemSize={ROW_HEIGHT}
          itemData={events}
        >
          {VirtualRow}
        </FixedSizeList>
      ) : (
        events.map((e, i) => (
          <LogRow key={i} e={e} />
        ))
      )}
    </div>
  );
}
