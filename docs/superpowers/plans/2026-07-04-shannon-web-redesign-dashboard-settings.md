# Shannon Web 子项目 5 · Dashboard 首页 + 设置页 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 Dashboard 首页(`/`)+ 设置页(`/settings`)+ 后端只读状态端点 `GET /api/system-status`,Workspaces 列表页路由从 `/` 迁至 `/workspaces`,TopBar 启用 Dashboard/Settings 导航。

**Architecture:** 路由迁移(`/`→DashboardPage、`/workspaces`←WorkspaceListPage、`/settings`→SettingsPage)+ 两新顶层页(放 `pages/` 与 WorkspaceListPage/ScanNewPage 同惯例)+ 一新后端 router。复用 `useWorkspaces` hook(5s 轮询)、`useSystemStatus` hook(新,打开页拉一次)、`theme.ts`(applyTheme/getInitialTheme)、`ErrorState`/`StatusBadge`/`Empty` 共享组件、DSF 15 个 shadcn 组件。Dashboard 不接 SSE,数据源单一 `GET /api/workspaces`。

**Tech Stack:** React 18 + Vite + TypeScript + Tailwind 3 + shadcn/ui (Radix) + react-router-dom 6 + vitest + Testing Library + MSW;FastAPI + starlette TestClient + importlib.metadata。

**Spec:** `docs/superpowers/specs/2026-07-04-shannon-web-redesign-dashboard-settings-design.md`

## Global Constraints

- **契约不动铁律**:不改 `src/state/dashboardReducer.ts` / `formatters.ts` / `api/types.ts` 的 `NdjsonEvent` union / `useEventSource.ts` / `useWorkspaces.ts` / `lib/theme.ts` / `WorkspaceListPage.tsx` 内部 / 现有所有 `/api/*` 端点(只新增 `/api/system-status`)。
- **新页全 Tailwind**:禁止向 `src/styles/events.css` 追加任何规则(DSF Tailwind 优先约定)。事件专用 `.ev-*` class 不动(跨媒介语义色不变量)。
- **前端命令必须 `cd packages/web/frontend`**(cwd 不持久,每次 bash 显式 cd);后端测试 `cd packages/web`。
- **operator 风**:`--radius: 3px`、克制阴影、IBM Plex 三族(mono/sans/serif)、语义色 token(`text-cyan`/`text-green`/`text-red`/`text-yellow`,tailwind.config 已 extend)。
- **TDD 纪律**:每 task 先写失败测试 → 跑验证失败 → 最小实现 → 跑验证通过 → commit。
- **commit 风格**:`feat(web): 子项目5·TaskN <内容>`。
- **测试选择器优先 role-based / 文本**,避免 brittle class 断言。
- **每 task 结束跑相关测试 + `npx tsc --noEmit`**(不引入类型错误)。

---

## File Structure

**新建:**
- `packages/web/src/shannon_web/api/system_status.py` —— 后端 `GET /api/system-status` router
- `packages/web/tests/test_app_system_status.py` —— 后端端点测试
- `packages/web/frontend/src/api/systemStatus.ts` —— 前端 `useSystemStatus` hook + `SystemStatus` 类型(自包含,不改 `types.ts`)
- `packages/web/frontend/src/api/systemStatus.test.ts` —— hook 测试
- `packages/web/frontend/src/pages/DashboardPage.tsx` —— Dashboard 首页(与 WorkspaceListPage/ScanNewPage 同级)
- `packages/web/frontend/src/pages/DashboardPage.test.tsx`
- `packages/web/frontend/src/pages/SettingsPage.tsx` —— 设置页
- `packages/web/frontend/src/pages/SettingsPage.test.tsx`

**修改:**
- `packages/web/src/shannon_web/app.py` —— 注册 system_status router
- `packages/web/frontend/src/router.tsx` —— `/`→DashboardPage、新增 `/workspaces`←WorkspaceListPage、新增 `/settings`→SettingsPage
- `packages/web/frontend/src/components/layout/TopBar.tsx` —— NAV 去 disabled、Workspaces 目标改 `/workspaces`
- `packages/web/frontend/src/router.test.ts` —— 加 Dashboard/Settings/`/workspaces` 断言
- `packages/web/frontend/src/pages/DevComponentsPage.tsx` —— 补登主题 Switch demo + 新页访问提示

---

## Task 1: 后端 `GET /api/system-status` 端点

**Files:**
- Create: `packages/web/src/shannon_web/api/system_status.py`
- Modify: `packages/web/src/shannon_web/app.py`(include_router 区,L73-77 附近)
- Test: `packages/web/tests/test_app_system_status.py`(Create)

**Interfaces:**
- Consumes: `request.app.state.config`(WebConfig,已有 `git_available` property);env `SHANNON_AI_PROVIDER`/`SHANNON_BROWSER_ENGINE`/`SHANNON_TEMPORAL_HOST`/`SHANNON_TEMPORAL_PORT`;`importlib.metadata.version("shannon-web")`。
- Produces: `GET /api/system-status` → `{ai_provider, browser_engine, temporal:{enabled,host,last_status,last_error}, git_available, version}`。后续 Task 2 的 `useSystemStatus` hook 消费此 shape。

**说明(plan 对 spec §5.3 的细化):** spec 说 Temporal "不做实时 ping"。plan 阶段读 `scan_manager.py:108-123` 确认 `_check_temporal` 是 **1s `socket.create_connection` 轻量探测(非 ICMP ping)**。设置页低频打开,实时轻量探测可接受且给用户真实可用信息,故 `last_status` 采用实时探测(connected/error)。这比 spec 原写的 "unknown" 更有用,且不动 `ScanManager`(端点自包含 socket 探测)。

- [ ] **Step 1: 写失败测试**

`packages/web/tests/test_app_system_status.py`:
```python
from fastapi.testclient import TestClient

from shannon_web.app import create_app


def test_system_status_shape():
    client = TestClient(create_app())
    r = client.get("/api/system-status")
    assert r.status_code == 200
    body = r.json()
    # 顶层字段
    assert body["ai_provider"] in {"claude", "openai"}
    assert body["browser_engine"] in {"agent-browser", "playwright"}
    assert isinstance(body["git_available"], bool)
    assert body["version"].startswith("shannon-web")
    # temporal 子对象
    t = body["temporal"]
    assert t["enabled"] is True
    assert "host" in t and isinstance(t["host"], str)
    assert t["last_status"] in {"connected", "error"}
    assert "last_error" in t
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web && uv run pytest tests/test_app_system_status.py -v`
Expected: FAIL(`404` 或路由不存在;端点未注册)。

- [ ] **Step 3: 实现 system_status.py**

`packages/web/src/shannon_web/api/system_status.py`:
```python
from __future__ import annotations

import asyncio
import os
import socket
from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api", tags=["system-status"])


async def _probe_temporal() -> tuple[str, str | None]:
    """轻量 socket 探测 Temporal 可达性(复用 scan_manager._check_temporal 同款逻辑)。

    非 ICMP ping,1s 超时;设置页低频打开可接受(对 spec §5.3 "不做实时 ping" 的细化)。
    """
    host = os.environ.get("SHANNON_TEMPORAL_HOST", "localhost")
    port = int(os.environ.get("SHANNON_TEMPORAL_PORT", "7233"))

    def _probe() -> tuple[bool, str | None]:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True, None
        except OSError as e:
            return False, str(e)

    loop = asyncio.get_running_loop()
    ok, err = await loop.run_in_executor(None, _probe)
    return ("connected", None) if ok else ("error", err)


@router.get("/system-status")
async def system_status(request: Request) -> dict:
    cfg = request.app.state.config
    last_status, last_error = await _probe_temporal()
    try:
        ver = version("shannon-web")
    except PackageNotFoundError:
        ver = "unknown"
    return {
        "ai_provider": os.environ.get("SHANNON_AI_PROVIDER", "claude"),
        "browser_engine": os.environ.get("SHANNON_BROWSER_ENGINE", "agent-browser"),
        "temporal": {
            "enabled": True,
            "host": f'{os.environ.get("SHANNON_TEMPORAL_HOST", "localhost")}:{os.environ.get("SHANNON_TEMPORAL_PORT", "7233")}',
            "last_status": last_status,
            "last_error": last_error,
        },
        "git_available": cfg.git_available,
        "version": f"shannon-web {ver}",
    }
```

- [ ] **Step 4: 注册 router 到 app.py**

`packages/web/src/shannon_web/app.py` 的 import 行(L63):
```python
    from .api import events, fs, multi_configs, scan, system_status, workspaces
```
include_router 区(L73-77 之后、`@app.get("/health")` 之前)加一行:
```python
    app.include_router(system_status.router)
```

- [ ] **Step 5: 跑测试验证通过**

Run: `cd packages/web && uv run pytest tests/test_app_system_status.py -v`
Expected: PASS。再跑 health 回归:`cd packages/web && uv run pytest tests/test_app_health.py -v` → PASS。

- [ ] **Step 6: commit**

```bash
git add packages/web/src/shannon_web/api/system_status.py packages/web/src/shannon_web/app.py packages/web/tests/test_app_system_status.py
git commit -m "feat(web): 子项目5·Task1 后端 GET /api/system-status 只读状态端点"
```

---

## Task 2: 前端 `useSystemStatus` hook + 类型

**Files:**
- Create: `packages/web/frontend/src/api/systemStatus.ts`
- Test: `packages/web/frontend/src/api/systemStatus.test.ts`(Create)

**Interfaces:**
- Consumes: `apiGet`(from `./client`),`ApiError`,`GET /api/system-status`(Task 1)。
- Produces: `useSystemStatus()` → `{data: SystemStatus | null, loading, error, refresh}`;`SystemStatus`/`TemporalStatus` interface。后续 Task 6 SettingsPage 消费。

**关键**:不自动轮询(spec §5.2.2)—— `useEffect` 仅 mount 拉一次,`refresh` 供手动刷新。`SystemStatus` 类型放本文件(不改 `types.ts`,契约铁律)。

- [ ] **Step 1: 写失败测试**

`packages/web/frontend/src/api/systemStatus.test.ts`:
```typescript
import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { useSystemStatus } from "./systemStatus";

const okBody = {
  ai_provider: "claude",
  browser_engine: "agent-browser",
  temporal: { enabled: true, host: "localhost:7233", last_status: "connected", last_error: null },
  git_available: true,
  version: "shannon-web 0.1.0",
};

const server = setupServer(
  http.get("/api/system-status", () => HttpResponse.json(okBody)),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("useSystemStatus", () => {
  it("mount 拉一次 system-status shape", async () => {
    const { result } = renderHook(() => useSystemStatus());
    await waitFor(() => expect(result.current.data).not.toBeNull());
    expect(result.current.data?.ai_provider).toBe("claude");
    expect(result.current.data?.temporal.last_status).toBe("connected");
    expect(result.current.data?.version).toBe("shannon-web 0.1.0");
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("fetch 失败 → error 设置,data 保持 null", async () => {
    server.use(http.get("/api/system-status", () => HttpResponse.json({}, { status: 500 })));
    const { result } = renderHook(() => useSystemStatus());
    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.data).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it("refresh 重新拉取", async () => {
    const { result } = renderHook(() => useSystemStatus());
    await waitFor(() => expect(result.current.data).not.toBeNull());
    let called = 0;
    server.use(http.get("/api/system-status", () => { called += 1; return HttpResponse.json(okBody); }));
    await result.current.refresh();
    expect(called).toBe(1);
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web/frontend && npx vitest run src/api/systemStatus.test.ts`
Expected: FAIL(模块不存在)。

- [ ] **Step 3: 实现 systemStatus.ts**

`packages/web/frontend/src/api/systemStatus.ts`:
```typescript
import { useCallback, useEffect, useState } from "react";
import { apiGet, ApiError } from "./client";

export interface TemporalStatus {
  enabled: boolean;
  host: string;
  last_status: "connected" | "error" | "unknown";
  last_error: string | null;
}

export interface SystemStatus {
  ai_provider: string;
  browser_engine: string;
  temporal: TemporalStatus;
  git_available: boolean;
  version: string;
}

export interface UseSystemStatusResult {
  data: SystemStatus | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

export function useSystemStatus(): UseSystemStatusResult {
  const [data, setData] = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const d = await apiGet<SystemStatus>("/system-status");
      setData(d);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? `加载失败(${e.status})` : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { data, loading, error, refresh };
}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web/frontend && npx vitest run src/api/systemStatus.test.ts`
Expected: PASS(3/3)。

- [ ] **Step 5: tsc + commit**

```bash
cd packages/web/frontend && npx tsc --noEmit
git add packages/web/frontend/src/api/systemStatus.ts packages/web/frontend/src/api/systemStatus.test.ts
git commit -m "feat(web): 子项目5·Task2 useSystemStatus hook（mount 拉一次 + 手动 refresh）"
```

---

## Task 3: 路由迁移 + TopBar 启用 + stub 页

**Files:**
- Modify: `packages/web/frontend/src/router.tsx`
- Modify: `packages/web/frontend/src/components/layout/TopBar.tsx`
- Create: `packages/web/frontend/src/pages/DashboardPage.tsx`(stub)
- Create: `packages/web/frontend/src/pages/SettingsPage.tsx`(stub)
- Modify: `packages/web/frontend/src/router.test.ts`

**Interfaces:**
- Consumes: `AppShell`(根 layout 不动)、`WorkspaceListPage`/`ScanNewPage`(不动)。
- Produces: 路由 `/`→DashboardPage、`/workspaces`→WorkspaceListPage、`/settings`→SettingsPage;TopBar 4 个 NavLink 全启用。后续 Task 4/5 填 DashboardPage 内容,Task 6 填 SettingsPage 内容。

**说明**:`router.test.ts` 是**字符串扫描 `router.tsx` 源码**模式(见现有文件),不是真渲染路由。新断言验证 router.tsx 含 DashboardPage/SettingsPage/`"/workspaces"`/`"/settings"` 字面量。`pages/` 目录是顶层页惯例(WorkspaceListPage/ScanNewPage 同级),DashboardPage/SettingsPage 放 `pages/`(对 spec §4.2 写的 `routes/` 的修正——`routes/` 是 WorkspaceDetail 子路由惯例)。

- [ ] **Step 1: 写失败测试(加断言到 router.test.ts)**

在 `packages/web/frontend/src/router.test.ts` 的 describe 块末尾加一个 it:
```typescript
  it("子项目5:Dashboard/Settings 路由 + Workspaces 迁 /workspaces", () => {
    expect(router).toContain("DashboardPage");
    expect(router).toContain("SettingsPage");
    expect(router).toContain('"/workspaces"');
    expect(router).toContain('"/settings"');
  });
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web/frontend && npx vitest run src/router.test.ts`
Expected: FAIL(新断言:router.tsx 不含 DashboardPage/SettingsPage/`"/workspaces"`/`"/settings"`)。

- [ ] **Step 3a: 创建 stub 页**

`packages/web/frontend/src/pages/DashboardPage.tsx`:
```tsx
export function DashboardPage() {
  return (
    <div className="space-y-4">
      <h1 className="font-serif text-2xl">Dashboard</h1>
      <p className="text-muted-foreground">即将实现(子项目5 Task4/5)</p>
    </div>
  );
}
```

`packages/web/frontend/src/pages/SettingsPage.tsx`:
```tsx
export function SettingsPage() {
  return (
    <div className="space-y-4">
      <h1 className="font-serif text-2xl">设置</h1>
      <p className="text-muted-foreground">即将实现(子项目5 Task6)</p>
    </div>
  );
}
```

- [ ] **Step 3b: 改 router.tsx**

在 `packages/web/frontend/src/router.tsx` 顶部 import 区(L3-4 附近)加:
```tsx
import { DashboardPage } from "./pages/DashboardPage";
import { SettingsPage } from "./pages/SettingsPage";
```
把 children 数组(L37-50)的路由改成:
```tsx
      { path: "/", element: <DashboardPage /> },
      { path: "/workspaces", element: <WorkspaceListPage /> },
      { path: "/scan/new", element: <ScanNewPage /> },
      {
        path: "/p/:workspace",
        element: <WorkspaceDetail />,
        children: [
          { index: true, element: <DefaultTab /> },
          { path: "overview", element: <OverviewTab /> },
          { path: "report", element: <ReportTab /> },
          { path: "deliverables", element: <DeliverablesTab /> },
          { path: "logs", element: <LogsTab /> },
          { path: "live", element: <LiveTab /> },
        ],
      },
      { path: "/settings", element: <SettingsPage /> },
```
(`/p/:workspace` 子路由不动,仅顺序放置。)

- [ ] **Step 3c: 改 TopBar.tsx**

把 `packages/web/frontend/src/components/layout/TopBar.tsx` 的 `NAV` 数组(L19-24)替换为:
```tsx
const NAV: NavItem[] = [
  { label: "Dashboard", to: "/", end: true },
  { label: "Workspaces", to: "/workspaces", end: true },
  { label: "Scan", to: "/scan/new" },
  { label: "Settings", to: "/settings" },
];
```
(全部去 `disabled`;Workspaces 的 `to` 从 `"/"` 改 `"/workspaces"`。)

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web/frontend && npx vitest run src/router.test.ts`
Expected: PASS(含新断言)。再跑列表页回归确认迁移未破坏:`cd packages/web/frontend && npx vitest run src/pages/WorkspaceListPage.test.tsx` → PASS(列表页测试用 `<MemoryRouter>` 包裹,不受 router.tsx 路径改动影响)。

- [ ] **Step 5: tsc + commit**

```bash
cd packages/web/frontend && npx tsc --noEmit
git add packages/web/frontend/src/router.tsx packages/web/frontend/src/components/layout/TopBar.tsx packages/web/frontend/src/pages/DashboardPage.tsx packages/web/frontend/src/pages/SettingsPage.tsx packages/web/frontend/src/router.test.ts
git commit -m "feat(web): 子项目5·Task3 路由迁移(Dashboard/Settings 启用 + Workspaces→/workspaces)"
```

---

## Task 4: DashboardPage 骨架 + 三态 + 全空态 + 汇总数字行

**Files:**
- Modify: `packages/web/frontend/src/pages/DashboardPage.tsx`(stub → 骨架)
- Test: `packages/web/frontend/src/pages/DashboardPage.test.tsx`(Create)

**Interfaces:**
- Consumes: `useWorkspaces()` → `{data: Workspace[], loading, error, refresh}`;`ErrorState`/`Empty`(共享组件);shadcn `Card`/`Skeleton`/`Button`。
- Produces: DashboardPage 三态(loading Skeleton / error ErrorState+重试 / 全空态 Empty+新建扫描)+ 4 汇总数字卡(运行中/今日完成/累计漏洞/累计 cost)+ 顶栏「新建扫描」入口。Task 5 在此基础上加 running 卡片墙 + 最近扫描区。

**关键**:`Workspace.created_at` 是 unix **秒**(列表页 `new Date(unix*1000)` 同款);`completed_at` 同。"今日完成" = `status==="completed" && isToday(completed_at)`。

- [ ] **Step 1: 写失败测试**

`packages/web/frontend/src/pages/DashboardPage.test.tsx`:
```typescript
import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { DashboardPage } from "./DashboardPage";
import type { Workspace } from "../api/types";

// 用「今天」的 unix 秒,验证 isToday 过滤
const todaySec = Math.floor(Date.now() / 1000);
const oldSec = todaySec - 3 * 86400; // 3 天前

const workspaces: Workspace[] = [
  { name: "ws-run", scan_type: "whitebox", status: "running", created_at: todaySec, total_cost_usd: 1.5, total_duration_ms: 120000, vuln_count: 3, is_correlation: false },
  { name: "ws-today", scan_type: "blackbox", status: "completed", created_at: oldSec, completed_at: todaySec, total_cost_usd: 2.0, total_duration_ms: 50000, vuln_count: 5, is_correlation: false },
  { name: "ws-old", scan_type: "whitebox", status: "completed", created_at: oldSec, completed_at: oldSec, total_cost_usd: 0.5, total_duration_ms: 30000, vuln_count: 7, is_correlation: false },
];

const server = setupServer(
  http.get("/api/workspaces", () => HttpResponse.json(workspaces)),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

function renderPage() {
  return render(<MemoryRouter><DashboardPage /></MemoryRouter>);
}

describe("DashboardPage 骨架 + 汇总", () => {
  it("全空态(data=[])→ 引导卡 + 新建扫描按钮", async () => {
    server.use(http.get("/api/workspaces", () => HttpResponse.json([])));
    renderPage();
    expect(await screen.findByText(/还没有扫描/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /\+ 新建扫描/ })).toHaveAttribute("href", "/scan/new");
  });

  it("汇总数字:运行中 1 / 今日完成 1 / 累计漏洞 15 / 累计 cost 4.00", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-run")).toBeInTheDocument());
    // 4 个汇总值;用 getAllByText 精确匹配数字
    // 运行中=1, 今日完成=1 → 两个 "1"(精确匹配,不撞 $1.50 等含 1 的文本)
    expect(screen.getAllByText("1")).toHaveLength(2);
    // 累计漏洞 = 3+5+7 = 15
    expect(screen.getByText("15")).toBeInTheDocument();
    // 累计 cost = 1.5+2.0+0.5 = 4.00
    expect(screen.getByText("$4.00")).toBeInTheDocument();
  });

  it("顶栏「+ 新建扫描」入口跳 /scan/new", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-run")).toBeInTheDocument());
    const links = screen.getAllByRole("link", { name: /\+ 新建扫描/ });
    expect(links.some((l) => l.getAttribute("href") === "/scan/new")).toBe(true);
  });

  it("error → ErrorState(role=alert)+ 重试", async () => {
    server.use(http.get("/api/workspaces", () => HttpResponse.json({}, { status: 500 })));
    renderPage();
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /重试/ })).toBeInTheDocument();
  });

  it("loading → Skeleton", async () => {
    server.use(http.get("/api/workspaces", () => new Promise(() => {}))); // 永不 resolve
    renderPage();
    expect(document.querySelector(".animate-pulse")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web/frontend && npx vitest run src/pages/DashboardPage.test.tsx`
Expected: FAIL(stub 无汇总/三态)。

- [ ] **Step 3: 实现 DashboardPage 骨架**

替换 `packages/web/frontend/src/pages/DashboardPage.tsx` 全文:
```tsx
import { Link } from "react-router-dom";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ErrorState";
import { Empty } from "@/components/Empty";
import { useWorkspaces } from "@/api/useWorkspaces";
import type { Workspace } from "@/api/types";

function isToday(unix: number | null | undefined): boolean {
  if (!unix) return false;
  const d = new Date(unix * 1000);
  const now = new Date();
  return d.getFullYear() === now.getFullYear()
    && d.getMonth() === now.getMonth()
    && d.getDate() === now.getDate();
}

function sum<T>(arr: T[], pick: (t: T) => number | undefined): number {
  return arr.reduce((acc, x) => acc + (pick(x) ?? 0), 0);
}

export function DashboardPage() {
  const { data, loading, error, refresh } = useWorkspaces();

  if (error && data.length === 0) {
    return <ErrorState message={`Dashboard 加载失败:${error}`} onRetry={refresh} />;
  }
  if (loading && data.length === 0) {
    return (
      <div className="space-y-2">
        {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-8 w-full" />)}
      </div>
    );
  }
  if (data.length === 0) {
    return (
      <Empty title="还没有扫描" hint="新建一个扫描开始">
        <Link to="/scan/new"><Button>+ 新建扫描</Button></Link>
      </Empty>
    );
  }

  const running = data.filter((w) => w.status === "running");
  const completedToday = data.filter((w) => w.status === "completed" && isToday(w.completed_at));
  const totalVulns = sum(data, (w) => w.vuln_count);
  const totalCost = sum(data, (w) => w.total_cost_usd);

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="font-serif text-2xl">Shannon</h1>
        <Link to="/scan/new"><Button>+ 新建扫描</Button></Link>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4" role="group" aria-label="汇总">
        <Card><CardContent className="p-4">
          <div className="text-xs text-muted-foreground">运行中</div>
          <div className="font-mono text-2xl text-cyan">{running.length}</div>
        </CardContent></Card>
        <Card><CardContent className="p-4">
          <div className="text-xs text-muted-foreground">今日完成</div>
          <div className="font-mono text-2xl text-green">{completedToday.length}</div>
        </CardContent></Card>
        <Card><CardContent className="p-4">
          <div className="text-xs text-muted-foreground">累计漏洞</div>
          <div className="font-mono text-2xl">{totalVulns}</div>
        </CardContent></Card>
        <Card><CardContent className="p-4">
          <div className="text-xs text-muted-foreground">累计 cost</div>
          <div className="font-mono text-2xl">${totalCost.toFixed(2)}</div>
        </CardContent></Card>
      </div>

      {/* Task 5 在此插入 running 卡片墙 + 最近扫描区 */}
    </div>
  );
}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web/frontend && npx vitest run src/pages/DashboardPage.test.tsx`
Expected: PASS(5/5)。

- [ ] **Step 5: tsc + commit**

```bash
cd packages/web/frontend && npx tsc --noEmit
git add packages/web/frontend/src/pages/DashboardPage.tsx packages/web/frontend/src/pages/DashboardPage.test.tsx
git commit -m "feat(web): 子项目5·Task4 DashboardPage 骨架+三态+全空态+汇总数字"
```

---

## Task 5: DashboardPage running 卡片墙 + 最近扫描区

**Files:**
- Modify: `packages/web/frontend/src/pages/DashboardPage.tsx`(加区块)
- Modify: `packages/web/frontend/src/pages/DashboardPage.test.tsx`(加 case)

**Interfaces:**
- Consumes: `StatusBadge`(共享,签名 `{status, correlation?}`);shadcn `Card`/`Badge`;`Workspace`。
- Produces: running workspace 卡片墙(整张可点 → `/p/{ws}/live`,无则 muted 空态)+ 最近 8 条非 running(整行可点 → `/p/{ws}`,标题旁「查看全部 →」跳 `/workspaces`)。

- [ ] **Step 1: 加测试 case**

在 `packages/web/frontend/src/pages/DashboardPage.test.tsx` 的 describe 块末尾追加:
```typescript
  it("running 卡片墙:整张可点跳 /p/ws-run/live", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-run")).toBeInTheDocument());
    const link = screen.getByRole("link", { name: /查看实时/ });
    expect(link.getAttribute("href")).toBe("/p/ws-run/live");
  });

  it("最近扫描区:非 running 行 + 「查看全部」跳 /workspaces", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-today")).toBeInTheDocument());
    expect(screen.getByText("ws-old")).toBeInTheDocument();
    // 「查看全部 →」跳列表页
    expect(screen.getByRole("link", { name: /查看全部/ }).getAttribute("href")).toBe("/workspaces");
    // 最近行整行可点跳 /p/{ws}
    expect(screen.getByRole("link", { name: /ws-today/ }).getAttribute("href")).toBe("/p/ws-today");
  });

  it("无 running → 显示空态文案", async () => {
    server.use(http.get("/api/workspaces", () => HttpResponse.json([
      { name: "ws-done", scan_type: "whitebox", status: "completed", created_at: todaySec, completed_at: todaySec, vuln_count: 1, is_correlation: false },
    ])));
    renderPage();
    await waitFor(() => expect(screen.getByText("ws-done")).toBeInTheDocument());
    expect(screen.getByText(/当前无运行中扫描/)).toBeInTheDocument();
  });
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web/frontend && npx vitest run src/pages/DashboardPage.test.tsx`
Expected: FAIL(新 case:无"查看实时"/"查看全部"/"当前无运行中扫描")。

- [ ] **Step 3: 加 running 区 + 最近区**

在 `packages/web/frontend/src/pages/DashboardPage.tsx` 顶部 import 区加:
```tsx
import { StatusBadge } from "@/components/StatusBadge";
import { Badge } from "@/components/ui/badge";
```
在文件顶部辅助函数区(`sum` 之后)加两个 helper:
```tsx
function fmtMs(ms: number): string {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  if (h > 0) return `${h}h${m % 60}m`;
  if (m > 0) return `${m}m${s % 60}s`;
  return `${s}s`;
}

function fmtTime(unix?: number | null): string {
  if (!unix) return "—";
  return new Date(unix * 1000).toLocaleString();
}
```
在组件内 `return` 之前(汇总卡计算之后)加最近区计算:
```tsx
  const recent = data
    .filter((w) => w.status !== "running")
    .slice()
    .sort((a, b) => (b.completed_at ?? b.created_at) - (a.completed_at ?? a.created_at))
    .slice(0, 8);
```
把 `{/* Task 5 在此插入 running 卡片墙 + 最近扫描区 */}` 占位注释替换为:
```tsx
      {running.length > 0 ? (
        <section className="space-y-2">
          <h2 className="font-serif text-lg text-muted-foreground">正在运行</h2>
          <div className="grid gap-3 md:grid-cols-2">
            {running.map((w) => (
              <Link key={w.name} to={`/p/${w.name}/live`} className="block">
                <Card className="transition-color hover:border-primary">
                  <CardContent className="space-y-1 p-4 font-mono text-sm">
                    <div className="flex items-center justify-between">
                      <StatusBadge status={w.status} />
                      <Badge variant="outline">{w.scan_type}</Badge>
                    </div>
                    <div className="text-base text-foreground">{w.name}</div>
                    <div className="text-xs text-muted-foreground">
                      {w.total_cost_usd != null ? `$${w.total_cost_usd.toFixed(2)}` : "—"}{" · "}
                      {w.total_duration_ms ? fmtMs(w.total_duration_ms) : "—"}
                    </div>
                    <div className="text-xs text-primary">查看实时 →</div>
                  </CardContent>
                </Card>
              </Link>
            ))}
          </div>
        </section>
      ) : (
        <p className="text-sm text-muted-foreground">当前无运行中扫描</p>
      )}

      {recent.length > 0 && (
        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <h2 className="font-serif text-lg text-muted-foreground">最近扫描</h2>
            <Link to="/workspaces" className="text-sm text-primary hover:underline">查看全部 →</Link>
          </div>
          <Card>
            <CardContent className="divide-y divide-border p-0">
              {recent.map((w) => (
                <Link key={w.name} to={`/p/${w.name}`} className="flex flex-wrap items-center gap-3 p-3 font-mono text-sm hover:bg-accent">
                  <StatusBadge status={w.status} />
                  <span className="text-foreground">{w.name}</span>
                  <Badge variant="outline">{w.scan_type}</Badge>
                  <span className="text-muted-foreground">{w.vuln_count ?? 0} vuln</span>
                  <span className="text-muted-foreground">{w.total_cost_usd != null ? `$${w.total_cost_usd.toFixed(2)}` : "—"}</span>
                  <span className="ml-auto text-xs text-muted-foreground">{fmtTime(w.completed_at ?? w.created_at)}</span>
                </Link>
              ))}
            </CardContent>
          </Card>
        </section>
      )}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web/frontend && npx vitest run src/pages/DashboardPage.test.tsx`
Expected: PASS(全部 case)。

- [ ] **Step 5: tsc + commit**

```bash
cd packages/web/frontend && npx tsc --noEmit
git add packages/web/frontend/src/pages/DashboardPage.tsx packages/web/frontend/src/pages/DashboardPage.test.tsx
git commit -m "feat(web): 子项目5·Task5 DashboardPage running 卡片墙+最近扫描区"
```

---

## Task 6: SettingsPage 三模块(主题 + 系统状态 + 关于)

**Files:**
- Modify: `packages/web/frontend/src/pages/SettingsPage.tsx`(stub → 完整)
- Test: `packages/web/frontend/src/pages/SettingsPage.test.tsx`(Create)

**Interfaces:**
- Consumes: `useSystemStatus()`(Task 2);`theme.ts` 的 `applyTheme`/`getInitialTheme` + `Theme` 类型 + `THEME_KEY`;shadcn `Switch`/`Label`/`Card`/`Badge`/`Skeleton`;`ErrorState`。
- Produces: 主题切换(与 TopBar ThemeToggle 共用 `applyTheme`,同步 localStorage)+ 系统状态只读面板(fetch 失败局部 ErrorState)+ 关于/版本。

**关键**:`theme.ts` 真实函数是 `applyTheme(t)` / `getInitialTheme()`(不是 setTheme/getTheme)。Switch `checked = theme === "light"`。

- [ ] **Step 1: 写失败测试**

`packages/web/frontend/src/pages/SettingsPage.test.tsx`:
```typescript
import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { SettingsPage } from "./SettingsPage";

const okBody = {
  ai_provider: "claude",
  browser_engine: "agent-browser",
  temporal: { enabled: true, host: "localhost:7233", last_status: "connected", last_error: null },
  git_available: true,
  version: "shannon-web 0.1.0",
};

const server = setupServer(
  http.get("/api/system-status", () => HttpResponse.json(okBody)),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

describe("SettingsPage", () => {
  it("渲染三张 Card(主题/系统状态/关于)", async () => {
    render(<SettingsPage />);
    // CardTitle 渲染为 div(非语义 heading),用文本匹配
    expect(await screen.findByText("主题")).toBeInTheDocument();
    expect(screen.getByText("系统状态")).toBeInTheDocument();
    expect(screen.getByText("关于")).toBeInTheDocument();
  });

  it("状态面板渲染各字段(ai_provider/temporal/version)", async () => {
    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByText("claude")).toBeInTheDocument());
    expect(screen.getByText("agent-browser")).toBeInTheDocument();
    expect(screen.getByText("localhost:7233")).toBeInTheDocument();
    expect(screen.getByText("shannon-web 0.1.0")).toBeInTheDocument();
    expect(screen.getByText("可用")).toBeInTheDocument(); // git_available
  });

  it("主题 Switch 切到浅色 → <html> 加 light class + localStorage", async () => {
    render(<SettingsPage />);
    const sw = screen.getByRole("switch", { name: /切换深浅主题/ });
    fireEvent.click(sw);
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(localStorage.getItem("shannon-theme")).toBe("light");
  });

  it("status fetch 失败 → 局部 ErrorState(role=alert)", async () => {
    server.use(http.get("/api/system-status", () => HttpResponse.json({}, { status: 500 })));
    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    // 主题 Card 仍在(不受 status 失败影响)
    expect(screen.getByText("主题")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd packages/web/frontend && npx vitest run src/pages/SettingsPage.test.tsx`
Expected: FAIL(stub 无三模块)。

- [ ] **Step 3: 实现 SettingsPage**

替换 `packages/web/frontend/src/pages/SettingsPage.tsx` 全文:
```tsx
import { useState } from "react";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ErrorState";
import { useSystemStatus } from "@/api/systemStatus";
import { applyTheme, getInitialTheme, type Theme } from "@/lib/theme";

export function SettingsPage() {
  const initial = typeof window !== "undefined" ? getInitialTheme() : "dark";
  const [theme, setThemeState] = useState<Theme>(initial);
  const { data, loading, error, refresh } = useSystemStatus();

  function setTheme(t: Theme) {
    setThemeState(t);
    applyTheme(t);
  }

  return (
    <div className="space-y-6">
      <h1 className="font-serif text-2xl">设置</h1>

      <Card>
        <CardHeader><CardTitle className="font-serif text-base">主题</CardTitle></CardHeader>
        <CardContent className="flex items-center gap-3 text-sm">
          <Label htmlFor="theme-switch">深色</Label>
          <Switch
            id="theme-switch"
            checked={theme === "light"}
            onCheckedChange={(c) => setTheme(c ? "light" : "dark")}
            aria-label="切换深浅主题"
          />
          <Label htmlFor="theme-switch">浅色</Label>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="font-serif text-base">系统状态</CardTitle></CardHeader>
        <CardContent>
          {loading && <Skeleton className="h-20 w-full" />}
          {error && <ErrorState message={`状态加载失败:${error}`} onRetry={refresh} />}
          {data && (
            <dl className="grid grid-cols-[140px_1fr] gap-y-2 font-mono text-sm">
              <dt className="text-muted-foreground">AI 引擎</dt>
              <dd>{data.ai_provider}</dd>
              <dt className="text-muted-foreground">浏览器引擎</dt>
              <dd>{data.browser_engine}</dd>
              <dt className="text-muted-foreground">Temporal</dt>
              <dd className="flex items-center gap-2">
                {data.temporal.host}
                <Badge variant="outline" className={data.temporal.last_status === "connected" ? "border-green/40 text-green" : "border-red/40 text-red"}>
                  {data.temporal.last_status}
                </Badge>
              </dd>
              <dt className="text-muted-foreground">Git</dt>
              <dd>{data.git_available ? "可用" : "不可用"}</dd>
              <dt className="text-muted-foreground">版本</dt>
              <dd>{data.version}</dd>
            </dl>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="font-serif text-base">关于</CardTitle></CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          <div>shannon-py 安全扫描平台 web 控制台。版本信息见上方系统状态面板。</div>
        </CardContent>
      </Card>
    </div>
  );
}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd packages/web/frontend && npx vitest run src/pages/SettingsPage.test.tsx`
Expected: PASS(4/4)。

- [ ] **Step 5: tsc + commit**

```bash
cd packages/web/frontend && npx tsc --noEmit
git add packages/web/frontend/src/pages/SettingsPage.tsx packages/web/frontend/src/pages/SettingsPage.test.tsx
git commit -m "feat(web): 子项目5·Task6 SettingsPage 三模块(主题+系统状态+关于)"
```

---

## Task 7: dev 预览页补登 + 双主题冒烟 + 完工验收

**Files:**
- Modify: `packages/web/frontend/src/pages/DevComponentsPage.tsx`(加主题 Switch demo + 新页访问提示 Section)
- 无新单测(冒烟锚点 + 全套回归作护栏)

**说明**:不在 dev 预览页嵌整页 DashboardPage/SettingsPage(它们用 `useWorkspaces`/`useSystemStatus` 会真 fetch,dev 预览页需要后端跑着才有意义)。改为加一个 Section 展示设置页主题切换控件 + 文字提示访问 `/`、`/settings` 实测双主题。

- [ ] **Step 1: 补登 Section**

在 `packages/web/frontend/src/pages/DevComponentsPage.tsx` 的 `<Section title="Theme">` 之后(或任意位置)加一个 Section:
```tsx
      <Section title="子项目5 新页(Dashboard / Settings)">
        <span className="text-sm text-muted-foreground">访问 </span>
        <code className="font-mono text-cyan">/</code>
        <span className="text-sm text-muted-foreground"> 看 Dashboard 进站概览,</span>
        <code className="font-mono text-cyan">/settings</code>
        <span className="text-sm text-muted-foreground"> 看主题 + 系统状态 + 关于。ThemeToggle 切深/浅验对比度。</span>
      </Section>
```

- [ ] **Step 2: tsc + build**

Run:
```bash
cd packages/web/frontend && npx tsc --noEmit
cd packages/web/frontend && npm run build
```
Expected: tsc 0 错;build 成功。

- [ ] **Step 3: 跑全套前端测试 + 后端测试回归**

Run:
```bash
cd packages/web/frontend && npx vitest run
cd packages/web && uv run pytest tests/
```
Expected: 前端全套 PASS(含 dashboardReducer/router/list/detail/systemStatus/DashboardPage/SettingsPage);后端 PASS(含 test_app_health/test_app_system_status)。

- [ ] **Step 4: 人工双主题冒烟**

```bash
cd packages/web/frontend && npm run dev
```
冒烟(人工):打开 `/` → 看 Dashboard 4 汇总卡 + running/最近区 + ThemeToggle 切深/浅验可读;打开 `/settings` → 主题 Switch 切换验 `<html>` class 变 + 状态面板各字段 + 关于;TopBar 四个导航全可点(Dashboard/Workspaces/Scan/Settings);Workspaces 跳 `/workspaces` 列表页仍工作。

- [ ] **Step 5: commit**

```bash
git add packages/web/frontend/src/pages/DevComponentsPage.tsx
git commit -m "feat(web): 子项目5·Task7 dev 预览页补登新页锚点 + 双主题冒烟"
```

---

## 完工验收

- [ ] 全套前端测试绿:`cd packages/web/frontend && npx vitest run`
- [ ] 全套后端测试绿:`cd packages/web && uv run pytest tests/`
- [ ] tsc 0 错:`cd packages/web/frontend && npx tsc --noEmit`
- [ ] build 成功:`cd packages/web/frontend && npm run build`
- [ ] 契约不动验证:`git diff main -- packages/web/frontend/src/state/dashboardReducer.ts packages/web/frontend/src/state/formatters.ts packages/web/frontend/src/api/types.ts packages/web/frontend/src/api/useEventSource.ts packages/web/frontend/src/api/useWorkspaces.ts packages/web/frontend/src/lib/theme.ts packages/web/frontend/src/pages/WorkspaceListPage.tsx` → 空 diff(types.ts 仅 NdjsonEvent 不动;若新增 SystemStatus 类型应只在 systemStatus.ts)。
- [ ] `/api/system-status` 端点可访问:`curl localhost:7878/api/system-status` 返回完整 shape。
- [ ] 人工双主题冒烟:`/` + `/settings` × 深/浅 共 4 个组合肉眼过一遍 + TopBar 四导航全启用。
