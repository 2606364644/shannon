import { memo, useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { useParams } from "react-router-dom";
import { FixedSizeList } from "react-window";
import { apiGet } from "../../api/client";

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
      <div style={style} className="log-row ev-info">
        [{ev.ts}] {ev.type} {ev.message ?? ev.tool_name ?? ""}
      </div>
    );
  }
  return <div style={style} className="trace">{l}</div>;
});

// react-window 包装：行高 20px，对齐 Task 8 LogStream 同模式（FixedSizeList + itemData）。
function VirtualLines({ lines }: { lines: string[] }) {
  return (
    <FixedSizeList height={500} width="100%" itemCount={lines.length} itemSize={ROW_HEIGHT} itemData={lines}>
      {Row}
    </FixedSizeList>
  );
}

export function LogsTab() {
  const { workspace } = useParams<{ workspace: string }>();
  const [files, setFiles] = useState<string[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [content, setContent] = useState("");
  useEffect(() => { apiGet<{ files: string[] }>(`/workspaces/${workspace}/logs`).then((r) => setFiles(r.files)); }, [workspace]);
  useEffect(() => {
    if (!sel) return;
    apiGet<{ content: string }>(`/workspaces/${workspace}/logs?file=${encodeURIComponent(sel)}`).then((r) => setContent(r.content));
  }, [workspace, sel]);

  const isJsonl = sel?.endsWith(".log") && !sel.endsWith("workflow.log") && !sel.endsWith("activity_failures.log");
  const lines = content.split(/\r?\n/).filter(Boolean);
  const big = lines.length > VIRTUAL_LINE_THRESHOLD;

  return (
    <div className="logs-layout">
      <div className="logs-files">
        {files.map((f) => <div key={f} className={`log-file mono ${sel === f ? "sel" : ""}`} onClick={() => setSel(f)}>{f}</div>)}
      </div>
      <div className="logs-content mono">
        {!sel && <div className="trace">选择左侧日志文件</div>}
        {sel && big && isJsonl ? (
          <>
            <div className="trace">⚠ 大文件（{lines.length} 行），虚拟滚动渲染</div>
            <VirtualLines lines={lines} />
          </>
        ) : isJsonl ? (
          lines.map((l, i) => {
            let ev: { ts?: string; type?: string; message?: string; tool_name?: string } | null = null;
            try { ev = JSON.parse(l); } catch { /* 非 JSON */ }
            if (ev) return <div key={i} className="log-row ev-info">[{ev.ts}] {ev.type} {ev.message ?? ev.tool_name ?? ""}</div>;
            return <div key={i} className="trace">{l}</div>;
          })
        ) : (
          sel && <pre>{content}</pre>
        )}
      </div>
    </div>
  );
}
