import { memo, useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { useParams, useOutletContext } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { FixedSizeList } from "react-window";
import { apiGet, scanLogsPath, blackboxRunLogsPath } from "../../api/client";
import { ErrorState } from "../../components/ErrorState";
import { Empty } from "../../components/Empty";
import { Skeleton } from "../../components/ui/skeleton";
import { parseEventTs, fmtLocalFull } from "../../utils/eventTs";

// 按行数（而非字符数）判阈值：spec/log-prose 谈论的是行计数，大日志=多行。
// 5000 行覆盖典型 workflow.log / activity_failures.log（数百~数千行），同时避免
// 给中量日志误开虚拟化（旧 100k 字符阈值会因长行提前触发）。
const VIRTUAL_LINE_THRESHOLD = 5000;
const ROW_HEIGHT = 20;

// 两种 JSON 行格式共用同一渲染分支：
// - events.ndjson 风格 {ts, type, message|tool_name}；
// - agent .log（agent_logger.log_event，agents/*.log 每行）{type, timestamp, data}——
//   内容在 data 里。旧实现只读前者字段，agent 日志全部渲染成 "[] llm_response"
//   空壳（2026-08-28 「chain-verdict 日志什么记录都没有」事故）。
type LogEv = {
  ts?: string;
  timestamp?: string;
  type?: string;
  message?: string;
  tool_name?: string;
  data?: unknown;
};

// agent .log 的 data 按事件类型提取摘要；未知形态兜底 JSON（不丢记录）。
// content/result 截断上限：单行 div 靠 ellipsis 展示，防 read_file 整文件撑爆 DOM。
const DATA_SNIPPET_LIMIT = 2000;

function snippet(s: unknown): string {
  if (typeof s !== "string") return "";
  const flat = s.replace(/\s+/g, " ").trim();
  return flat.length > DATA_SNIPPET_LIMIT ? flat.slice(0, DATA_SNIPPET_LIMIT) + " …" : flat;
}

function safeJson(v: unknown): string {
  try {
    return JSON.stringify(v) ?? "";
  } catch {
    return String(v);
  }
}

function describeData(type?: string, data?: unknown): string {
  if (data == null) return "";
  if (typeof data !== "object") return snippet(data);
  const d = data as Record<string, unknown>;
  switch (type) {
    case "llm_response":
      return `t${d.turn ?? "?"} ${snippet(d.content)}`.trim();
    case "tool_start":
      return `${String(d.toolName ?? "tool")} ${snippet(safeJson(d.parameters))}`.trim();
    case "tool_end":
      return `→ ${snippet(d.result)}`;
    case "agent_start":
      return `${String(d.agentName ?? "")} attempt ${String(d.attemptNumber ?? "")}`.trim();
    case "agent_end": {
      const st = d.success === true ? "success" : d.success === false ? "failed" : "";
      return `${st} ${d.duration_ms != null ? `${d.duration_ms}ms` : ""}`.trim();
    }
    default:
      return snippet(safeJson(data));
  }
}

// JSON 行的显示字段：时间戳两种格式都认，内容 message/tool_name（events.ndjson）优先、
// agent .log 落 describeData(data)。Row 与非虚拟化分支共用，两处渲染保持一致。
function evDisplay(ev: LogEv): { ts: string; body: string } {
  return {
    ts: ev.ts ?? ev.timestamp ?? "",
    body: ev.message ?? ev.tool_name ?? describeData(ev.type, ev.data),
  };
}

// ev.ts -> browser-local full datetime. Raw ts is worker-UTC (or "t1" placeholder
// in tests); parseEventTs -> epoch -> fmtLocalFull. NaN (placeholder/non-date) falls
// back to the raw string so log rows never show "Invalid Date".
function fmtEvTs(ts?: string): string {
  if (!ts) return "";
  const ms = parseEventTs(ts);
  return Number.isNaN(ms) ? ts : fmtLocalFull(ms);
}

// Row 提到模块作用域 + memo：避免每次父组件渲染时重新定义 Row，导致 FixedSizeList
// 重新渲染所有可见行（react-window 用 children 引用相等性判断是否复用行）。
const Row = memo(function Row({ index, style, data }: {
  index: number;
  style: CSSProperties;
  data: string[];
}) {
  const l = data[index];
  let ev: LogEv | null = null;
  try { ev = JSON.parse(l); } catch { /* 非 JSON，按原文本渲染 */ }
  if (ev) {
    const { ts, body } = evDisplay(ev);
    return (
      <div style={style} className="border-l-2 border-cyan/40 bg-cyan/10 px-2 font-mono text-xs leading-5 whitespace-nowrap overflow-hidden text-ellipsis">
        [{fmtEvTs(ts)}] {ev.type} {body}
      </div>
    );
  }
  return <div style={style} className="text-sm text-muted-foreground">{l}</div>;
});

// react-window 包装：行高 20px，对齐 Task 8 LogStream 同模式（FixedSizeList + itemData）。
// 容器高度由外层 ResizeObserver 测量后传入（自适应替代旧固定 500）。
function VirtualLines({ lines, height }: { lines: string[]; height: number }) {
  return (
    <FixedSizeList height={height} width="100%" itemCount={lines.length} itemSize={ROW_HEIGHT} itemData={lines}>
      {Row}
    </FixedSizeList>
  );
}

export function LogsTab() {
  const { t } = useTranslation();
  const { workspace, scanId } = useParams<{ workspace: string; scanId: string }>();
  // 版本化黑盒 run（模式对齐 DeliverablesTab）：ScanDetail 经 Outlet context 下发
  // 选中 run；组合任务黑盒侧走 run 级端点，白盒侧维持 scan 级。无 context（非组合
  // 任务/单挂路由）→ selectedRun=null 不渲染切换器，行为与旧版一致。
  const outletCtx = useOutletContext<{ selectedRun?: string | null; combined?: boolean | null }>();
  const selectedRun = outletCtx?.selectedRun ?? null;
  const showToggle = outletCtx?.combined === true && !!selectedRun;
  const [track, setTrack] = useState<"whitebox" | "blackbox">("whitebox");
  // 黑盒侧当前生效 track（切换器隐藏时视为白盒，防 context 瞬时空导致请求打空 run）
  const effTrack = showToggle && track === "blackbox" ? "blackbox" : "whitebox";
  const [files, setFiles] = useState<string[]>([]);
  const [filesErr, setFilesErr] = useState<string | null>(null);
  const [filesLoading, setFilesLoading] = useState(true);
  const [sel, setSel] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [contentErr, setContentErr] = useState<string | null>(null);

  // 容器实测像素高度 → 喂给 FixedSizeList.height。jsdom 无 ResizeObserver，
  // typeof 守卫 + 初始值 400 兜底（fallback 保证虚拟滚动测试不崩）。
  const viewportRef = useRef<HTMLDivElement>(null);
  const [viewportH, setViewportH] = useState(400);
  useEffect(() => {
    if (typeof ResizeObserver === "undefined" || !viewportRef.current) return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setViewportH(Math.max(120, Math.floor(e.contentRect.height)));
    });
    ro.observe(viewportRef.current);
    return () => ro.disconnect();
  }, []);

  // 当前轨的日志端点（无 file=列表，带 file=内容），两个 effect 共用。
  const logsPath = (file?: string) =>
    effTrack === "blackbox" && selectedRun
      ? blackboxRunLogsPath(workspace!, scanId!, selectedRun, file)
      : scanLogsPath(workspace!, scanId!, file);

  useEffect(() => {
    if (!workspace || !scanId) return;
    setFilesLoading(true);
    setFilesErr(null);
    apiGet<{ files: string[] }>(logsPath())
      .then((r) => setFiles(r.files))
      .catch((e: unknown) => setFilesErr(e instanceof Error ? e.message : t("workspaceDetail.logs.listLoadError")))
      .finally(() => setFilesLoading(false));
  }, [workspace, scanId, effTrack, selectedRun]);

  useEffect(() => {
    if (!sel || !workspace || !scanId) return;
    setContent("");
    setContentErr(null);
    apiGet<{ content: string }>(logsPath(sel))
      .then((r) => setContent(r.content ?? ""))
      .catch((e: unknown) => setContentErr(e instanceof Error ? e.message : t("workspaceDetail.logs.contentLoadError")));
  }, [workspace, scanId, sel, effTrack, selectedRun]);

  // 换轨/换 run 时旧选中文件不在新列表（run 级文件名含 run 前缀），统一清空回选择提示。
  useEffect(() => { setSel(null); }, [effTrack, selectedRun]);

  // JSON 行渲染门：.log（agents/*.log 与 events 风格）+ .ndjson（events.ndjson/
  // authcheck-events.ndjson 主事件流，行结构与 events 风格同构）；workflow.log 与
  // activity_failures.log 是人读文本，走 pre 原样。
  const isJsonl = (sel?.endsWith(".log") || sel?.endsWith(".ndjson"))
    && !sel.endsWith("workflow.log") && !sel.endsWith("activity_failures.log");
  const lines = content.split(/\r?\n/).filter(Boolean);
  const big = lines.length > VIRTUAL_LINE_THRESHOLD;

  // 布局：grid h-full 吃 ScanDetail 的 flex-1 tab 容器（live/logs 走 flex 链），grid-rows-1 让单行撑满，
  // 两栏 h-full min-h-0 + overflow 各自内滚。不再用 max-h 固定算式（旧版窄屏 header 换行/矮视口下溢出）。
  return (
    <div className="grid h-full grid-cols-[240px_1fr] grid-rows-1 gap-4">
      <div className="h-full min-h-0 overflow-y-auto border-r border-border pr-2">
        {showToggle && (
          <div className="mb-2 flex gap-1" data-testid="log-track-toggle">
            {(["whitebox", "blackbox"] as const).map((tk) => (
              <button
                key={tk}
                type="button"
                aria-pressed={effTrack === tk}
                className={`flex-1 rounded-md px-2 py-1 text-xs font-medium hover:bg-accent ${effTrack === tk ? "bg-accent text-primary" : "text-muted-foreground"}`}
                onClick={() => setTrack(tk)}
              >
                {tk === "whitebox"
                  ? t("workspaceDetail.logs.trackWhitebox")
                  : t("workspaceDetail.logs.trackBlackbox", { runId: selectedRun })}
              </button>
            ))}
          </div>
        )}
        {filesLoading && <Skeleton className="h-4 w-full" />}
        {filesErr && <ErrorState message={filesErr} />}
        {!filesLoading && !filesErr && files.length === 0 && (
          <Empty icon="∅" title={t("workspaceDetail.logs.emptyTitle")} hint={t("workspaceDetail.logs.emptyHint")} />
        )}
        {files.map((f) => (
          <button
            key={f}
            type="button"
            aria-current={sel === f ? "true" : undefined}
            className={`block w-full text-left rounded-md px-2 py-0.5 font-mono text-xs hover:bg-accent ${sel === f ? "bg-accent text-primary" : "text-foreground"}`}
            onClick={() => setSel(f)}
          >
            {f}
          </button>
        ))}
      </div>
      <div ref={viewportRef} className="h-full min-h-0 overflow-auto">
        {!sel && <div className="text-sm text-muted-foreground">{t("workspaceDetail.logs.selectHint")}</div>}
        {sel && contentErr && <ErrorState message={contentErr} />}
        {sel && !contentErr && isJsonl && big ? (
          <>
            <div className="text-sm text-muted-foreground">{t("workspaceDetail.logs.bigFileHint", { count: lines.length })}</div>
            <VirtualLines lines={lines} height={viewportH} />
          </>
        ) : sel && !contentErr && isJsonl ? (
          lines.map((l, i) => {
            let ev: LogEv | null = null;
            try { ev = JSON.parse(l); } catch { /* 非 JSON */ }
            if (ev) {
              const { ts, body } = evDisplay(ev);
              return <div key={i} className="border-l-2 border-cyan/40 bg-cyan/10 px-2 font-mono text-xs leading-5 whitespace-nowrap overflow-hidden text-ellipsis">[{fmtEvTs(ts)}] {ev.type} {body}</div>;
            }
            return <div key={i} className="text-sm text-muted-foreground">{l}</div>;
          })
        ) : (
          sel && !contentErr && <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed">{content}</pre>
        )}
      </div>
    </div>
  );
}
