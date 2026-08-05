import { memo, useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { FixedSizeList } from "react-window";
import { apiGet, scanLogsPath } from "../../api/client";
import { ErrorState } from "../../components/ErrorState";
import { Empty } from "../../components/Empty";
import { Skeleton } from "../../components/ui/skeleton";

// 按行数（而非字符数）判阈值：spec/log-prose 谈论的是行计数，大日志=多行。
// 5000 行覆盖典型 workflow.log / activity_failures.log（数百~数千行），同时避免
// 给中量日志误开虚拟化（旧 100k 字符阈值会因长行提前触发）。
const VIRTUAL_LINE_THRESHOLD = 5000;
const ROW_HEIGHT = 20;

type LogEv = { ts?: string; type?: string; message?: string; tool_name?: string };

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
    return (
      <div style={style} className="border-l-2 border-cyan/40 bg-cyan/10 px-2 font-mono text-xs leading-5 whitespace-nowrap overflow-hidden text-ellipsis">
        [{ev.ts}] {ev.type} {ev.message ?? ev.tool_name ?? ""}
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

  useEffect(() => {
    if (!workspace || !scanId) return;
    setFilesLoading(true);
    setFilesErr(null);
    apiGet<{ files: string[] }>(scanLogsPath(workspace, scanId))
      .then((r) => setFiles(r.files))
      .catch((e: unknown) => setFilesErr(e instanceof Error ? e.message : t("workspaceDetail.logs.listLoadError")))
      .finally(() => setFilesLoading(false));
  }, [workspace, scanId]);

  useEffect(() => {
    if (!sel || !workspace || !scanId) return;
    setContent("");
    setContentErr(null);
    apiGet<{ content: string }>(scanLogsPath(workspace, scanId, sel))
      .then((r) => setContent(r.content ?? ""))
      .catch((e: unknown) => setContentErr(e instanceof Error ? e.message : t("workspaceDetail.logs.contentLoadError")));
  }, [workspace, scanId, sel]);

  const isJsonl = sel?.endsWith(".log") && !sel.endsWith("workflow.log") && !sel.endsWith("activity_failures.log");
  const lines = content.split(/\r?\n/).filter(Boolean);
  const big = lines.length > VIRTUAL_LINE_THRESHOLD;

  // 自适应布局：grid 不强制满高，两栏各自 max-h（100dvh-14rem，与 LiveTab 同口径=TopBar+main py+scan header+tabs）
  // + min-h-0 允许内滚。没选日志时右栏仅提示行（不触发 max-h）-> 容器塌缩 -> 整页不溢出、无外层滚动条；
  // 选了日志后右栏顶 max-h 独立内滚。max-h 对 calc 误差高容忍（偏大仅上限松，不强制溢出）。
  return (
    <div className="grid grid-cols-[240px_1fr] gap-4">
      <div className="min-h-0 max-h-[calc(100dvh-14rem)] overflow-y-auto border-r border-border pr-2">
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
      <div ref={viewportRef} className="min-h-0 max-h-[calc(100dvh-14rem)] overflow-auto">
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
            if (ev) return <div key={i} className="border-l-2 border-cyan/40 bg-cyan/10 px-2 font-mono text-xs leading-5 whitespace-nowrap overflow-hidden text-ellipsis">[{ev.ts}] {ev.type} {ev.message ?? ev.tool_name ?? ""}</div>;
            return <div key={i} className="text-sm text-muted-foreground">{l}</div>;
          })
        ) : (
          sel && !contentErr && <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed">{content}</pre>
        )}
      </div>
    </div>
  );
}
