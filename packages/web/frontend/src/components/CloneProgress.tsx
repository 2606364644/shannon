import { useTranslation } from "react-i18next";
import { useEventSource } from "@/api/useEventSource";

type CloneEvt = { progress?: number; type?: string; status?: string; error?: string };

export function CloneProgress({ name }: { name: string }) {
  const { t } = useTranslation();
  const { events, status } = useEventSource(`/api/repos/${name}/events`, "clone_end");
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
      {progress !== null ? t("repos.clone.cloningProgress", { progress }) : t("repos.clone.cloning")}
    </div>
  );
}
