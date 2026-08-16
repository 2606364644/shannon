import { useTranslation } from "react-i18next";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** 内置角色预设（值即存档的 role 字符串；标签走 i18n scan.auth.rolePresets.*）。
 *  覆盖最常见的两类（超管/管理员）+ 低权用户；审计管理员等特殊角色不预设，直接输入即可。 */
export const ROLE_PRESETS = ["superadmin", "admin", "user"] as const;

/** 多角色凭据录入草稿（前端内部态，scan 页 inline + 档案 dialog 共用）。
 *  - 新建 id 空（后端分配）；编辑 id 透传原值。
 *  - password 空串 = 不改（编辑态）。 */
export interface CredentialDraft {
  id?: string;
  role: string;
  username: string;
  password: string;
}

interface Props {
  value: CredentialDraft[];
  onChange: (next: CredentialDraft[]) => void;
  /** true = 多角色（显「+ 添加角色」+ 每行删除，最少 1 行）；false = 单行只录。 */
  allowMulti: boolean;
  /** true = 锁定首行（value[0]）不可删——scan 页 inline 用以保护 primary 凭据恒在 index 0。
   *  档案 dialog 不传（所有角色都可删，只要 length>1）。 */
  lockFirstRow?: boolean;
}

/** 多角色凭据增删行：每行 角色/用户名/密码/删除；底部「+ 添加角色」。
 *  受控组件——值与变更全由父级管；本组件不持状态（草稿存在父级 AuthFormState / dialog state）。
 *  add() 只 append（不 unshift），保证 value[0] 恒为 primary（lockFirstRow 时不可删）。 */
export function CredentialRows({ value, onChange, allowMulti, lockFirstRow }: Props) {
  const { t } = useTranslation();

  function update(i: number, patch: Partial<CredentialDraft>) {
    const next = value.slice();
    next[i] = { ...next[i], ...patch };
    onChange(next);
  }
  function add() {
    onChange([...value, { role: "", username: "", password: "" }]);
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
              <Input
                id={`cr-role-${i}`}
                value={d.role}
                onChange={(e) => update(i, { role: e.target.value })}
                placeholder={t("scan.auth.rolePlaceholder")}
                size="sm"
              />
              {/* 内置角色快选：点 chip 即填入对应 role 值；当前值命中时高亮。
                  特殊角色（如审计管理员）不内置，直接在输入框填写。 */}
              <div className="flex flex-wrap gap-1 pt-0.5" title={t("scan.auth.rolePresetsHint")}>
                {ROLE_PRESETS.map((p) => (
                  <button
                    key={p}
                    type="button"
                    aria-pressed={d.role === p}
                    onClick={() => update(i, { role: p })}
                    className={cn(
                      "rounded-full border px-2 py-0.5 text-[10.5px] transition-colors",
                      d.role === p
                        ? "border-primary/60 bg-primary/10 font-medium text-foreground"
                        : "border-border bg-muted/30 text-muted-foreground hover:bg-muted/70",
                    )}
                  >
                    {t(`scan.auth.rolePresets.${p}`)}
                  </button>
                ))}
              </div>
            </div>
            <div className="space-y-1">
              <Label htmlFor={`cr-user-${i}`} className="text-[11px] text-muted-foreground">{t("scan.auth.username")}</Label>
              <Input id={`cr-user-${i}`} value={d.username} onChange={(e) => update(i, { username: e.target.value })} size="sm" />
            </div>
            <div className="space-y-1">
              <Label htmlFor={`cr-pw-${i}`} className="text-[11px] text-muted-foreground">{t("scan.auth.password")}</Label>
              <Input id={`cr-pw-${i}`} type="password" value={d.password} onChange={(e) => update(i, { password: e.target.value })} size="sm" />
            </div>
          </div>
          {allowMulti && value.length > 1 && !(lockFirstRow && i === 0) && (
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
