import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { browseFs } from "@/api/client";
import type { FsEntry } from "@/api/types";
import { ApiError } from "@/api/client";
import { cn } from "@/lib/utils";

const RECENT_KEY = "shannon-fs-recent";

export interface FileSystemPickerProps {
  value: string;
  onChange: (abs: string) => void;
  title?: string;
  triggerLabel?: string;
}

function loadRecent(): string[] {
  try {
    return JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]");
  } catch {
    return [];
  }
}

function pushRecent(path: string): string[] {
  const cur = loadRecent().filter((p) => p !== path);
  const next = [path, ...cur].slice(0, 5);
  localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  return next;
}

export function FileSystemPicker({ value, onChange, title, triggerLabel }: FileSystemPickerProps) {
  const { t } = useTranslation();
  const resolvedTitle = title ?? t("fileSystemPicker.titleDefault");
  const resolvedTriggerLabel = triggerLabel ?? t("scan.fields.browse");
  const [open, setOpen] = useState(false);
  const [currentPath, setCurrentPath] = useState(value || "/");
  const [entries, setEntries] = useState<FsEntry[]>([]);
  const [, setParent] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [manualPath, setManualPath] = useState(value || "/");
  const [error, setError] = useState<string | null>(null);
  const [recent, setRecent] = useState<string[]>([]);

  async function load(path: string) {
    setError(null);
    try {
      const r = await browseFs(path);
      setCurrentPath(r.path);
      setEntries(r.entries);
      setParent(r.parent);
      setManualPath(r.path);
      setSelected(null);
    } catch (e) {
      if (e instanceof ApiError) {
        const detail = (e.body as { detail?: string })?.detail;
        setError(detail ?? t("fileSystemPicker.errorStatus", { status: e.status }));
      } else {
        setError(t("fileSystemPicker.requestFailed"));
      }
    }
  }

  useEffect(() => {
    if (open) {
      setRecent(loadRecent());
      load(value || "/");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function confirm() {
    if (!selected) return;
    onChange(selected);
    setRecent(pushRecent(selected));
    setOpen(false);
  }

  const selectedIsDir = entries.find((e) => `${currentPath}/${e.name}` === selected)?.type === "dir";

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>{resolvedTriggerLabel}</Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{resolvedTitle}</DialogTitle>
          </DialogHeader>

          {/* 面包屑 + home + 刷新 */}
          <div className="flex items-center gap-2 text-sm">
            <Button variant="ghost" size="sm" onClick={() => load("~")}>🏠</Button>
            <span className="font-mono text-muted-foreground truncate">{currentPath}</span>
            <Button variant="ghost" size="icon" aria-label={t("fileSystemPicker.refreshAria")} onClick={() => load(currentPath)}>↻</Button>
          </div>

          {/* 最近书签 */}
          {recent.length > 0 && (
            <div className="flex flex-wrap items-center gap-1 text-xs">
              <span className="text-muted-foreground">{t("fileSystemPicker.recent")}</span>
              {recent.map((p) => (
                <button key={p} className="rounded border border-border px-2 py-0.5 hover:bg-accent" onClick={() => load(p)}>
                  {p.split("/").pop() || p}
                </button>
              ))}
            </div>
          )}

          {/* 列表区 */}
          <div className="min-h-[200px] max-h-[320px] overflow-auto rounded-md border border-border bg-background">
            {error ? (
              <div className="p-3 text-sm text-red">⚠ {error}</div>
            ) : entries.length === 0 ? (
              <div className="p-3 text-sm text-muted-foreground">{t("fileSystemPicker.emptyDir")}</div>
            ) : (
              <ul>
                {entries.map((e) => {
                  const full = `${currentPath}/${e.name}`;
                  const isDir = e.type === "dir";
                  return (
                    <li
                      key={e.name}
                      data-selected={selected === full}
                      onDoubleClick={() => isDir && load(full)}
                      onClick={() => setSelected(full)}
                      className={cn(
                        "flex cursor-pointer items-center gap-2 px-3 py-1 text-sm",
                        isDir ? "text-foreground" : "text-muted-foreground",
                        selected === full && "bg-accent",
                      )}
                    >
                      <span>{isDir ? "📁" : "📄"}</span>
                      <span className="font-mono">{e.name}</span>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {/* 路径输入 */}
          <Input
            value={manualPath}
            onChange={(e) => setManualPath(e.target.value)}
            onBlur={() => manualPath !== currentPath && load(manualPath)}
            onKeyDown={(e) => { if (e.key === "Enter") load(manualPath); }}
            className="font-mono text-sm"
          />

          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>{t("common.cancel")}</Button>
            <Button onClick={confirm} disabled={!selectedIsDir}>{t("fileSystemPicker.selectThisDir")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
