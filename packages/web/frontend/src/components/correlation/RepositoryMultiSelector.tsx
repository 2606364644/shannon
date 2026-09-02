import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Search } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui/button";
import type { Repo } from "@/api/types";

interface Props {
  repos: Repo[];
  selected: string[];
  onChange: (next: string[]) => void;
  disabled?: boolean;
}

/** 自动拓扑的仓库多选器：搜索 + 固定高度滚动列表 + 全选/清空。
 * 仓库多时原 2-3 列网格整屏平铺（反馈「眼花缭乱」）——改单列列表 max-h 滚动，
 * 工具条按名过滤；「全选」只作用于当前过滤结果中的可选仓（ready/stale），
 * 不误选被过滤掉的仓。不可选行灰显 + 右侧 state 原文，解释为什么点不了。 */
export function RepositoryMultiSelector({ repos, selected, onChange, disabled }: Props) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const filtered = q ? repos.filter((r) => r.name.toLowerCase().includes(q)) : repos;
  const selectable = (r: Repo) => r.state === "ready" || r.state === "stale";
  const visibleSelectable = filtered.filter(selectable);
  const allVisibleSelected = visibleSelectable.length > 0
    && visibleSelectable.every((r) => selected.includes(r.name));

  const toggle = (name: string, on: boolean) =>
    onChange(on ? [...selected, name] : selected.filter((n) => n !== name));
  const selectAllVisible = () =>
    onChange([...new Set([...selected, ...visibleSelectable.map((r) => r.name)])]);

  return (
    <div data-testid="topology-repo-selector" className="overflow-hidden rounded-lg border border-border bg-card">
      {/* 工具条：搜索 + 已选计数 + 全选/清空 */}
      <div className="flex items-center gap-1.5 border-b border-border px-2.5 py-1.5">
        <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("scan.correlation.analysis.searchPlaceholder")}
          aria-label={t("scan.correlation.analysis.searchPlaceholder")}
          disabled={disabled}
          className="h-6 min-w-0 flex-1 bg-transparent text-xs outline-none placeholder:text-muted-foreground"
        />
        <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium tabular-nums text-muted-foreground">
          {t("scan.correlation.analysis.selectedCount", { count: selected.length })}
        </span>
        <Button
          type="button" variant="ghost" size="sm" className="h-6 px-2 text-[11px]"
          disabled={disabled || visibleSelectable.length === 0 || allVisibleSelected}
          onClick={selectAllVisible}
        >
          {t("scan.correlation.analysis.selectAll")}
        </Button>
        <Button
          type="button" variant="ghost" size="sm" className="h-6 px-2 text-[11px]"
          disabled={disabled || selected.length === 0}
          onClick={() => onChange([])}
        >
          {t("scan.correlation.analysis.clearAll")}
        </Button>
      </div>
      {/* 列表：固定高度滚动，不再把表单拉长 */}
      <div className="max-h-64 overflow-y-auto p-1">
        {filtered.map((repo) => {
          const id = `topology-repo-${repo.name.replace(/[^A-Za-z0-9_-]/g, "_")}`;
          const checked = selected.includes(repo.name);
          const ok = selectable(repo);
          return (
            <label
              key={repo.name}
              htmlFor={id}
              className={`flex h-8 items-center gap-2 rounded-md px-2 ${
                !ok ? "cursor-not-allowed opacity-60"
                  : checked ? "cursor-pointer bg-primary/5"
                  : disabled ? "cursor-default"
                  : "cursor-pointer hover:bg-muted/60"
              }`}
            >
              <Checkbox
                id={id}
                checked={checked}
                disabled={disabled || !ok}
                onCheckedChange={(value) => toggle(repo.name, value === true)}
              />
              <span className="min-w-0 flex-1 truncate font-mono text-xs">{repo.name}</span>
              {ok
                ? <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-green" aria-hidden />
                : <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{repo.state}</span>}
            </label>
          );
        })}
        {!filtered.length && (
          <p className="px-2 py-3 text-center text-xs text-muted-foreground">
            {repos.length ? t("scan.correlation.analysis.filterEmpty") : t("scan.correlation.analysis.noReadyRepos")}
          </p>
        )}
      </div>
    </div>
  );
}
