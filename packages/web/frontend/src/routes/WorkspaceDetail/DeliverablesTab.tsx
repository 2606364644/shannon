import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { apiGet, apiGetText } from "../../api/client";
import type { DeliverablesSummary, DeliverablesFile } from "../../api/types";
import { FileTree } from "../../components/FileTree";
import { MarkdownView } from "../../components/MarkdownView";
import { VulnCard } from "../../components/VulnCard";
import { ErrorState } from "../../components/ErrorState";
import { Empty } from "../../components/Empty";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

export function DeliverablesTab() {
  const { workspace } = useParams<{ workspace: string }>();
  const [data, setData] = useState<DeliverablesSummary | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [sel, setSel] = useState<DeliverablesFile | null>(null);
  useEffect(() => {
    if (!workspace) return;
    setLoading(true);
    setData(null);
    setSel(null);
    setErr(null);
    apiGet<DeliverablesSummary>(`/workspaces/${workspace}/deliverables`)
      .then((d) => { setData(d); setLoading(false); })
      .catch((e: unknown) => { setData(null); setErr(String(e)); setLoading(false); });
  }, [workspace]);

  // 三态早返回（同 Task 9 模式）：err → ErrorState；loading → Skeleton；data → 主布局
  if (err) return <ErrorState message={`产物加载失败：${err}`} />;
  if (loading) {
    return (
      <div className="space-y-2">
        {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-8 w-full" />)}
      </div>
    );
  }
  if (!data) return null;

  return (
    <div className="grid grid-cols-[1fr_360px] items-start gap-5">
      <div className="space-y-2">
        <h3 className="mb-2 font-semibold tracking-tight text-base">
          漏洞聚合 · {data.aggregated_vulnerabilities.length}
        </h3>
        {data.notes?.injection_has_no_queue && (
          /*
            injection 走 GitNexus 轨候选，不产独立 exploitation queue。
            用 Badge 的原生 title 属性做 tooltip，避免引入 Radix TooltipProvider
            横切（@/components/ui/tooltip 需 app 层 provider，本处不值得）。
          */
          <div>
            <Badge
              variant="outline"
              className="text-muted-foreground"
              title="injection 走 GitNexus 轨候选，不产独立 exploitation queue"
            >
              💡 injection 类
            </Badge>
          </div>
        )}
        {data.aggregated_vulnerabilities.length === 0 && (
          <Empty title="暂无聚合漏洞" hint="扫描未完成或无 vuln 命中" />
        )}
        {data.aggregated_vulnerabilities.map((v) => <VulnCard key={v.ID} v={v} />)}
      </div>
      <div className="max-h-[calc(100vh-200px)] overflow-auto border-l border-border pl-4">
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
  // 文件预览失败：局部 ErrorState（不整页崩，左侧 vuln grid 仍可用）
  if (err) return <ErrorState message={`文件加载失败：${err}`} />;
  if (file.kind === "empty_json") return <div className="text-sm text-muted-foreground">无数据（常态空）</div>;
  if (file.kind === "big_json")
    // apiGetText 无 range/limit 支持，大 JSON 全量拉取代价高且卡 UI。
    // 改为诚实的『文件过大』提示 + 字节数，引导用户去产物目录/日志查看，
    // 不渲染永远为空的 <pre>（content 未 fetch，旧实现是死预览）。
    return (
      <div className="text-sm text-yellow">
        ⚠ 文件过大（{file.size} 字节），请在产物目录查看
      </div>
    );
  if (file.kind === "md") return <MarkdownViewLazy content={content} />;
  return <pre className="font-mono text-xs">{content}</pre>;
}

function MarkdownViewLazy({ content }: { content: string }) {
  return content ? <MarkdownView markdown={content} /> : <div className="text-sm text-muted-foreground">加载…</div>;
}
