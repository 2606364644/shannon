import { useTranslation } from "react-i18next";

/**
 * 验证缺口节（spec 2026-09-03-blackbox-verification-gap-traceability §8）。
 *
 * 融合报告卡片区后的「哪些没验证成、为什么」清单：每条 = 可定位漏洞 ID +
 * 真实原因（agent 中断元数据与端点痕迹 / 登记被校验拒收的拒因 / 未跑类）。
 * 数据源 report_data.verification_gaps（report_fusion 产 [{vuln_id, reason}]）；
 * 空或缺失不渲染——纯白盒报告零回归。
 */
export function VerificationGapsSection({
  gaps,
  onLocate,
}: {
  gaps?: Array<{ vuln_id: string; reason?: string | null }> | null;
  onLocate: (vulnId: string) => void;
}) {
  const { t } = useTranslation();
  if (!gaps || gaps.length === 0) return null;
  return (
    <section
      data-testid="verification-gaps-section"
      className="space-y-2 rounded-md border border-border bg-card p-4 shadow-[var(--shadow-card)]"
    >
      <h2 className="text-base font-semibold tracking-tight">
        {t("report.verificationGaps")}
      </h2>
      <div className="space-y-1.5">
        {gaps.map((g) => (
          <div
            key={g.vuln_id}
            data-testid="verification-gap-row"
            className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[12.5px]"
          >
            <button
              type="button"
              onClick={() => onLocate(g.vuln_id)}
              className="rounded-full border border-border px-1.5 py-0.5 font-mono text-[10.5px] text-primary transition-colors hover:bg-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
              aria-label={`locate ${g.vuln_id}`}
            >
              {g.vuln_id}
            </button>
            {g.reason && (
              <span className="text-muted-foreground">{g.reason}</span>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
