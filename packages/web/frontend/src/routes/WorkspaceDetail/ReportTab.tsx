import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { apiGetText, scanReportPath } from "../../api/client";
import { MarkdownView } from "../../components/MarkdownView";
import { ErrorState } from "../../components/ErrorState";
import { Empty } from "../../components/Empty";
import { Skeleton } from "@/components/ui/skeleton";

export function ReportTab() {
  const { t } = useTranslation();
  const { workspace, scanId } = useParams<{ workspace: string; scanId: string }>();
  const [md, setMd] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    if (!workspace || !scanId) return;
    setLoading(true);
    setErr(null);
    setMd("");
    apiGetText(scanReportPath(workspace, scanId))
      .then((txt) => { setMd(txt); setLoading(false); })
      .catch((e: unknown) => { setErr(String(e)); setLoading(false); });
  }, [workspace, scanId]);
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
