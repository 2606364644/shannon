import { useEventSource } from "@/api/useEventSource";

type CloneEvt = { progress?: number; type?: string; status?: string; error?: string };

export function CloneProgress({ name }: { name: string }) {
  const { events } = useEventSource(`/api/repos/${name}/events`, "clone_end");
  const last = events[events.length - 1] as CloneEvt | undefined;
  const endEvent = [...events].reverse().find((e) => (e as CloneEvt).type === "clone_end") as CloneEvt | undefined;
  const failed = endEvent?.status === "failed";
  const progress = last?.progress ?? null;

  if (failed) {
    return <div className="text-xs text-destructive">clone 失败：{endEvent?.error ?? "未知错误"}</div>;
  }
  if (endEvent && !failed) {
    return <div className="text-xs text-green">✓ 就绪</div>;
  }
  return (
    <div className="text-xs text-muted-foreground">
      clone 中{progress !== null ? `… ${progress}%` : "…"}
    </div>
  );
}
