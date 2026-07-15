import type { Repo } from "@/api/types";

/**
 * 按查询词筛选仓库：匹配完整 name / basename（最后一段）/ source.url，大小写不敏感。
 * 空查询（含纯空格）返回全部。
 */
export function filterRepos(repos: Repo[], query: string): Repo[] {
  const q = query.trim().toLowerCase();
  if (!q) return repos;
  return repos.filter((r) => {
    const name = r.name.toLowerCase();
    const base = (r.name.split("/").pop() ?? r.name).toLowerCase();
    const url = (r.source?.url ?? "").toLowerCase();
    return name.includes(q) || base.includes(q) || url.includes(q);
  });
}

/** 按 group 字段分组；group 为 null/undefined 归入 ungroupedLabel。保持插入顺序。 */
export function groupRepos(
  repos: Repo[],
  ungroupedLabel: string,
): { name: string; repos: Repo[] }[] {
  const map = new Map<string, Repo[]>();
  for (const r of repos) {
    const g = r.group ?? ungroupedLabel;
    let arr = map.get(g);
    if (!arr) {
      arr = [];
      map.set(g, arr);
    }
    arr.push(r);
  }
  return Array.from(map, ([name, rs]) => ({ name, repos: rs }));
}
