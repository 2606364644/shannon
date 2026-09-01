import { describe, expect, it } from "vitest";
import { CorrFormState, formToYaml, yamlToForm, validateForm, CorrYamlError, effectiveCorrRoles } from "./correlation-yaml";

const base: CorrFormState = {
  repos: [
    { repo: "frontend", role: "entrypoint", protocol: "grpc", reuseScanId: null },
    { repo: "order-svc", role: "backend", protocol: "grpc", reuseScanId: "frontend-20260801-120000" },
  ],
  relations: [{ from: "frontend", to: "order-svc", protocol: "grpc" }],
};

describe("formToYaml / yamlToForm roundtrip", () => {
  it("生成含 workspace 复用与 path 现扫", () => {
    const y = formToYaml(base);
    expect(y).toContain("frontend:");
    expect(y).toContain("path: frontend");
    expect(y).toContain("workspace: frontend-20260801-120000");
    expect(y).toContain("role: entrypoint");
  });
  it("roundtrip 无损（含多边自由拓扑）", () => {
    const s: CorrFormState = {
      repos: base.repos.concat([{ repo: "pay-svc", role: "backend", protocol: "http", reuseScanId: null }]),
      relations: [
        { from: "frontend", to: "order-svc", protocol: "grpc" },
        { from: "order-svc", to: "pay-svc", protocol: "http" },   // 后端互调
      ],
    };
    expect(yamlToForm(formToYaml(s))).toEqual(s);
  });
  it("坏 YAML throw CorrYamlError 带 issues", () => {
    expect(() => yamlToForm("repos: [")).toThrow(CorrYamlError);
    expect(() => yamlToForm("relations:\n  - {from: a, to: ghost}\nrepos:\n  a: {path: a, role: entrypoint}"))
      .toThrow(/ghost/);
  });
  it("validateForm：缺 entrypoint / 重复 repo 报错", () => {
    expect(validateForm({ repos: [{ repo: "a", role: "backend", protocol: "grpc", reuseScanId: null }], relations: [] }))
      .toEqual([expect.stringContaining("entrypoint")]);
    const dup = { repos: [base.repos[0], { ...base.repos[0] }], relations: [] };
    expect(validateForm(dup).length).toBeGreaterThan(0);
  });
});

describe("validateForm：relations 引用存在", () => {
  it("悬空引用报错（含服务名）、合法引用不报", () => {
    const kind = (m: string) => m.includes("relations 引用未声明服务");
    const dangling: CorrFormState = {
      repos: base.repos,
      relations: [{ from: "frontend", to: "ghost", protocol: "grpc" }],
    };
    expect(validateForm(dangling).filter(kind)).toEqual(["relations 引用未声明服务: ghost"]);
    const valid: CorrFormState = { repos: base.repos, relations: base.relations };
    expect(validateForm(valid).some(kind)).toBe(false);
  });
});

describe("validateForm：复用模式未选扫描（reuseScanId === \"\" 哨兵）", () => {
  it("未选报「仓库 {{name}} 缺少来源」；已选/现扫模式不报", () => {
    // D3 setCardSource：切到复用但未选 scan → reuseScanId === ""；formToYaml 视 ""
    // 为 falsy 会静默落成 path: 现扫——validateForm 须拦下（final-fix ③）。
    const unselected: CorrFormState = {
      repos: [
        { repo: "frontend", role: "entrypoint", protocol: "grpc", reuseScanId: null },
        { repo: "order-svc", role: "backend", protocol: "grpc", reuseScanId: "" },
      ],
      relations: [{ from: "frontend", to: "order-svc", protocol: "grpc" }],
    };
    expect(validateForm(unselected)).toEqual(["仓库 order-svc 缺少来源"]);
    // 文案形状须能被 corr-issues-i18n 的 repoNoSource 映射消费（仓库 <name> 缺少来源）
    const msg = validateForm(unselected)[0];
    expect(msg.startsWith("仓库 ") && msg.endsWith(" 缺少来源")).toBe(true);
    const selected: CorrFormState = {
      repos: [
        unselected.repos[0],
        { ...unselected.repos[1], reuseScanId: "order-svc-20260801-120000" },
      ],
      relations: unselected.relations,
    };
    expect(validateForm(selected)).toEqual([]);
  });
});

describe("multi-role correlation YAML", () => {
  it("serializes legacy role and roles for dual capability", () => {
    const s: CorrFormState = {
      repos: [
        { repo: "gateway", role: "entrypoint", roles: ["entrypoint", "backend"], protocol: "grpc", reuseScanId: null },
        { repo: "order", role: "backend", protocol: "grpc", reuseScanId: null },
      ],
      relations: [
        { from: "gateway", to: "order", protocol: "grpc" },
        { from: "order", to: "gateway", protocol: "http" },
      ],
    };
    const text = formToYaml(s);
    expect(text).toContain("role: entrypoint");
    expect(text).toContain("roles:\n      - entrypoint\n      - backend");
    expect(yamlToForm(text)).toEqual(s);
  });

  it("keeps legacy single-role YAML unchanged and rejects malformed roles/protocol/self-loop", () => {
    const legacy = yamlToForm("repos:\n  gateway: {path: gateway, role: entrypoint}\n  order: {path: order, role: backend}\nrelations:\n  - {from: gateway, to: order, protocol: grpc}\n  - {from: gateway, to: order, protocol: http}\n");
    expect(legacy.repos[0].roles).toBeUndefined();
    expect(effectiveCorrRoles(legacy.repos[0])).toEqual(["entrypoint"]);
    expect(() => yamlToForm("repos:\n  a: {role: entrypoint, roles: [database]}\nrelations: []")).toThrow(/roles/);
    expect(() => yamlToForm("repos:\n  a: {role: entrypoint}\nrelations:\n  - {from: a, to: a, protocol: grpc}")).toThrow(/self/);
    expect(() => yamlToForm("repos:\n  a: {role: entrypoint}\nrelations:\n  - {from: a, to: a, protocol: thrift}")).toThrow(/protocol/);
  });
});

it("treats a relation with omitted protocol as legacy grpc and rejects only explicit invalid values", () => {
  const state = yamlToForm(`repos:
  gateway: {path: gateway, role: entrypoint}
  order: {path: order, role: backend}
relations:
  - {from: gateway, to: order}
`);
  expect(state.relations).toEqual([{ from: "gateway", to: "order", protocol: "grpc" }]);
});
