import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { Vulnerability, MergeSource } from "../api/types";

type BadgeTag = "llm-only" | "gitnexus-only" | "both" | "other";
function toBadgeTag(src: string): BadgeTag {
  return src === "llm-only" || src === "gitnexus-only" || src === "both" ? (src as BadgeTag) : "other";
}

export function MergeSourceBadge({ src }: { src?: MergeSource }) {
  const { t } = useTranslation();
  if (!src) return null;
  const tag = toBadgeTag(src);
  const map: Record<Exclude<BadgeTag, "other">, { glyph: string; key: string; cls: string }> = {
    "llm-only": { glyph: "💭", key: "vuln.llmTrack", cls: "border-magenta/40 text-magenta" },
    "gitnexus-only": { glyph: "🔍", key: "vuln.gnTrack", cls: "border-cyan/40 text-cyan" },
    "both": { glyph: "✓", key: "vuln.dualConfirmed", cls: "border-green/40 text-green" },
  };
  const m = map[tag as Exclude<BadgeTag, "other">];
  return m ? (
    <Badge variant="outline" className={`gap-1 ${m.cls}`}>{m.glyph} {t(m.key)}</Badge>
  ) : (
    <Badge variant="outline" className="text-muted-foreground">{src}</Badge>
  );
}

export function VulnCard({ v, dataflowTreeId }: { v: Vulnerability; dataflowTreeId?: string | null }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const toggle = () => setOpen((o) => !o);
  return (
    <Card className="gap-0">
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
        {/* ID 中性 semibold（spec 2026-08-27 §2.1：ID 是标识符不是警报——队列里逐条
            标红携带零信息，红色稀缺预算留给 severity/top risks） */}
        <span className="font-semibold text-foreground">{v.ID}</span>
        <span>{v.title || v.vulnerability_type}</span>
        {/* 可达性走字形通道（⌖ 中性徽章），不与 severity 抢红色 */}
        {v.externally_exploitable && (
          <Badge variant="outline" className="text-foreground/75">⌖ {t("vuln.reachable")}</Badge>
        )}
        <MergeSourceBadge src={v.merge_source} />
        {v.confidence && <Badge variant="outline" className="text-muted-foreground">{v.confidence}</Badge>}
        {v.source_endpoint && <span className="text-xs text-muted-foreground">{v.source_endpoint}</span>}
        <span className="ml-auto text-xs text-muted-foreground">{open ? "▴" : "▾"}</span>
      </CardHeader>
      {open && (
        <CardContent className="space-y-1 text-sm">
          {v.vulnerable_code_location && (
            <div><b>location:</b> <code className="font-mono text-cyan">{v.vulnerable_code_location}</code></div>
          )}
          {v.missing_defense && <div><b>missing_defense:</b> {v.missing_defense}</div>}
          {v.exploitation_hypothesis && <div><b>hypothesis:</b> {v.exploitation_hypothesis}</div>}
          {v.suggested_exploit_technique && (
            <div><b>technique:</b> <code className="font-mono text-cyan">{v.suggested_exploit_technique}</code></div>
          )}
          {v.notes && <div className="text-muted-foreground"><b>notes:</b> {v.notes}</div>}
          {/* 数据流跳转（spec 2026-08-20 §5 路由与入口）：taint 树上的 finding 由
              DeliverablesTab 建 finding_id → tree_id 映射传入；无映射（auth/authz、
              树未含该 finding、无 dataflow 产物）不渲染链接。相对路由 ../dataflow
              与本 tab（deliverables）同级；落点 = DataFlowTab ?tree= 锚点定位 + 闪烁。 */}
          {dataflowTreeId && (
            <div>
              <Link
                to={`../dataflow?tree=${encodeURIComponent(dataflowTreeId)}`}
                data-testid="vuln-dataflow-link"
                className="inline-flex items-center gap-1 text-primary hover:underline"
              >
                {t("vuln.viewDataflow")} <span aria-hidden>→</span>
              </Link>
            </div>
          )}
        </CardContent>
      )}
    </Card>
  );
}
