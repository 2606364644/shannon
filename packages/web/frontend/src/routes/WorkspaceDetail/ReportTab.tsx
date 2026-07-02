import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { apiGetText } from "../../api/client";
import { MarkdownView } from "../../components/MarkdownView";

export function ReportTab() {
  const { workspace } = useParams<{ workspace: string }>();
  const [md, setMd] = useState("");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!workspace) return;
    setMd("");
    setErr(null);
    apiGetText(`/workspaces/${workspace}/report`).then(setMd).catch((e) => setErr(String(e)));
  }, [workspace]);
  if (err) return <div className="trace error">报告加载失败：{err}</div>;
  if (!md) return <div className="trace">加载报告…</div>;
  return <MarkdownView markdown={md} />;
}
