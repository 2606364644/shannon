// D3: 跨仓关联表单组件——repo 卡片增删/角色/来源（重新扫 | 复用历史）+ relations
// 摘要 + gateway/auth/HOST 块 + YamlPanel 接线。Harness 镜像 ScanNewPage 的单向数据流
// （表单路径 yaml=formToYaml(state) 派生、YAML 编辑路径仅校验、apply 显式回填），
// 风格对齐 ScanFormFields.test.tsx：msw + MemoryRouter + i18n zh + fireEvent。
import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach } from "vitest";
import { useState } from "react";
import { render, screen, fireEvent, waitFor, cleanup, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import i18n from "@/i18n";
import { CorrelationFormFields } from "./CorrelationFormFields";
import {
  formToYaml, yamlToForm, CorrYamlError, type CorrFormState,
} from "@/lib/correlation-yaml";
import { DEFAULT_AUTH, DEFAULT_HOST, type AuthFormState, type HostFormState } from "@/pages/ScanNewPage";
import type { Workspace } from "@/api/types";

const WS_LIST: Workspace[] = [
  { name: "ws1", scan_type: "correlation", status: "completed", created_at: 0 },
];

const REPOS_FIXTURE = [
  { name: "frontend", state: "ready", source: { kind: "git", url: "https://gitlab.example/frontend.git" } },
  { name: "order-svc", state: "ready", source: { kind: "git", url: "https://gitlab.example/order-svc.git" } },
];

// 复用候选 fixture：order-svc 一条白盒（应命中）+ frontend 一条白盒（应被 repo 过滤掉）。
const WB_SCANS = [
  {
    scan_id: "20260801-120000", workflow_id: "ws1-order-20260801-120000", scan_type: "whitebox",
    repo: "order-svc", status: "completed", created_at: 1722400000, vuln_count: 1, is_running: false,
  },
  {
    scan_id: "20260801-999999", workflow_id: "ws1-front-20260801-999999", scan_type: "whitebox",
    repo: "frontend", status: "completed", created_at: 1722400100, vuln_count: 0, is_running: false,
  },
];

const server = setupServer(
  http.get("/api/workspaces/:ws/repos", () => HttpResponse.json(REPOS_FIXTURE)),
  http.get("/api/workspaces/:ws/scans", () => HttpResponse.json(WB_SCANS)),
  http.get("/api/workspaces/:ws/auth-profiles", () => HttpResponse.json([])),
  http.get("/api/workspaces/:ws/host-profiles", () => HttpResponse.json([])),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => { i18n.changeLanguage("zh"); });
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

/** 复刻 ScanNewPage 的单向数据流包装：表单交互 → onState 重生成 yaml；YAML 编辑 →
 *  仅校验（CorrYamlError + D1 已知限制的裸 TypeError 都纳入 yamlErr）；apply 显式回填。 */
function Harness() {
  const [state, setState] = useState<CorrFormState>({ repos: [], relations: [] });
  const [yamlText, setYamlText] = useState(() => formToYaml({ repos: [], relations: [] }));
  const [yamlError, setYamlError] = useState<CorrYamlError | null>(null);
  const [auth, setAuthState] = useState<AuthFormState>(DEFAULT_AUTH);
  const [host, setHostState] = useState<HostFormState>(DEFAULT_HOST);
  const updateCorr = (s: CorrFormState) => {
    setState(s);
    setYamlText(formToYaml(s));
    setYamlError(null);
  };
  const onYaml = (y: string) => {
    setYamlText(y);
    try { yamlToForm(y); setYamlError(null); } catch (e) {
      setYamlError(e instanceof CorrYamlError ? e : new CorrYamlError([String(e)]));
    }
  };
  const applyYaml = () => {
    try { updateCorr(yamlToForm(yamlText)); } catch { /* 不可达：有错时 apply disabled */ }
  };
  return (
    <MemoryRouter>
      <CorrelationFormFields
        state={state}
        onState={updateCorr}
        yaml={yamlText}
        onYaml={onYaml}
        yamlError={yamlError}
        onApplyYaml={applyYaml}
        workspace="ws1"
        wsList={WS_LIST}
        onWorkspaceChange={() => {}}
        wsLoading={false}
        gatewayUrl=""
        onGatewayUrl={() => {}}
        auth={auth}
        setAuth={(p) => setAuthState((a) => ({ ...a, ...p }))}
        host={host}
        setHost={(p) => setHostState((h) => ({ ...h, ...p }))}
      />
    </MemoryRouter>
  );
}

// RepoCombobox 触发器（未选中显 placeholder「选择仓库」）——按卡片 scope 取。
function openRepoPicker(card: HTMLElement) {
  fireEvent.click(within(card).getByText("选择仓库"));
}

async function pickRepo(name: string) {
  fireEvent.click(await screen.findByText(name));
}

function openYamlPanel() {
  fireEvent.click(screen.getByRole("button", { name: /YAML 配置/ }));
  return screen.findByLabelText("YAML 编辑器");
}

describe("CorrelationFormFields", () => {
  it("添加两个仓库 + 角色默认第一个 entrypoint → 生成星型 YAML", async () => {
    render(<Harness />);
    // 无卡片 → 添加两次（第一张默认 entrypoint，第二张默认 backend）
    fireEvent.click(screen.getByRole("button", { name: "+ 添加仓库" }));
    fireEvent.click(screen.getByRole("button", { name: "+ 添加仓库" }));
    const cards = screen.getAllByTestId("corr-repo-row");
    expect(cards).toHaveLength(2);
    expect(within(cards[0]).getByRole("button", { name: "入口" })).toHaveAttribute("aria-pressed", "true");
    expect(within(cards[1]).getByRole("button", { name: "后端" })).toHaveAttribute("aria-pressed", "true");
    // 分别选仓库（frontend 入口 / order-svc 后端）
    openRepoPicker(cards[0]);
    await pickRepo("frontend");
    openRepoPicker(cards[1]);
    await pickRepo("order-svc");
    // 星型边自动补齐（entrypoint → backend）
    await waitFor(() => expect(screen.getByText(/frontend → order-svc/)).toBeInTheDocument());
    // YAML 派生：展开面板读 textarea 值
    const editor = (await openYamlPanel()) as HTMLTextAreaElement;
    await waitFor(() => expect(editor.value).toContain("role: entrypoint"));
    expect(editor.value).toContain("frontend:");
    expect(editor.value).toContain("order-svc:");
    expect(editor.value).toContain("from: frontend");
    expect(editor.value).toContain("to: order-svc");
  });

  it("复用模式选历史扫描 → YAML 含 workspace 字段（候选按 repo 过滤）", async () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "+ 添加仓库" }));
    const card = screen.getByTestId("corr-repo-row");
    openRepoPicker(card);
    await pickRepo("order-svc");
    // 切来源 → 复用历史
    fireEvent.click(within(card).getByRole("button", { name: "复用历史" }));
    // 候选下拉：order-svc 的白盒在列；frontend 的白盒被 repo 过滤掉
    fireEvent.click(screen.getByText("选择要复用的白盒扫描").closest("button")!);
    fireEvent.click(await screen.findByRole("option", { name: /20260801-120000/ }));
    expect(screen.queryByRole("option", { name: /20260801-999999/ })).toBeNull();
    // YAML 派生：复用卡片写 workspace: <scan_id>（D1 formToYaml 语义）
    const editor = (await openYamlPanel()) as HTMLTextAreaElement;
    await waitFor(() => expect(editor.value).toContain("workspace: 20260801-120000"));
  });

  it("YAML 编辑非法引用 → 错误提示 + 应用按钮禁用；修正后恢复可应用", async () => {
    render(<Harness />);
    const editor = await openYamlPanel();
    // 非法：relations 引用未声明服务 ghost
    fireEvent.change(editor, {
      target: {
        value: "repos:\n  frontend:\n    path: frontend\n    role: entrypoint\nrelations:\n  - from: frontend\n    to: ghost\n    protocol: grpc\n",
      },
    });
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("ghost");
    const apply = screen.getByRole("button", { name: /应用到表单/ });
    expect(apply).toBeDisabled();
    // 修正为合法拓扑 → 错误消失、apply 恢复
    fireEvent.change(editor, {
      target: {
        value: "repos:\n  frontend:\n    path: frontend\n    role: entrypoint\n  order-svc:\n    path: order-svc\n    role: backend\nrelations:\n  - from: frontend\n    to: order-svc\n    protocol: http\n",
      },
    });
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(apply).toBeEnabled();
    // 显式应用回填表单：两张卡片 + http 协议边
    fireEvent.click(apply);
    await waitFor(() => expect(screen.getAllByTestId("corr-repo-row")).toHaveLength(2));
    expect(screen.getByText(/frontend → order-svc/).textContent).toContain("http");
  });

  it("缺 entrypoint 提交校验拦截（唯一卡片切 backend → 显校验问题）", async () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "+ 添加仓库" }));
    const card = screen.getByTestId("corr-repo-row");
    // 唯一卡片从默认 entrypoint 切成 backend → 无 entrypoint
    fireEvent.click(within(card).getByRole("button", { name: "后端" }));
    await waitFor(() =>
      expect(screen.getByTestId("corr-form-issues").textContent).toContain("至少需要一个 entrypoint"));
    // 切回 entrypoint → entrypoint 问题消失（卡片未命名的另一 issue 合法保留）
    fireEvent.click(within(card).getByRole("button", { name: "入口" }));
    await waitFor(() =>
      expect(screen.getByTestId("corr-form-issues").textContent).not.toContain("entrypoint"));
  });

  it("YAML 病态 relations（非列表）→ 裸 TypeError 也纳入错误提示（不崩、apply 禁用）", async () => {
    render(<Harness />);
    const editor = await openYamlPanel();
    // D1 已知限制：yamlToForm 对非列表 relations 抛裸 TypeError（非 CorrYamlError）
    fireEvent.change(editor, {
      target: { value: "repos:\n  a:\n    path: a\n    role: entrypoint\nrelations: 5\n" },
    });
    const alert = await screen.findByRole("alert");
    expect(alert.textContent!.length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /应用到表单/ })).toBeDisabled();
  });
});
