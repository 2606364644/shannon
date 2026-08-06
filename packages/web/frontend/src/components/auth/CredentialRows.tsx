import { useTranslation } from "react-i18next";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";

/** 多角色凭据录入草稿（前端内部态，scan 页 inline + 档案 dialog 共用）。
 *  - 新建 id 空（后端分配）；编辑 id 透传原值。
 *  - password 空串 = 不改（编辑态）；totpSecret 空串 = 无。 */
export interface CredentialDraft {
  id?: string;
  role: string;
  username: string;
  password: string;
  totpSecret: string;
}

interface Props {
  value: CredentialDraft[];
  onChange: (next: CredentialDraft[]) => void;
  /** true = 多角色（显「+ 添加角色」+ 每行删除，最少 1 行）；false = 单行只录。 */
  allowMulti: boolean;
  /** true = 每行显 TOTP 字段；false = 隐藏。 */
  showTotp: boolean;
}

/** 多角色凭据增删行（2026-08-07 #2）：每行 角色/用户名/密码/可选 TOTP/删除；底部「+ 添加角色」。
 *  受控组件——值与变更全由父级管；本组件不持状态（草稿存在父级 AuthFormState / dialog state）。 */
export function CredentialRows({ value, onChange, allowMulti, showTotp }: Props) {
  const { t } = useTranslation();

  function update(i: number, patch: Partial<CredentialDraft>) {
    const next = value.slice();
    next[i] = { ...next[i], ...patch };
    onChange(next);
  }
  function add() {
    onChange([...value, { role: "", username: "", password: "", totpSecret: "" }]);
  }
  function remove(i: number) {
    onChange(value.filter((_, idx) => idx !== i));
  }

  return (
    <div className="space-y-3">
      {value.map((d, i) => (
        <div key={d.id ?? i} className="rounded-lg border border-border bg-secondary p-3 space-y-2">
          <div className="grid grid-cols-3 gap-2">
            <div className="space-y-1">
              <Label htmlFor={`cr-role-${i}`} className="text-[11px] text-muted-foreground">{t("scan.auth.role")}</Label>
              <Input id={`cr-role-${i}`} value={d.role} onChange={(e) => update(i, { role: e.target.value })} className="text-xs" />
            </div>
            <div className="space-y-1">
              <Label htmlFor={`cr-user-${i}`} className="text-[11px] text-muted-foreground">{t("scan.auth.username")}</Label>
              <Input id={`cr-user-${i}`} value={d.username} onChange={(e) => update(i, { username: e.target.value })} className="text-xs" />
            </div>
            <div className="space-y-1">
              <Label htmlFor={`cr-pw-${i}`} className="text-[11px] text-muted-foreground">{t("scan.auth.password")}</Label>
              <Input id={`cr-pw-${i}`} type="password" value={d.password} onChange={(e) => update(i, { password: e.target.value })} className="text-xs" />
            </div>
          </div>
          {showTotp && (
            <div className="space-y-1">
              <Label htmlFor={`cr-totp-${i}`} className="text-[11px] text-muted-foreground">
                {t("scan.auth.totpSecret")} <span className="font-normal">({t("scan.auth.optional")})</span>
              </Label>
              <Input id={`cr-totp-${i}`} value={d.totpSecret} onChange={(e) => update(i, { totpSecret: e.target.value })} className="font-mono text-xs" />
            </div>
          )}
          {allowMulti && value.length > 1 && (
            <Button type="button" variant="ghost" size="sm" onClick={() => remove(i)} className="text-xs h-7">
              {t("scan.auth.removeRole")}
            </Button>
          )}
        </div>
      ))}
      {allowMulti && (
        <Button type="button" variant="outline" size="sm" onClick={add} className="text-xs">
          {t("scan.auth.addRole")}
        </Button>
      )}
    </div>
  );
}
