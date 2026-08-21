import { useState } from "react";
import { useTranslation } from "react-i18next";
import useSWR from "swr";
import { Check, ChevronsUpDown } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { listBranches } from "@/api/client";
import { cn } from "@/lib/utils";

export interface BranchComboboxProps {
  ws: string;
  /** 仓库名（可为 group/repo） */
  repo: string;
  /** 当前分支（repo.source.branch）；null 显示 "-" 占位 */
  value: string | null;
  /** 选中 ≠ value 的分支时回调（同分支 no-op 不回调） */
  onSwitch: (branch: string) => void;
}

/**
 * 仓库列表「分支」列的行内可搜索下拉（spec 2026-08-21 §3）。
 * 与 RepoCombobox 同模式（Popover + Command + shouldFilter={false} 自管过滤），
 * 不做代码级泛化——分支是纯字符串列表（无分组/徽章），泛化会动扫描页在用组件。
 *
 *  - 数据 lazy：挂载/聚焦/重连都不拉，点开触发器手动 mutate（列表页 N 行零额外请求）
 *  - 手输兜底：输入不在枚举列表时追加「使用 <输入>」项（枚举失败/离线/远端新分支）
 */
export function BranchCombobox({ ws, repo, value, onSwitch }: BranchComboboxProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const { data, error, isValidating, mutate } = useSWR(
    ["repo-branches", ws, repo],
    ([, w, r]: [string, string, string]) => listBranches(w, r),
    { revalidateOnMount: false, revalidateIfStale: false, revalidateOnFocus: false, revalidateOnReconnect: false },
  );

  const branches = data?.branches ?? [];
  const q = query.trim();
  const filtered = branches.filter((b) => b.toLowerCase().includes(q.toLowerCase()));
  // 兜底判定用完整列表（非 filtered）：输入恰为某分支时点列表项等价，不重复造项
  const showFallback = q !== "" && !branches.includes(q);

  function handleOpenChange(o: boolean) {
    setOpen(o);
    if (o) void mutate(); // 点开才拉/刷新远端分支
  }

  function pick(branch: string) {
    setOpen(false);
    setQuery("");
    if (branch !== value) onSwitch(branch);
  }

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={t("repoDetail.switchAria")}
          aria-expanded={open}
          className="group/bc inline-flex max-w-full items-center gap-0.5 font-mono text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
          <span className="truncate">{value ?? "-"}</span>
          <ChevronsUpDown className="size-3 shrink-0 opacity-0 transition-opacity group-hover/bc:opacity-60" />
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-56 p-0" align="start">
        <Command shouldFilter={false}>
          <CommandInput placeholder={t("repoDetail.branchSearch")} onValueChange={setQuery} />
          <CommandList>
            {error ? (
              <div className="px-3 py-2 text-xs text-muted-foreground">
                {t("repoDetail.branchLoadFailed")}
              </div>
            ) : !isValidating && branches.length === 0 ? (
              <CommandEmpty>{t("repoDetail.branchEmpty")}</CommandEmpty>
            ) : isValidating && branches.length === 0 ? (
              <div className="px-3 py-2 text-xs text-muted-foreground">
                {t("repoDetail.branchLoading")}
              </div>
            ) : null}
            {filtered.map((b) => (
              <CommandItem
                key={b}
                value={b}
                onSelect={() => pick(b)}
                aria-current={b === value ? "true" : undefined}
                className="gap-2"
              >
                <Check
                  className={cn("size-4 shrink-0", b === value ? "opacity-100" : "opacity-0")}
                />
                <span className="truncate font-mono text-xs">{b}</span>
              </CommandItem>
            ))}
            {showFallback && (
              <CommandItem value={q} onSelect={() => pick(q)} className="gap-2">
                <Check className="size-4 shrink-0 opacity-0" />
                <span className="truncate font-mono text-xs">{q}</span>
                <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                  {t("repoDetail.useBranch")}
                </span>
              </CommandItem>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
