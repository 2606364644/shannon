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
  return (
    <Badge variant="outline" className="font-mono text-red border-red/40">
      ● 可达
    </Badge>
  );
}
