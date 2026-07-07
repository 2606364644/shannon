import { useEventSource } from "@/api/useEventSource";

export function CloneProgress({ name }: { name: string }) {
  const { events, status } = useEventSource(`/api/repos/${name}/events`, "clone_end");
  const last = events[events.length - 1];
  const progress = (last as { progress?: number } | undefined)?.progress ?? null;
  const failed = (last as { type?: string; status?: string } | undefined)?.type === "clone_end"
    && (last as { status?: string }).status === "failed";

  if (failed) {
    return <div className="text-xs text-destructive">clone 失败：{(last as { error?: string }).error ?? "未知错误"}</div>;
  }
  if (status === "closed") {
    return <div className="text-xs text-green">✓ 就绪</div>;
  }
  return (
    <div className="text-xs text-muted-foreground">
      clone 中{progress !== null ? `… ${progress}%` : "…"}
    </div>
  );
}
