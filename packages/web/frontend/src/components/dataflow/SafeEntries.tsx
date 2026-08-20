// 排查过的入口区（spec 2026-08-20 §5 区 3）。
// 未匹配到 sink 树的 safe_vectors 平铺（subject + 防护机制 + 位置），区头说明
// 「有起点、无危险终点」：这些输入没有流向任何危险调用点（或已被防护拦下），
// 不成树、不成漏洞——列出证明扫过、查过。空列表 → 区隐藏（无事可证）。
import { useTranslation } from "react-i18next";
import type { SafeVector } from "@/api/types";

export interface SafeEntriesProps {
  vectors: SafeVector[];
}

export function SafeEntries({ vectors }: SafeEntriesProps) {
  const { t } = useTranslation();
  if (vectors.length === 0) return null;
  return (
    <section data-safe-section="" className="space-y-3">
      <header>
        <h3 className="font-medium">{t("workspaceDetail.dataflow.safeTitle")}</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {t("workspaceDetail.dataflow.safeIntro")}
        </p>
      </header>
      <ul className="space-y-1.5">
        {vectors.map((v, i) => (
          <li
            key={i}
            data-safe-vector=""
            className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 rounded-md border border-border bg-card px-3 py-2 text-sm"
          >
            <span className="font-mono font-medium">{v.subject ?? "—"}</span>
            {v.defense_mechanism && (
              <span className="text-xs">
                <span className="text-muted-foreground">
                  {t("workspaceDetail.dataflow.safeDefenseLabel")}：
                </span>
                <span className="text-[hsl(var(--c-green))]">{v.defense_mechanism}</span>
              </span>
            )}
            {v.render_context && (
              <span className="text-xs text-muted-foreground">{v.render_context}</span>
            )}
            {v.location && (
              <span className="ml-auto font-mono text-xs text-muted-foreground">{v.location}</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
