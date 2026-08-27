import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export type MergeSource = "llm-only" | "gitnexus-only" | "both";

const MERGE_MAP: Record<MergeSource, { label: string; cls: string }> = {
  "llm-only": { label: "💭 LLM轨", cls: "text-magenta border-magenta/40" },
  "gitnexus-only": { label: "🔍 GN轨", cls: "text-cyan border-cyan/40" },
  "both": { label: "✓ 双轨确认", cls: "text-green border-green/40" },
};

export function MergeSourceBadge({ source }: { source: MergeSource }) {
  const m = MERGE_MAP[source];
  return (
    <Badge variant="outline" className={cn("font-mono", m.cls)}>
      {m.label}
    </Badge>
  );
}

export function ReachableBadge({ reachable }: { reachable: boolean }) {
  if (!reachable) {
    return (
      <Badge variant="outline" className="font-mono text-muted-foreground">
        ○ 内部
      </Badge>
    );
  }
  /* 可达性走字形通道（⌖ 中性），不与 severity 抢红色（spec 2026-08-27 §2.1） */
  return (
    <Badge variant="outline" className="font-mono text-foreground/75">
      ⌖ 可达
    </Badge>
  );
}
