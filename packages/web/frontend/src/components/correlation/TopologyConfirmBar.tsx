import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";

interface Props {
  /** 拓扑带 AI 分析来源（哪怕手工改过）→ 须显式确认才能提交；纯手搭免确认（null 不渲染）。 */
  needsConfirm: boolean;
  /** 页面层确认态（fingerprint + YAML canonical 语义比对全通过）。 */
  confirmed: boolean;
  onConfirm: () => void;
}

/** 拓扑确认门禁状态条（2026-09-04 工作台化）：tabs 行右侧、三视图共享——「这个拓扑
 *  可信吗」是跨仓扫描的工作流灵魂（AI 推断产物须人工背书才可提交），不该埋在图
 *  编辑器底部的一行按钮里。琥珀点 = AI 草稿待确认（与 tab 标签琥珀点同一语义色）；
 *  确认后降为 muted ✓（提交按钮亮起是最终信号，这里只回echo）。 */
export function TopologyConfirmBar({ needsConfirm, confirmed, onConfirm }: Props) {
  const { t } = useTranslation();
  if (!needsConfirm) return null;
  return (
    <div data-testid="corr-confirm-bar" className="flex items-center gap-2">
      {confirmed ? (
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <span aria-hidden>✓</span>{t("scan.correlation.topology.confirmedBadge")}
        </span>
      ) : (
        <>
          <span data-testid="corr-confirm-pending" className="flex items-center gap-1.5 text-xs text-amber">
            <span aria-hidden className="size-1.5 rounded-full bg-amber" />
            {t("scan.correlation.topology.draftBadge")}
          </span>
          <Button type="button" variant="outline" size="sm" onClick={onConfirm}>
            {t("scan.correlation.topology.confirm")}
          </Button>
        </>
      )}
    </div>
  );
}
