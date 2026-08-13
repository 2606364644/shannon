import { useMemo, useState } from "react";
import { Check, ChevronsUpDown } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { filterRepos, groupRepos } from "@/lib/repos";
import type { Repo } from "@/api/types";

export interface RepoComboboxProps {
  repos: Repo[];
  value: string | null;
  onChange: (name: string) => void;
  /** 触发器未选中时的占位文案 */
  placeholder?: string;
  /** 搜索输入框占位文案 */
  searchPlaceholder?: string;
  /** 无匹配仓库时的空态文案 */
  emptyText?: string;
  /** group 为空时的分组标签 */
  ungroupedLabel?: string;
  /** 关联仓库（按路径关联、共享/只读）的标记文案 */
  linkedLabel?: string;
}

export function RepoCombobox({
  repos,
  value,
  onChange,
  placeholder = "Select repo",
  searchPlaceholder = "Search...",
  emptyText = "No match",
  ungroupedLabel = "Ungrouped",
  linkedLabel = "Linked",
}: RepoComboboxProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  const selected = repos.find((r) => r.name === value);
  const selectedLabel = selected
    ? (selected.name.split("/").pop() ?? selected.name)
    : placeholder;

  const groups = useMemo(
    () => groupRepos(filterRepos(repos, query), ungroupedLabel),
    [repos, query, ungroupedLabel],
  );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="w-full justify-between font-normal font-mono text-xs"
        >
          <span className={cn(!selected && "text-muted-foreground")}>
            {selectedLabel}
          </span>
          <ChevronsUpDown className="h-4 w-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className="w-[var(--radix-popover-trigger-width)] p-0"
        align="start"
      >
        <Command shouldFilter={false}>
          <CommandInput
            placeholder={searchPlaceholder}
            onValueChange={setQuery}
          />
          <CommandList>
            <CommandEmpty>{emptyText}</CommandEmpty>
            {groups.map((g) => (
              <CommandGroup key={g.name} heading={g.name}>
                {g.repos.map((r) => {
                  const base = r.name.split("/").pop() ?? r.name;
                  const isSel = r.name === value;
                  return (
                    <CommandItem
                      key={r.name}
                      value={r.name}
                      onSelect={() => {
                        onChange(r.name);
                        setOpen(false);
                      }}
                      className="gap-2"
                    >
                      <Check
                        className={cn(
                          "h-4 w-4 shrink-0",
                          isSel ? "opacity-100" : "opacity-0",
                        )}
                      />
                      <span>{base}</span>
                      {r.linked ? (
                        <span className="ml-auto shrink-0 rounded border border-cyan/40 px-1 text-xs text-cyan">
                          {linkedLabel}
                        </span>
                      ) : r.source?.url ? (
                        <span className="ml-auto truncate text-xs text-muted-foreground">
                          {r.source.url}
                        </span>
                      ) : null}
                    </CommandItem>
                  );
                })}
              </CommandGroup>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
