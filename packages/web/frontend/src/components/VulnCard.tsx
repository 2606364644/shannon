import { useState } from "react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { Vulnerability, MergeSource } from "../api/types";

type BadgeTag = "llm-only" | "gitnexus-only" | "both" | "other";
function toBadgeTag(src: string): BadgeTag {
  return src === "llm-only" || src === "gitnexus-only" || src === "both" ? (src as BadgeTag) : "other";
}

export function MergeSourceBadge({ src }: { src?: MergeSource }) {
  if (!src) return null;
  const tag = toBadgeTag(src);
  const map: Record<Exclude<BadgeTag, "other">, { label: string; cls: string }> = {
    "llm-only": { label: "💭 LLM轨", cls: "border-magenta/40 text-magenta" },
    "gitnexus-only": { label: "🔍 GN轨", cls: "border-cyan/40 text-cyan" },
    "both": { label: "✓ 双轨确认", cls: "border-green/40 text-green" },
  };
  const m = map[tag as Exclude<BadgeTag, "other">];
  return m ? (
    <Badge variant="outline" className={`gap-1 ${m.cls}`}>{m.label}</Badge>
  ) : (
    <Badge variant="outline" className="text-muted-foreground">{src}</Badge>
  );
}

export function VulnCard({ v }: { v: Vulnerability }) {
  const [open, setOpen] = useState(false);
  const toggle = () => setOpen((o) => !o);
  return (
    <Card className={`gap-0 ${v.externally_exploitable ? "border-red/50" : ""}`}>
      <CardHeader
        className="flex cursor-pointer select-none flex-row flex-wrap items-center gap-2 font-mono text-sm"
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={toggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggle();
          }
        }}
      >
        <span className="font-bold text-red">{v.ID}</span>
        <span>{v.vulnerability_type}</span>
        {v.externally_exploitable && (
          <Badge variant="outline" className="border-red/40 text-red">● 可达</Badge>
        )}
        <MergeSourceBadge src={v.merge_source} />
        {v.confidence && <Badge variant="outline" className="text-muted-foreground">{v.confidence}</Badge>}
        {v.source_endpoint && <span className="text-xs text-muted-foreground">{v.source_endpoint}</span>}
        <span className="ml-auto text-xs text-muted-foreground">{open ? "▴" : "▾"}</span>
      </CardHeader>
      {open && (
        <CardContent className="space-y-1 font-serif text-sm">
          {v.vulnerable_code_location && (
            <div><b>location:</b> <code className="font-mono text-cyan">{v.vulnerable_code_location}</code></div>
          )}
          {v.missing_defense && <div><b>missing_defense:</b> {v.missing_defense}</div>}
          {v.exploitation_hypothesis && <div><b>hypothesis:</b> {v.exploitation_hypothesis}</div>}
          {v.suggested_exploit_technique && (
            <div><b>technique:</b> <code className="font-mono text-cyan">{v.suggested_exploit_technique}</code></div>
          )}
          {v.notes && <div className="text-muted-foreground"><b>notes:</b> {v.notes}</div>}
        </CardContent>
      )}
    </Card>
  );
}
