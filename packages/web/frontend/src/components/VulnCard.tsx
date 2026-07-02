import { useState } from "react";
import type { Vulnerability, MergeSource } from "../api/types";

// MergeSource 类型在 types.ts 是 `"llm-only" | "gitnexus-only" | "both" | string` ——
// 末尾 `| string` 会把字面量联合吸收成 string，导致 `===` 后 TS 无法把分支体窄化到具体字面量。
// 这里用 includes 守卫把值映射到一个收窄过的字面量 tag，再 switch；运行时与类型都正确。
type BadgeTag = "llm-only" | "gitnexus-only" | "both" | "other";
function toBadgeTag(src: string): BadgeTag {
  return src === "llm-only" || src === "gitnexus-only" || src === "both"
    ? (src as BadgeTag)
    : "other";
}

export function MergeSourceBadge({ src }: { src?: MergeSource }) {
  if (!src) return null;
  const tag = toBadgeTag(src);
  switch (tag) {
    case "llm-only":
      return <span className="badge ev-llm">💭 LLM轨</span>;
    case "gitnexus-only":
      return <span className="badge ev-info">🔍 GN轨</span>;
    case "both":
      return <span className="badge ev-agent-ok">✓ 双轨确认</span>;
    default:
      return <span className="badge trace">{src}</span>;
  }
}

export function VulnCard({ v }: { v: Vulnerability }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`vuln-card ${v.externally_exploitable ? "reachable" : ""}`}>
      <div className="vc-head mono" onClick={() => setOpen((o) => !o)}>
        <span className="vc-id">{v.ID}</span> {v.vulnerability_type}
        {v.externally_exploitable && <span className="badge ev-agent-fail">● 可达</span>}
        <MergeSourceBadge src={v.merge_source} />
        {v.confidence && <span className="badge trace">{v.confidence}</span>}
        {v.source_endpoint && <span className="trace"> {v.source_endpoint}</span>}
        <span className="trace">{open ? " ▴" : " ▾"}</span>
      </div>
      {open && (
        <div className="vc-detail serif">
          {v.vulnerable_code_location && <div><b>location:</b> <code className="mono">{v.vulnerable_code_location}</code></div>}
          {v.missing_defense && <div><b>missing_defense:</b> {v.missing_defense}</div>}
          {v.exploitation_hypothesis && <div><b>hypothesis:</b> {v.exploitation_hypothesis}</div>}
          {v.suggested_exploit_technique && <div><b>technique:</b> <code className="mono">{v.suggested_exploit_technique}</code></div>}
          {v.notes && <div className="vc-notes"><b>notes:</b> {v.notes}</div>}
        </div>
      )}
    </div>
  );
}
