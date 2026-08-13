import { useTranslation } from "react-i18next";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";

/** HOST 映射录入草稿（前端内部态，档案 dialog 持有）。
 *  - 新建无 id（后端分配）；编辑透传原 ip+host 作 key。
 *  - grid-cols-2：IP / 域名 两列，增删行。 */
export interface MappingDraft {
  ip: string;
  host: string;
}

interface Props {
  value: MappingDraft[];
  onChange: (next: MappingDraft[]) => void;
}

/** domain→IP 映射行编辑器：每行 IP / 域名 / 删除；底部「+ 添加映射」。
 *  受控组件——值与变更全由父级管；本组件不持状态（草稿存在父级 dialog state）。
 *  add() 只 append，保证顺序稳定。最少 1 行（length<=1 时隐藏删除）。 */
export function MappingRows({ value, onChange }: Props) {
  const { t } = useTranslation();

  function update(i: number, patch: Partial<MappingDraft>) {
    const next = value.slice();
    next[i] = { ...next[i], ...patch };
    onChange(next);
  }
  function add() {
    onChange([...value, { ip: "", host: "" }]);
  }
  function remove(i: number) {
    onChange(value.filter((_, idx) => idx !== i));
  }

  return (
    <div className="space-y-3">
      {value.map((d, i) => (
        <div key={i} className="rounded-lg border border-border bg-secondary p-3 space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <Label htmlFor={`hm-ip-${i}`} className="text-[11px] text-muted-foreground">
                {t("hostProfiles.ip")}
              </Label>
              <Input
                id={`hm-ip-${i}`}
                value={d.ip}
                onChange={(e) => update(i, { ip: e.target.value })}
                className="text-xs font-mono"
                placeholder="10.0.0.1"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor={`hm-host-${i}`} className="text-[11px] text-muted-foreground">
                {t("hostProfiles.host")}
              </Label>
              <Input
                id={`hm-host-${i}`}
                value={d.host}
                onChange={(e) => update(i, { host: e.target.value })}
                className="text-xs font-mono"
                placeholder="api.example.com"
              />
            </div>
          </div>
          {value.length > 1 && (
            <Button type="button" variant="ghost" size="sm" onClick={() => remove(i)} className="text-xs h-7">
              {t("hostProfiles.removeMapping")}
            </Button>
          )}
        </div>
      ))}
      <Button type="button" variant="outline" size="sm" onClick={add} className="text-xs">
        {t("hostProfiles.addMapping")}
      </Button>
    </div>
  );
}
