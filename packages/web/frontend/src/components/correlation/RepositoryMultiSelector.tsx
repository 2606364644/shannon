import { useTranslation } from "react-i18next";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import type { Repo } from "@/api/types";

interface Props {
  repos: Repo[];
  selected: string[];
  onChange: (next: string[]) => void;
  disabled?: boolean;
}

export function RepositoryMultiSelector({ repos, selected, onChange, disabled }: Props) {
  const { t } = useTranslation();
  return (
    <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3" data-testid="topology-repo-selector">
      {repos.map((repo) => {
        const id = `topology-repo-${repo.name.replace(/[^A-Za-z0-9_-]/g, "_")}`;
        const checked = selected.includes(repo.name);
        return (
          <div key={repo.name} className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2">
            <Checkbox
              id={id}
              checked={checked}
              disabled={disabled || (repo.state !== "ready" && repo.state !== "stale")}
              onCheckedChange={(value) => onChange(value
                ? [...selected, repo.name]
                : selected.filter((name) => name !== repo.name))}
            />
            <Label htmlFor={id} className="min-w-0 flex-1 truncate font-mono text-xs">
              {repo.name}
            </Label>
          </div>
        );
      })}
      {!repos.length && (
        <p className="text-xs text-muted-foreground">{t("scan.correlation.analysis.noReadyRepos")}</p>
      )}
    </div>
  );
}
