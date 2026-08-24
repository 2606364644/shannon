import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { CorrYamlError } from "@/lib/correlation-yaml";
import { formatCorrIssue } from "./corr-issues-i18n";

/** 跨仓关联 YAML 面板（D3）：折叠（默认收起）+ textarea + 错误行提示 + 「应用到表单」按钮。
 *  单向数据流约定（brief）：表单交互路径 yaml 由父层 formToYaml(state) 派生；本面板的
 *  textarea 编辑仅向上 onYaml（父层校验、错误经 error 回显），回填表单必须走显式
 *  onApply 按钮——不在输入中间态实时回填（防抖动/防回路）。 */
export function YamlPanel({ yaml, onChange, error, onApply }: {
  yaml: string;
  onChange: (y: string) => void;
  error: CorrYamlError | null;
  onApply: () => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <div data-testid="corr-yaml-panel" className="space-y-2">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="text-xs font-medium text-primary hover:underline"
      >
        {t("scan.correlation.yamlToggle")}
      </button>
      {open && (
        <div className="space-y-2">
          <Textarea
            aria-label={t("scan.correlation.yamlEditor")}
            value={yaml}
            onChange={(e) => onChange(e.target.value)}
            rows={14}
            className="font-mono text-xs w-full"
            spellCheck={false}
          />
          {error && (
            <p role="alert" className="text-destructive text-xs">
              {error.issues.map((m) => formatCorrIssue(m, t)).join("; ")}
            </p>
          )}
          <div>
            <Button type="button" variant="outline" onClick={onApply} disabled={!!error}>
              {t("scan.correlation.applyYaml")}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
