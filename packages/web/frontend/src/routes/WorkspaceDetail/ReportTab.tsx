import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { apiGetText, getScan, scanReportPath } from "../../api/client";
// useTranslation 在子组件 SingleReport/CombinedReport 内使用；顶层 ReportTab 仅路由态。
import { MarkdownView } from "../../components/MarkdownView";
import { ErrorState } from "../../components/ErrorState";
import { Empty } from "../../components/Empty";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

type Track = "whitebox" | "blackbox" | "combined";

/**
 * 报告 tab。
 * 非组合（combined!=true）：原单报告视图（GET /report auto-infer）——零回归。
 * 组合（combined=true，spec §10.1 三视图）：渲染 [白盒报告 | 黑盒报告 | 融合报告] 子 tab，
 *   各拉 scanReportPath(ws, id, track)（?track=whitebox/blackbox/combined）。
 */
export function ReportTab() {
  const { workspace, scanId } = useParams<{ workspace: string; scanId: string }>();
  const [combined, setCombined] = useState<boolean | null>(null);

  // 先探 combined 标记（getScan 透传 session.combined）。失败/非组合 → null（走单视图）。
  useEffect(() => {
    if (!workspace || !scanId) return;
    setCombined(null);
    getScan(workspace, scanId)
      .then((s) => setCombined(s.combined === true))
      .catch(() => setCombined(false));
  }, [workspace, scanId]);

  if (combined === null) {
    // combined 探测中：Skeleton（与单视图 loading 一致外观）。
    return (
      <div className="space-y-2">
        {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-6 w-full" />)}
      </div>
    );
  }
  if (combined) return <CombinedReport ws={workspace!} scanId={scanId!} />;
  return <SingleReport ws={workspace!} scanId={scanId!} />;
}

/** 非组合：原单报告视图（零回归）。 */
function SingleReport({ ws, scanId }: { ws: string; scanId: string }) {
  const { t } = useTranslation();
  const [md, setMd] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    setLoading(true);
    setErr(null);
    setMd("");
    apiGetText(scanReportPath(ws, scanId))
      .then((txt) => { setMd(txt); setLoading(false); })
      .catch((e: unknown) => { setErr(String(e)); setLoading(false); });
  }, [ws, scanId]);
  if (err) return <ErrorState message={t("workspaceDetail.report.loadError", { error: err })} />;
  if (loading) {
    return (
      <div className="space-y-2">
        {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-6 w-full" />)}
      </div>
    );
  }
  if (!md) return <Empty title={t("workspaceDetail.report.emptyTitle")} hint={t("workspaceDetail.report.emptyHint")} />;
  return (
    <div className="rounded-md border border-border bg-card p-4">
      <MarkdownView markdown={md} />
    </div>
  );
}

/** 组合：三子 tab，各拉对应 track 报告。 */
function CombinedReport({ ws, scanId }: { ws: string; scanId: string }) {
  const { t } = useTranslation();
  const [track, setTrack] = useState<Track>("combined");
  const [md, setMd] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    setErr(null);
    setMd("");
    apiGetText(scanReportPath(ws, scanId, track))
      .then((txt) => { setMd(txt); setLoading(false); })
      .catch((e: unknown) => { setErr(String(e)); setLoading(false); });
  }, [ws, scanId, track]);

  return (
    <div className="space-y-3">
      <Tabs value={track} onValueChange={(v) => setTrack(v as Track)}>
        <TabsList>
          <TabsTrigger value="whitebox">{t("workspaceDetail.report.combined.tabWhitebox")}</TabsTrigger>
          <TabsTrigger value="blackbox">{t("workspaceDetail.report.combined.tabBlackbox")}</TabsTrigger>
          <TabsTrigger value="combined">{t("workspaceDetail.report.combined.tabCombined")}</TabsTrigger>
        </TabsList>
      </Tabs>
      {err ? (
        <ErrorState message={t("workspaceDetail.report.loadError", { error: err })} />
      ) : loading ? (
        <div className="space-y-2">
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-6 w-full" />)}
        </div>
      ) : !md ? (
        <Empty title={t("workspaceDetail.report.emptyTitle")} hint={t("workspaceDetail.report.emptyHint")} />
      ) : (
        <div className="rounded-md border border-border bg-card p-4">
          <MarkdownView markdown={md} />
        </div>
      )}
    </div>
  );
}
