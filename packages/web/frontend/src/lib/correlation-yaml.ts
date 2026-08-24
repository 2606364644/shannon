import yaml from "js-yaml";

export type CorrRole = "entrypoint" | "backend";
export type CorrProtocol = "grpc" | "http" | "graphql";

export interface CorrRepoDraft { repo: string; role: CorrRole; protocol: CorrProtocol; reuseScanId: string | null; protoRoots?: string[]; }
export interface CorrRelation { from: string; to: string; protocol: CorrProtocol }
export interface CorrFormState { repos: CorrRepoDraft[]; relations: CorrRelation[] }

export class CorrYamlError extends Error {
  constructor(public issues: string[]) { super(issues.join("; ")); }
}

export function formToYaml(s: CorrFormState): string {
  const repos: Record<string, unknown> = {};
  for (const r of s.repos) {
    const spec: Record<string, unknown> = {
      path: r.repo,               // web 语义：工作区仓库名（后端 _resolve_repo_dir 解析）
      role: r.role,
    };
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
    repos.push({
      repo: name,
      role: raw?.role === "entrypoint" ? "entrypoint" : "backend",
      protocol: ["grpc", "http", "graphql"].includes(raw?.protocol) ? raw.protocol : incoming.get(name) ?? "grpc",
      reuseScanId: typeof reuse === "string" ? reuse : null,
      protoRoots: Array.isArray(raw?.proto_roots) ? raw.proto_roots : undefined,
    });
  }
  const names = new Set(repos.map((r) => r.repo));
  const relations: CorrRelation[] = [];
  for (const e of doc.relations ?? []) {
    for (const side of ["from", "to"] as const) {
      if (!names.has(e?.[side])) issues.push(`relations 引用未声明服务: ${e?.[side]}`);
    }
    if (names.has(e?.from) && names.has(e?.to)) {
      relations.push({ from: e.from, to: e.to,
        protocol: ["grpc", "http", "graphql"].includes(e?.protocol) ? e.protocol : "grpc" });
    }
  }
  if (!repos.some((r) => r.role === "entrypoint")) issues.push("至少需要一个 entrypoint 仓库");
  if (issues.length) throw new CorrYamlError(issues);
  return { repos, relations };
}

export function validateForm(s: CorrFormState): string[] {
  const issues: string[] = [];
  if (!s.repos.some((r) => r.role === "entrypoint")) issues.push("至少需要一个 entrypoint 仓库");
  const seen = new Set<string>();
  for (const r of s.repos) {
    if (!r.repo.trim()) issues.push("存在未命名的仓库卡片");
    if (seen.has(r.repo)) issues.push(`仓库重复: ${r.repo}`);
    seen.add(r.repo);
    if (r.role === "backend" && r.reuseScanId === null && !r.repo.trim()) issues.push(`仓库 ${r.repo} 缺少来源`);
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
