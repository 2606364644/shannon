import { useTranslation } from "react-i18next";
import { Textarea } from "@/components/ui/textarea";
import type { CorrYamlError } from "@/lib/correlation-yaml";
import { formatCorrIssue } from "./corr-issues-i18n";

/** 跨仓关联 YAML tab（2026-09-04 tabs 重组）：恒展开编辑器——YAML 是三视图子页之一
 *  （图 | 表单 | YAML），不再有折叠形态与「应用到表单」按钮：文本是源，解析成功即
 *  扇出重建表单+图；出错时表单/图保持上次有效态，错误行说明这一行为，用户原文
 *  （注释/排版）不被 canonical 化回写。 */
export function YamlPanel({ yaml, onChange, error, synced = false }: {
  yaml: string;
  onChange: (y: string) => void;
  error: CorrYamlError | null;
  synced?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div data-testid="corr-yaml-panel" className="space-y-2">
      {synced && (
        <p className="text-[11px] text-muted-foreground">{t("scan.correlation.yamlSyncHint")}</p>
      )}
      <Textarea
        aria-label={t("scan.correlation.yamlEditor")}
        value={yaml}
        onChange={(e) => onChange(e.target.value)}
        rows={14}
        className="font-mono text-xs w-full"
        spellCheck={false}
      />
      {error ? (
        <p role="alert" className="text-destructive text-xs">
          {error.issues.map((m) => formatCorrIssue(m, t)).join("; ")}
          {synced && <span className="block text-muted-foreground">{t("scan.correlation.yamlErrorKeepGraph")}</span>}
        </p>
      ) : null}
    </div>
  );
}
