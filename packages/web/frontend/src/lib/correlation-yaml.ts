import yaml from "js-yaml";

export type CorrRole = "entrypoint" | "backend";
export type CorrProtocol = "grpc" | "http" | "graphql";

export interface CorrRepoDraft { repo: string; role: CorrRole; roles?: CorrRole[]; protocol: CorrProtocol; reuseScanId: string | null; protoRoots?: string[]; }
export interface CorrRelation { from: string; to: string; protocol: CorrProtocol }
export interface CorrFormState { repos: CorrRepoDraft[]; relations: CorrRelation[] }

export function effectiveCorrRoles(repo: Pick<CorrRepoDraft, "role" | "roles">): CorrRole[] {
  const roles = repo.roles?.length ? repo.roles : [repo.role];
  const valid = (["entrypoint", "backend"] as CorrRole[]).filter((role) => roles.includes(role));
  return valid.length ? valid : ["backend"];
}

export class CorrYamlError extends Error {
  constructor(public issues: string[]) { super(issues.join("; ")); }
}

export function formToYaml(s: CorrFormState): string {
  const repos: Record<string, unknown> = {};
  for (const r of s.repos) {
    const roles = effectiveCorrRoles(r);
    const primary = roles.includes(r.role) ? r.role : roles[0];
    const spec: Record<string, unknown> = {
      path: r.repo,               // web 语义：工作区仓库名（后端 _resolve_repo_dir 解析）
      role: primary,
    };
    if (roles.length > 1) spec.roles = roles;
    if (r.reuseScanId) { delete spec.path; spec.workspace = r.reuseScanId; }
    if (r.protoRoots?.length) spec.proto_roots = r.protoRoots;
    repos[r.repo] = spec;
  }
  return yaml.dump({
    repos,
    relations: s.relations.map((e) => ({ from: e.from, to: e.to, protocol: e.protocol })),
  }, { noRefs: true, lineWidth: 120 });
}

export function yamlToForm(y: string): CorrFormState {
  const issues: string[] = [];
  let doc: any;
  try { doc = yaml.load(y); } catch (e: any) { throw new CorrYamlError([`YAML 语法错误: ${e.message}`]); }
  if (!doc || typeof doc !== "object" || !doc.repos) throw new CorrYamlError(["缺少 repos 段"]);
  // 协议重建（brief 修正）：formToYaml 不写 per-repo protocol（后端 RepoSpec extra="forbid" 无此字段，
  // 边协议只存 relations），故按「指向该仓的首条 relation 边的 protocol」重建 CorrRepoDraft.protocol
  // （接口注释语义：entrypoint→该仓边的协议；无入边/显式合法 protocol 时回落 grpc/显式值）。
  const incoming = new Map<string, string>();
  for (const e of (doc.relations ?? []) as any[]) {
    const proto = ["grpc", "http", "graphql"].includes(e?.protocol) ? e.protocol : "grpc";
    if (e?.to && !incoming.has(e.to)) incoming.set(e.to, proto);
  }
  const repos: CorrRepoDraft[] = [];
  for (const [name, raw] of Object.entries<any>(doc.repos)) {
    const reuse = raw?.workspace ?? null;
    const role: CorrRole = raw?.role === "entrypoint" ? "entrypoint" : "backend";
    const rawRoles = Array.isArray(raw?.roles) ? raw.roles : undefined;
    if (rawRoles && (!rawRoles.every((value: unknown) => value === "entrypoint" || value === "backend") || !rawRoles.length))
      issues.push(`repos.${name} roles 必须是 entrypoint/backend 的非空集合`);
    const roles = rawRoles?.length
      ? (["entrypoint", "backend"] as CorrRole[]).filter((value) => rawRoles.includes(value))
      : [role];
    if (roles.length && !roles.includes(role)) issues.push(`repos.${name} legacy role 必须包含在 roles 中`);
    const repo: CorrRepoDraft = {
      repo: name,
      role,
      protocol: ["grpc", "http", "graphql"].includes(raw?.protocol)
        ? raw.protocol
        : roles.includes("entrypoint")
          ? (doc.relations ?? []).find((e: any) => e?.from === name && ["grpc", "http", "graphql"].includes(e.protocol))?.protocol ?? incoming.get(name) ?? "grpc"
          : incoming.get(name) ?? "grpc",
      reuseScanId: typeof reuse === "string" ? reuse : null,
    };
    if (rawRoles?.length) repo.roles = roles;
    if (Array.isArray(raw?.proto_roots)) repo.protoRoots = raw.proto_roots;
    repos.push(repo);
  }
  const names = new Set(repos.map((r) => r.repo));
  const relations: CorrRelation[] = [];
  const identities = new Set<string>();
  for (const e of doc.relations ?? []) {
    for (const side of ["from", "to"] as const) {
      if (!names.has(e?.[side])) issues.push(`relations 引用未声明服务: ${e?.[side]}`);
    }
    if (e?.from === e?.to && names.has(e?.from)) issues.push(`relations 不允许 self-loop: ${e?.from}`);
    if (e?.protocol !== undefined && !["grpc", "http", "graphql"].includes(e.protocol))
      issues.push(`relations protocol 必须是 grpc/http/graphql: ${e.protocol}`);
    const protocol = e?.protocol === undefined ? "grpc" : e.protocol;
    if (names.has(e?.from) && names.has(e?.to) && ["grpc", "http", "graphql"].includes(protocol)) {
      const identity = `${e.from}\n${e.to}\n${protocol}`;
      if (identities.has(identity)) issues.push(`relations 重复边: ${e.from} -> ${e.to} (${e.protocol})`);
      identities.add(identity);
      relations.push({ from: e.from, to: e.to, protocol });
    }
  }
  if (!repos.some((r) => effectiveCorrRoles(r).includes("entrypoint"))) issues.push("至少需要一个 entrypoint 仓库");
  if (issues.length) throw new CorrYamlError(issues);
  return { repos, relations };
}

export function validateForm(s: CorrFormState): string[] {
  const issues: string[] = [];
  if (!s.repos.some((r) => effectiveCorrRoles(r).includes("entrypoint"))) issues.push("至少需要一个 entrypoint 仓库");
  const seen = new Set<string>();
  for (const r of s.repos) {
    if (!r.repo.trim()) issues.push("存在未命名的仓库卡片");
    if (seen.has(r.repo)) issues.push(`仓库重复: ${r.repo}`);
    seen.add(r.repo);
    if (!effectiveCorrRoles(r).includes("entrypoint") && r.reuseScanId === null && !r.repo.trim()) issues.push(`仓库 ${r.repo} 缺少来源`);
    // 复用模式未选扫描（D3 setCardSource 以 reuseScanId === "" 作哨兵）：formToYaml
    // 视 "" 为 falsy 会静默落成 path: 现扫——与用户「复用」意图相悖，按「缺少来源」
    // 拦下。文案形状与 corr-issues-i18n 的 repoNoSource（仓库 {{name}} 缺少来源）对齐。
    if (r.reuseScanId === "") issues.push(`仓库 ${r.repo} 缺少来源`);
  }
  // relations 引用存在（接口契约第三项）：每条边的 from/to 必须指向已声明的仓库名
  const names = new Set(s.repos.map((r) => r.repo));
  for (const e of s.relations) {
    for (const side of ["from", "to"] as const) {
      if (!names.has(e[side])) issues.push(`relations 引用未声明服务: ${e[side]}`);
    }
  }
  return issues;
}
