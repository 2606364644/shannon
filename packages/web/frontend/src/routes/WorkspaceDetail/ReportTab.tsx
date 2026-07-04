import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { apiGetText } from "../../api/client";
import { MarkdownView } from "../../components/MarkdownView";
import { ErrorState } from "../../components/ErrorState";
import { Empty } from "../../components/Empty";
import { Skeleton } from "@/components/ui/skeleton";

export function ReportTab() {
  const { workspace } = useParams<{ workspace: string }>();
  const [md, setMd] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    if (!workspace) return;
    setLoading(true);
    setErr(null);
    setMd("");
    apiGetText(`/workspaces/${workspace}/report`)
      .then((t) => { setMd(t); setLoading(false); })
      .catch((e: unknown) => { setErr(String(e)); setLoading(false); });
  }, [workspace]);
  if (err) return <ErrorState message={`报告加载失败：${err}`} />;
  if (loading) {
    return (
      <div className="space-y-2">
        {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-6 w-full" />)}
      </div>
    );
  }
  if (!md) return <Empty title="报告尚未生成" hint="扫描完成后将在此呈现" />;
  return (
    <div className="rounded-md border border-border bg-card p-4">
      <MarkdownView markdown={md} />
    </div>
  );
}
