import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { FixedSizeList } from "react-window";
import { apiGet } from "../../api/client";

const VIRTUAL_THRESHOLD = 100_000;
const ROW_HEIGHT = 20;

// react-window 包装：行高 20px，对齐 Task 8 LogStream 同模式（FixedSizeList + itemData）。
function VirtualLines({ lines }: { lines: string[] }) {
  const Row = ({ index, style, data }: { index: number; style: React.CSSProperties; data: string[] }) => {
    const l = data[index];
    let ev: { ts?: string; type?: string; message?: string; tool_name?: string } | null = null;
    try { ev = JSON.parse(l); } catch { /* 非 JSON，按原文本渲染 */ }
    if (ev) {
      return (
        <div style={style} className="log-row ev-info">
          [{ev.ts}] {ev.type} {ev.message ?? ev.tool_name ?? ""}
        </div>
      );
    }
    return <div style={style} className="trace">{l}</div>;
  };
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
  const big = content.length > VIRTUAL_THRESHOLD;

  return (
    <div className="logs-layout">
      <div className="logs-files">
        {files.map((f) => <div key={f} className={`log-file mono ${sel === f ? "sel" : ""}`} onClick={() => setSel(f)}>{f}</div>)}
      </div>
      <div className="logs-content mono">
        {!sel && <div className="trace">选择左侧日志文件</div>}
        {sel && big && isJsonl ? (
          <>
            <div className="trace">⚠ 大文件（{content.length} 字符），虚拟滚动渲染</div>
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
