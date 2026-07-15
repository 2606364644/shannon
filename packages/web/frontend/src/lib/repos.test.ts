import { describe, it, expect } from "vitest";
import { filterRepos, groupRepos } from "./repos";
import type { Repo } from "@/api/types";

const REPOS: Repo[] = [
  {
    name: "frontend/admin",
    group: "frontend",
    source: { kind: "git", url: "https://gitlab.com/team-x/admin.git" },
    state: "ready",
  },
  {
    name: "frontend/my-app",
    group: "frontend",
    source: { kind: "git", url: "https://gitlab.com/team-y/myapp.git" },
    state: "ready",
  },
  {
    name: "plain-repo",
    group: null,
    source: { kind: "git", url: "https://github.com/ev/plain.git" },
    state: "ready",
  },
];

describe("filterRepos", () => {
  it("空查询（含纯空格）返回全部", () => {
    expect(filterRepos(REPOS, "")).toHaveLength(3);
    expect(filterRepos(REPOS, "   ")).toHaveLength(3);
  });

  it("按完整 name（含分组前缀）匹配", () => {
    expect(filterRepos(REPOS, "frontend/admin").map((x) => x.name)).toEqual([
      "frontend/admin",
    ]);
  });

  it("按 basename（name 最后一段）匹配", () => {
    expect(filterRepos(REPOS, "admin").map((x) => x.name)).toEqual([
      "frontend/admin",
    ]);
  });

  it("按 source.url 匹配", () => {
    expect(filterRepos(REPOS, "team-x").map((x) => x.name)).toEqual([
      "frontend/admin",
    ]);
  });

  it("大小写不敏感", () => {
    expect(filterRepos(REPOS, "ADMIN").map((x) => x.name)).toEqual([
      "frontend/admin",
    ]);
    expect(filterRepos(REPOS, "MY-APP").map((x) => x.name)).toEqual([
      "frontend/my-app",
    ]);
  });

  it("无匹配返回空数组", () => {
    expect(filterRepos(REPOS, "不存在的仓库")).toEqual([]);
  });
});

describe("groupRepos", () => {
  it("按 group 字段分组，null 归入未分组标签，保持插入顺序", () => {
    const groups = groupRepos(REPOS, "未分组");
    expect(groups.map((g) => g.name)).toEqual(["frontend", "未分组"]);
    expect(groups[0].repos.map((x) => x.name)).toEqual([
      "frontend/admin",
      "frontend/my-app",
    ]);
    expect(groups[1].repos.map((x) => x.name)).toEqual(["plain-repo"]);
  });
});
