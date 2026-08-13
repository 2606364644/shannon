// HOST 档案 新建/编辑 对话框。镜像 AuthProfileDialog.tsx 范式
// (open/onOpenChange/onSaved、busy、reset on close、<form onSubmit>)。
// 字段: 档案名 / MappingRows(IP / 域名) + 可选 source_url + 「从链接拉取」按钮调 parseHostProfile 预填 mappings。
// 提交按钮文案恒为 "保存"（区别于工具栏 "新建档案"，避免测试中 within 消歧）。
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { createHostProfile, updateHostProfile, parseHostProfile } from "@/api/hostProfiles";
import { apiErrorMessage } from "@/lib/apiError";
import type { HostProfile } from "@/api/types";
import { MappingRows, type MappingDraft } from "./host/MappingRows";

interface Props {
  ws: string;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onSaved: () => void;
  editing?: HostProfile | null;
}

export function HostProfileDialog({ ws, open, onOpenChange, onSaved, editing }: Props) {
  const { t } = useTranslation();
  const [name, setName] = useState(editing?.name ?? "");
  const [sourceUrl, setSourceUrl] = useState(editing?.source_url ?? "");
  const [drafts, setDrafts] = useState<MappingDraft[]>(() => {
    if (editing && editing.mappings?.length) {
      return editing.mappings.map((m) => ({ ip: m.ip, host: m.host }));
    }
    return [{ ip: "", host: "" }];
  });
  const [busy, setBusy] = useState(false);
  const [parsing, setParsing] = useState(false);
  // 系统档案只读防御：列表已隐藏 Edit，此处兜底——万一外部直传 system editing，禁提交，与后端 403 一致。
  const readOnly = editing?.scope === "system";

  function reset() {
    setName(""); setSourceUrl(""); setDrafts([{ ip: "", host: "" }]);
  }

  async function onParse() {
    const u = sourceUrl.trim();
    if (!u) {
      toast.error(t("hostProfiles.parseNeedUrl"));
      return;
    }
    setParsing(true);
    try {
      const r = await parseHostProfile(ws, u);
      if (r.mappings.length) {
        setDrafts(r.mappings.map((m) => ({ ip: m.ip, host: m.host })));
      }
      if (r.warnings.length) {
        toast.warning(r.warnings.join("\n"));
      }
      if (r.mappings.length === 0 && r.warnings.length === 0) {
        toast.info(t("hostProfiles.parseEmpty"));
      }
    } catch (e) {
      toast.error(apiErrorMessage(e, t("hostProfiles.parseFailed")));
    } finally {
      setParsing(false);
    }
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (readOnly) return;
    if (!name.trim() || drafts.some((d) => !d.ip.trim() || !d.host.trim())) {
      toast.error(t(editing ? "hostProfiles.saveFailed" : "hostProfiles.createFailed"));
      return;
    }
    setBusy(true);
    try {
      const mappings = drafts
        .filter((d) => d.ip.trim() && d.host.trim())
        .map((d) => ({ ip: d.ip.trim(), host: d.host.trim() }));
      const body: Partial<HostProfile> = {
        name: name.trim(),
        mappings,
        ...(sourceUrl.trim() ? { source_url: sourceUrl.trim() } : {}),
      };
      if (editing) await updateHostProfile(ws, editing.id, body);
      else await createHostProfile(ws, body);
      toast.success(t(editing ? "hostProfiles.saved" : "hostProfiles.created"));
      reset(); onSaved(); onOpenChange(false);
    } catch (e) {
      toast.error(apiErrorMessage(e, t(editing ? "hostProfiles.saveFailed" : "hostProfiles.createFailed")));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) reset(); onOpenChange(o); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{editing ? t("hostProfiles.edit") : t("hostProfiles.create")}</DialogTitle>
        </DialogHeader>
        <form onSubmit={onSubmit} className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="hp-name">{t("hostProfiles.name")}</Label>
            <Input id="hp-name" value={name} onChange={(e) => setName(e.target.value)} required />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="hp-source">{t("hostProfiles.sourceUrl")}</Label>
            <div className="flex gap-2">
              <Input
                id="hp-source"
                value={sourceUrl}
                onChange={(e) => setSourceUrl(e.target.value)}
                placeholder="https://example.com/hosts.txt"
                className="font-mono text-xs"
              />
              <Button type="button" variant="outline" size="sm" onClick={onParse} disabled={parsing} className="shrink-0">
                {parsing ? "…" : t("hostProfiles.parse")}
              </Button>
            </div>
            <p className="text-[11px] text-muted-foreground">{t("hostProfiles.sourceUrlHint")}</p>
          </div>
          <div className="space-y-1.5">
            <Label>{t("hostProfiles.mappings")}</Label>
            <MappingRows value={drafts} onChange={setDrafts} />
          </div>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button>
            {!readOnly && (
              <Button type="submit" disabled={busy}>{busy ? "…" : t("hostProfiles.save")}</Button>
            )}
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
