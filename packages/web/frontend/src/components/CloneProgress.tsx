import { useTranslation } from "react-i18next";
import { useEventSource } from "@/api/useEventSource";
import { repoEventsUrl } from "@/api/client";

type CloneEvt = { progress?: number; type?: string; status?: string; error?: string };

/** P2: clone 事件 SSE 走 ws 内路径 /api/workspaces/<ws>/repos/<name>/events。
 *  busyLabelKey：进行中文案的 i18n key 覆盖（上传解压的 extracting 复用此组件，
 *  事件管道同一条 clone.ndjson → SSE，仅文案不同；默认 "clone 中"）。 */
export function CloneProgress({ ws, name, busyLabelKey }: { ws: string; name: string; busyLabelKey?: string }) {
  const { t } = useTranslation();
  const { events, status } = useEventSource(repoEventsUrl(ws, name), "clone_end");
  const last = events[events.length - 1] as CloneEvt | undefined;
  const endEvent = [...events].reverse().find((e) => (e as CloneEvt).type === "clone_end") as CloneEvt | undefined;
  const failed = endEvent?.status === "failed";
  const progress = last?.progress ?? null;

  if (failed) {
    return <div className="text-xs text-destructive">{t("repos.clone.failed", { error: endEvent?.error ?? t("repos.clone.unknownError") })}</div>;
  }
  if (endEvent && !failed) {
    return <div className="text-xs text-green">{t("repos.clone.ready")}</div>;
  }
  if (status === "error") {
    return <div className="text-xs text-yellow">{t("repos.clone.reconnecting")}</div>;
  }
  return (
    <div className="text-xs text-muted-foreground">
      {progress !== null
        ? t("repos.clone.cloningProgress", { progress })
        : t(busyLabelKey ?? "repos.clone.cloning")}
    </div>
  );
}
