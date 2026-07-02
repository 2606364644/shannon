import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { apiGet, apiGetText } from "../../api/client";
import type { DeliverablesSummary, DeliverablesFile } from "../../api/types";
import { FileTree } from "../../components/FileTree";
import { MarkdownView } from "../../components/MarkdownView";
import { VulnCard } from "../../components/VulnCard";

export function DeliverablesTab() {
  const { workspace } = useParams<{ workspace: string }>();
  const [data, setData] = useState<DeliverablesSummary | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sel, setSel] = useState<DeliverablesFile | null>(null);
  useEffect(() => {
    if (!workspace) return;
    setData(null);
    setSel(null);
    setErr(null);
    apiGet<DeliverablesSummary>(`/workspaces/${workspace}/deliverables`)
      .then(setData)
      .catch((e) => { setData(null); setErr(String(e)); });
  }, [workspace]);
  if (err) return <div className="trace error">产物加载失败：{err}</div>;
  if (!data) return <div className="trace">加载产物…</div>;
  return (
    <div className="deliverables-layout">
      <div className="vuln-grid">
        <h3>漏洞聚合 · {data.aggregated_vulnerabilities.length}</h3>
        {data.notes?.injection_has_no_queue && (
          <div className="trace">⚠ injection 类无独立 queue（仅 analysis_deliverable + 报告），聚合不含 injection —— 见报告</div>
        )}
        {data.aggregated_vulnerabilities.length === 0 && (
          <div className="trace">暂无聚合漏洞（injection 类不走 queue，或扫描未完成）</div>
        )}
        {data.aggregated_vulnerabilities.map((v) => <VulnCard key={v.ID} v={v} />)}
      </div>
      <div className="deliverables-side">
        <FileTree files={data.files} onSelect={setSel} />
        {sel && <FilePreview ws={workspace!} file={sel} />}
      </div>
    </div>
  );
}

function FilePreview({ ws, file }: { ws: string; file: DeliverablesFile }) {
  const [content, setContent] = useState("");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    setContent("");
    setErr(null);
    if (file.kind === "md" || file.kind === "other_json" || file.kind.endsWith("_queue")) {
      apiGetText(`/workspaces/${ws}/deliverables?path=${encodeURIComponent(file.path)}`)
        .then(setContent)
        .catch((e) => { setContent(""); setErr(String(e)); });
    }
  }, [ws, file.path]);
  if (err) return <div className="trace error">文件加载失败：{err}</div>;
  if (file.kind === "empty_json") return <div className="trace">无数据（常态空）</div>;
  if (file.kind === "big_json")
    return (
      <div className="trace">
        大 JSON，用树查看器（虚拟滚动）
        <pre className="mono">{content.slice(0, 500)}…</pre>
      </div>
    );
  if (file.kind === "md") return <MarkdownViewLazy content={content} />;
  return <pre className="mono">{content}</pre>;
}

function MarkdownViewLazy({ content }: { content: string }) {
  return content ? <MarkdownView markdown={content} /> : <div className="trace">加载…</div>;
}
