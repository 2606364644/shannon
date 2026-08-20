// 认证 / 授权风险区（spec 2026-08-20 §5 区 2）。
// auth/authz 无数据流——不画树，逐接口检查防护关卡链：endpoint + 关卡卡序列
// （🟢 正常 / 🔴 缺失：dashed 红边 + 流动断线指示污点穿过的缺口 / 🟡 失效），
// detail 引 finding 原文（guard_evidence / missing_defense / mismatch_reason
// 组装器已并入 detail 字段）+ file:line。
import { useTranslation } from "react-i18next";
import type { ControlChainStep, ControlFinding } from "@/api/types";

export interface GuardChainProps {
  controls: ControlFinding[];
}

/** 关卡卡锚点 id（TocSideBar 目录条目与本卡片共用；id 缺失时按序稳定回退）。 */
export function controlAnchorId(f: ControlFinding, index: number): string {
  return f.id ?? `ctl-${index}`;
}

type Status = ControlChainStep["status"];

const STATUS_META: Record<Status, { glyph: string; i18nKey: string; cls: string }> = {
  ok: { glyph: "🟢", i18nKey: "guardOk", cls: "guard-ok" },
  missing: { glyph: "🔴", i18nKey: "guardMissing", cls: "guard-missing" },
  ineffective: { glyph: "🟡", i18nKey: "guardIneffective", cls: "guard-ineffective" },
};

export function GuardChain({ controls }: GuardChainProps) {
  const { t } = useTranslation();
  if (controls.length === 0) return null;
  return (
    <section data-guard-section="" className="space-y-3">
      <header>
        <h3 className="font-medium">{t("workspaceDetail.dataflow.controlsTitle")}</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {t("workspaceDetail.dataflow.controlsIntro")}
        </p>
      </header>
      {controls.map((c, i) => {
        const anchor = controlAnchorId(c, i);
        return (
          <div
            key={anchor}
            data-control-id={anchor}
            className="rounded-lg border border-border bg-card p-3 shadow-card"
          >
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-mono font-medium">{c.endpoint ?? c.id ?? anchor}</span>
              <span className="rounded bg-secondary px-1.5 py-0.5 text-[10px] text-muted-foreground">
                {c.vuln_class}
              </span>
            </div>
            <ol className="mt-2 flex flex-wrap items-stretch gap-2">
              {c.chain.map((step, j) => (
                <GuardStep key={j} step={step} t={t} />
              ))}
            </ol>
          </div>
        );
      })}
    </section>
  );
}

/** 单个关卡卡：状态图标 + label + 白话状态 + （缺失时）流动断线 + detail 原文 + file:line。 */
function GuardStep({
  step,
  t,
}: {
  step: ControlChainStep;
  t: ReturnType<typeof useTranslation>["t"];
}) {
  const meta = STATUS_META[step.status];
  const loc = step.file ? `${step.file}${step.line != null ? `:${step.line}` : ""}` : null;
  return (
    <li
      data-guard-step={step.status}
      className={`guard-step ${meta.cls} min-w-[160px] flex-1 basis-52 rounded-md border px-2.5 py-1.5 text-xs`}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <span aria-hidden>{meta.glyph}</span>
        <span className="font-medium">{step.label ?? t("workspaceDetail.dataflow.guardStep")}</span>
        <span className="text-muted-foreground">
          {t(`workspaceDetail.dataflow.${meta.i18nKey}`)}
        </span>
      </div>
      {/* 缺失关卡：流动断线（红色虚线滚动）指示污点从这个缺口穿过 */}
      {step.status === "missing" && <div data-guard-gap="" className="guard-gap-flow mt-1.5" aria-hidden />}
      {step.detail && <p className="mt-1 text-muted-foreground">{step.detail}</p>}
      {loc && <p className="mt-0.5 font-mono text-[10px] text-muted-foreground">{loc}</p>}
    </li>
  );
}
