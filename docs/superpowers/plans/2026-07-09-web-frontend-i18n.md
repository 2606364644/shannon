# shannon-web 前端 i18n 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 shannon-web 前端引入 react-i18next,把全站核心页面 UI 硬编码中文抽成 key,实现中英双语可切换、跟随浏览器语言、用户选择持久化。

**Architecture:** `src/i18n/index.ts` 一次性 init(资源内联同步、语言检测、fallback),`main.tsx` 副作用 import,无需 Provider。文案放 `src/locales/{zh,en}.json` 单 `translation` namespace,顶层按页面分组。新增 `LanguageSwitcher` 挂 TopBar。状态枚举值经 i18n 映射 + 未知 fallback。

**Tech Stack:** React 18.3 + TypeScript 5.5 + Vite 5 + vitest + @testing-library/react;i18next + react-i18next + i18next-browser-languagedetector(dev: i18next-parser)。

**对应 spec:** `docs/superpowers/specs/2026-07-09-web-frontend-i18n-design.md`

## Global Constraints

(每个 task 的需求都隐含包含本节)

- **范围**:仅前端 UI 文案(层 1)。**不动** `src/lib/vuln-block.ts` 中文关键词、后端 HTTPException detail、LLM 报告正文/日志内容/事件流、动态数据(仓库名/URL/分支/大小/时间戳)。
- **不动页面**:`src/pages/DevComponentsPage.tsx`(dev-only,本次不 i18n)。
- **库**:`i18next` + `react-i18next` + `i18next-browser-languagedetector`;devDependency `i18next-parser`。
- **i18n init 参数**:`fallbackLng:'zh'`、`supportedLngs:['zh','en']`、`load:'languageOnly'`、`interpolation.escapeValue:false`、`react.useSuspense:false`、`detection.order:['localStorage','navigator']`、`detection.lookupLocalStorage:'shannon.lang'`、`detection.cacheUserLanguage:true`。resources 内联 import(同步,无 Suspense)。
- **locale 文件**:`src/locales/zh.json` + `src/locales/en.json`,单 `translation` namespace,顶层分组 `common`/`nav`/`repos`/`workspaces`/`scan`/`dashboard`/`settings`/`repoDetail`/`workspaceDetail`。key 命名 camelCase,形如 `namespace[.section].key`。插值用 i18next `{{var}}`。
- **状态值映射**:`StatusBadge`/`STATE_BADGE` 的枚举值经 `t()` 映射,**未知枚举 fallback 渲染原值**(不得显示空白)。
- **测试**:前端测试必须 `cd packages/web/frontend` 再跑(cwd 不持久)。命令:`npx vitest run <file>`、`npx tsc -b`、`npx vite build`。`@` 别名指向 `./src`。
- **TDD**:新功能(LanguageSwitcher、状态 fallback、i18n init)走红→绿;文案迁移走「迁移 + 切语言回归测试(英文断言先红后绿)」。
- **频繁 commit**:每个 task 末尾 commit;commit message 前缀 `feat(i18n):`。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `src/i18n/index.ts` | i18n init + 检测 + resources 内联,export default i18n | 新增 |
| `src/locales/zh.json` | 中文文案字典 | 新增,逐 task 扩充 |
| `src/locales/en.json` | 英文文案字典 | 新增,逐 task 扩充 |
| `src/components/LanguageSwitcher.tsx` | zh/en 切换按钮 | 新增 |
| `i18next-parser.config.js` | key 提取配置 | 新增(Task 12) |
| `src/test-setup.ts` | vitest 全局 setup | 修改:import i18n |
| `src/i18n/i18n.test.ts` | i18n init 行为测试 | 新增 |
| `src/i18n/locales.test.ts` | locale 完整性(zh/en key 一致 + en 无空值) | 新增 |
| `src/main.tsx` | 入口 | 修改:import './i18n' |
| `src/components/layout/TopBar.tsx` | 顶部导航 | 修改:NAV label/aria → t();挂 LanguageSwitcher |
| `src/pages/ReposPage.tsx` + `AddRepoDialog` + `CloneProgress` | 仓库页 | 修改:文案 → t() |
| `src/components/StatusBadge.tsx` | 状态徽章 | 修改:枚举本地化 + fallback |
| `src/pages/WorkspaceListPage.tsx` | 扫描任务列表 | 修改:消除中英混排 |
| `src/pages/ScanNewPage.tsx` + `ScanFormFields` | 扫描表单 | 修改 |
| `src/pages/DashboardPage.tsx` + `SettingsPage.tsx` | 概览/设置 | 修改 |
| `src/pages/RepoDetailPage.tsx` | 仓库详情 | 修改 |
| `src/routes/WorkspaceDetail/*.tsx`(5 tab) | workspace 详情 tabs | 修改:仅 UI 文案 |
| `src/components/MarkdownVulnCard.tsx` + `MarkdownView.tsx` | 漏洞卡/Markdown | 修改:仅 UI 模板文案 |

**通用迁移方法(所有文案迁移 task 适用)**:
1. 用 `rg -n "[一-鿿]" <file>` 扫出该文件所有含中文的行。
2. 按本 task 的 namespace 表把每个硬编码中文写成 `t('namespace.key')`,插值改 `t('key', { var })`。
3. 组件顶部加 `const { t } = useTranslation();`(从 `react-i18next` import)。
4. 模块级常量(如 `UNGROUPED`、`STATE_BADGE`)不能调 hook:把文案改成 key 字符串,在使用处(组件内)调 `t()`,或把值作为参数从组件传入。

---

## Task 1:搭建 i18n 基础设施

**Files:**
- Create: `src/i18n/index.ts`
- Create: `src/locales/zh.json`
- Create: `src/locales/en.json`
- Create: `src/i18n/i18n.test.ts`
- Create: `src/i18n/locales.test.ts`
- Modify: `src/test-setup.ts`
- Modify: `src/main.tsx`

**Interfaces:**
- Produces: `src/i18n/index.ts` 默认导出 i18n 实例;`useTranslation()`(react-i18next)在各组件可用;localStorage key `shannon.lang`;locale 顶层分组 `common`/`nav`(后续 task 扩充)。

- [ ] **Step 1: 安装依赖**

Run:
```bash
cd packages/web/frontend
npm install i18next react-i18next i18next-browser-languagedetector
```
Expected: 三个包写入 `package.json` dependencies。

- [ ] **Step 2: 写 locale 骨架 `src/locales/zh.json`**

```json
{
  "common": {
    "cancel": "取消",
    "confirm": "确认",
    "delete": "删除",
    "update": "更新",
    "loading": "加载中…",
    "langSwitchAria": "切换语言"
  },
  "nav": {
    "dashboard": "Dashboard",
    "workspaces": "Workspaces",
    "repos": "仓库",
    "scan": "Scan",
    "settings": "Settings",
    "mainAria": "主导航"
  }
}
```

- [ ] **Step 3: 写 `src/locales/en.json`(key 必须与 zh 完全一致)**

```json
{
  "common": {
    "cancel": "Cancel",
    "confirm": "Confirm",
    "delete": "Delete",
    "update": "Update",
    "loading": "Loading…",
    "langSwitchAria": "Switch language"
  },
  "nav": {
    "dashboard": "Dashboard",
    "workspaces": "Workspaces",
    "repos": "Repositories",
    "scan": "Scan",
    "settings": "Settings",
    "mainAria": "Main navigation"
  }
}
```

- [ ] **Step 4: 写 `src/i18n/index.ts`**

```ts
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import zh from "../locales/zh.json";
import en from "../locales/en.json";

void i18n
  .use(initReactI18next)
  .use(LanguageDetector)
  .init({
    resources: {
      zh: { translation: zh },
      en: { translation: en },
    },
    fallbackLng: "zh",
    supportedLngs: ["zh", "en"],
    load: "languageOnly",
    interpolation: { escapeValue: false },
    react: { useSuspense: false },
    detection: {
      order: ["localStorage", "navigator"],
      lookupLocalStorage: "shannon.lang",
      cacheUserLanguage: true,
    },
  });

export default i18n;
```

> 若 Step 10 的 `tsc -b` 报 JSON import 错误,确认 `tsconfig.json` 含 `"resolveJsonModule": true`(Vite react-ts 模板通常已开);未开则加上。

- [ ] **Step 5: 写失败测试 `src/i18n/i18n.test.ts`**

```ts
import { describe, it, expect, beforeEach } from "vitest";
import i18n from "@/i18n";

describe("i18n init", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  it("fallbackLng 为 zh", () => {
    expect(i18n.options.fallbackLng).toBe("zh");
  });

  it("zh/en 关键 key 都有值且可切换", () => {
    expect(i18n.t("nav.repos")).toBe("仓库");
    i18n.changeLanguage("en");
    expect(i18n.t("nav.repos")).toBe("Repositories");
  });
});
```

- [ ] **Step 6: 写 locale 完整性测试 `src/i18n/locales.test.ts`**

```ts
import { describe, it, expect } from "vitest";
import zh from "../locales/zh.json";
import en from "../locales/en.json";

type Obj = Record<string, unknown>;
function leafKeys(obj: Obj, prefix = ""): string[] {
  return Object.entries(obj).flatMap(([k, v]) =>
    v && typeof v === "object" ? leafKeys(v as Obj, `${prefix}${k}.`) : [`${prefix}${k}`]
  );
}
function get(obj: Obj, path: string): unknown {
  return path.split(".").reduce<unknown>((acc, p) => (acc && typeof acc === "object" ? (acc as Obj)[p] : undefined), obj);
}

describe("locale 完整性", () => {
  const zhKeys = leafKeys(zh as Obj).sort();
  const enKeys = leafKeys(en as Obj).sort();

  it("zh 与 en 的 key 集合完全一致", () => {
    expect(enKeys, "en 与 zh 的 key 不一致").toEqual(zhKeys);
  });

  it("en 所有 key 都有非空值", () => {
    for (const k of enKeys) {
      expect(get(en as Obj, k), `en.${k} 缺值`).toBeTruthy();
    }
  });
});
```

- [ ] **Step 7: 测试 setup 加载 i18n —— 修改 `src/test-setup.ts`**

在文件顶部添加一行(若无其他内容则此行为全部):

```ts
import "@/i18n";
```

(若 `src/test-setup.ts` 已有内容,只在顶部加这一行 import,其余保留。)

- [ ] **Step 8: 跑测试,确认 i18n 测试通过、locale 完整性通过**

Run: `cd packages/web/frontend && npx vitest run src/i18n`
Expected: PASS(3 个测试绿)。

- [ ] **Step 9: 入口挂载 —— 修改 `src/main.tsx`**

在 `import "./styles/index.css";` 之后加一行:

```ts
import "./i18n";
```

- [ ] **Step 10: tsc + build 验证**

Run: `cd packages/web/frontend && npx tsc -b && npx vite build`
Expected: 无错误。

- [ ] **Step 11: Commit**

```bash
git add packages/web/frontend/src/i18n packages/web/frontend/src/locales packages/web/frontend/src/test-setup.ts packages/web/frontend/src/main.tsx packages/web/frontend/package.json packages/web/frontend/package-lock.json
git commit -m "feat(i18n): 搭建 react-i18next 基础设施 + locale 完整性校验"
```

---

## Task 2:LanguageSwitcher 组件

**Files:**
- Create: `src/components/LanguageSwitcher.tsx`
- Create: `src/components/LanguageSwitcher.test.tsx`

**Interfaces:**
- Produces: `<LanguageSwitcher />` —— 点击在 zh/en 间切换,调 `i18n.changeLanguage`,detector 自动写 `localStorage['shannon.lang']`。

- [ ] **Step 1: 写失败测试 `src/components/LanguageSwitcher.test.tsx`**

```tsx
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import LanguageSwitcher from "./LanguageSwitcher";
import i18n from "@/i18n";

describe("LanguageSwitcher", () => {
  beforeEach(() => {
    localStorage.removeItem("shannon.lang");
    i18n.changeLanguage("zh");
  });

  it("中文时显示 EN 按钮", () => {
    render(<LanguageSwitcher />);
    expect(screen.getByLabelText("切换语言")).toHaveTextContent("EN");
  });

  it("点击切换到英文并持久化", () => {
    render(<LanguageSwitcher />);
    fireEvent.click(screen.getByLabelText("切换语言"));
    expect(i18n.language).toMatch(/^en/);
    expect(localStorage.getItem("shannon.lang")).toBe("en");
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd packages/web/frontend && npx vitest run src/components/LanguageSwitcher.test.tsx`
Expected: FAIL(组件不存在)。

- [ ] **Step 3: 实现 `src/components/LanguageSwitcher.tsx`**

```tsx
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";

export function LanguageSwitcher() {
  const { t, i18n } = useTranslation();
  const isZh = (i18n.language ?? "zh").startsWith("zh");
  return (
    <Button
      variant="ghost"
      size="sm"
      className="px-2 text-xs"
      aria-label={t("common.langSwitchAria")}
      onClick={() => i18n.changeLanguage(isZh ? "en" : "zh")}
    >
      {isZh ? "EN" : "中"}
    </Button>
  );
}

export default LanguageSwitcher;
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd packages/web/frontend && npx vitest run src/components/LanguageSwitcher.test.tsx`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/components/LanguageSwitcher.tsx packages/web/frontend/src/components/LanguageSwitcher.test.tsx
git commit -m "feat(i18n): 新增 LanguageSwitcher zh/en 切换组件"
```

---

## Task 3:TopBar 集成(导航 i18n + 挂切换器)

**Files:**
- Modify: `src/components/layout/TopBar.tsx`
- Modify: `src/components/layout/TopBar.test.tsx`(若存在;否则 Create)

**Interfaces:**
- Consumes: Task 1 的 `useTranslation`、Task 2 的 `<LanguageSwitcher />`、locale `nav.*` key。

- [ ] **Step 1: 先看是否有现有 TopBar 测试**

Run: `cd packages/web/frontend && ls src/components/layout/TopBar.test.tsx 2>/dev/null && echo EXISTS || echo NONE`
- 若 EXISTS:本 task 的测试追加到现有文件,注意不破坏现有断言(默认中文,文案迁移后仍是中文,断言不变)。
- 若 NONE:Create 新测试文件。

- [ ] **Step 2: 写/追加失败测试(切语言断言)**

在 `TopBar.test.tsx` 加(create 或 append):

```tsx
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { TopBar } from "./TopBar";
import i18n from "@/i18n";

describe("TopBar i18n", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  it("中文渲染导航「仓库」", () => {
    render(<MemoryRouter><TopBar /></MemoryRouter>);
    expect(screen.getByText("仓库")).toBeInTheDocument();
  });

  it("切英文后导航变 Repositories", () => {
    render(<MemoryRouter><TopBar /></MemoryRouter>);
    i18n.changeLanguage("en");
    expect(screen.getByText("Repositories")).toBeInTheDocument();
  });

  it("渲染语言切换器", () => {
    render(<MemoryRouter><TopBar /></MemoryRouter>);
    expect(screen.getByLabelText("切换语言")).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd packages/web/frontend && npx vitest run src/components/layout/TopBar.test.tsx`
Expected: FAIL(切英文断言失败,因 label 仍硬编码中文)。

- [ ] **Step 4: 改 `src/components/layout/TopBar.tsx`**

完整替换文件内容:

```tsx
import { Link, NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { ThemeToggle } from "./ThemeToggle";
import { LanguageSwitcher } from "./LanguageSwitcher";

interface NavItem {
  labelKey: string;
  to: string;
  disabled?: boolean;
  end?: boolean;
}

const NAV: NavItem[] = [
  { labelKey: "nav.dashboard", to: "/", end: true },
  { labelKey: "nav.workspaces", to: "/workspaces", end: true },
  { labelKey: "nav.repos", to: "/repos", end: true },
  { labelKey: "nav.scan", to: "/scan/new" },
  { labelKey: "nav.settings", to: "/settings" },
];

export function TopBar() {
  const { t } = useTranslation();
  return (
    <header className="border-b border-border bg-card">
      <div className="mx-auto flex h-12 max-w-[1400px] items-center gap-6 px-7">
        <Link to="/" className="flex items-center gap-1.5 font-semibold tracking-tight text-base">
          <span className="text-cyan">⬡</span>
          <span>Shannon</span>
        </Link>
        <nav className="flex items-center gap-1" aria-label={t("nav.mainAria")}>
          {NAV.map((n) =>
            n.disabled ? (
              <span
                key={n.labelKey}
                aria-disabled="true"
                className="cursor-not-allowed border-b-2 border-transparent px-3 py-1.5 text-sm text-muted-foreground/50"
              >
                {t(n.labelKey)}
              </span>
            ) : (
              <NavLink key={n.labelKey} to={n.to} end={n.end} className="inline-flex">
                {({ isActive }) => (
                  <span
                    data-active={isActive}
                    className={cn(
                      "border-b-2 px-3 py-1.5 text-sm transition-colors",
                      isActive
                        ? "border-primary text-primary"
                        : "border-transparent text-muted-foreground hover:text-foreground"
                    )}
                  >
                    {t(n.labelKey)}
                  </span>
                )}
              </NavLink>
            )
          )}
        </nav>
        <div className="ml-auto flex items-center gap-1">
          {/* 运行中扫描指示器 slot（子项目 5 接 SSE） */}
          <LanguageSwitcher />
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd packages/web/frontend && npx vitest run src/components/layout/TopBar.test.tsx`
Expected: PASS(含切英文断言)。

- [ ] **Step 6: Commit**

```bash
git add packages/web/frontend/src/components/layout/TopBar.tsx packages/web/frontend/src/components/layout/TopBar.test.tsx
git commit -m "feat(i18n): TopBar 导航文案接入 i18n + 挂载 LanguageSwitcher"
```

---

## Task 4:ReposPage + AddRepoDialog + CloneProgress

**Files:**
- Modify: `src/locales/zh.json`、`src/locales/en.json`(加 `repos` 分组)
- Modify: `src/pages/ReposPage.tsx`
- Modify: `src/components/AddRepoDialog.tsx`
- Modify: `src/components/CloneProgress.tsx`
- Create: `src/pages/ReposPage.test.tsx`(若已存在则追加 i18n 用例)

**Interfaces:**
- Consumes: Task 1 的 `useTranslation`。
- Produces: locale `repos.*` 分组。

- [ ] **Step 1: 扩充 locale —— 在 `zh.json` 顶层加 `repos` 分组**

```json
"repos": {
  "title": "仓库",
  "addRepo": "+ 添加仓库",
  "searchPlaceholder": "搜索仓库名",
  "loading": "加载中…",
  "empty": "暂无仓库。点「+ 添加仓库」clone 一个。",
  "noMatch": "无匹配仓库。",
  "expand": "展开 ▸",
  "collapse": "折叠 ▾",
  "ungrouped": "未分组",
  "updating": "正在更新 {{name}}",
  "updateAria": "更新 {{name}}",
  "deleteAria": "删除 {{name}}",
  "table": { "name": "名称", "source": "来源", "branch": "分支", "size": "大小", "state": "状态", "actions": "操作" },
  "states": { "ready": "✓ 就绪", "failed": "✗ 失败", "stale": "⚠ 未完成", "cloning": "clone 中", "pulling": "pull 中" },
  "deleteDialog": { "title": "删除仓库", "desc": "删除仓库 {{name}}？代码目录永久删除。" },
  "errors": { "loadFailed": "加载仓库列表失败", "inUse": "仓库正被使用", "deleteFailed": "删除失败（{{status}}）", "updateFailed": "更新失败（{{status}}）" }
}
```

- [ ] **Step 2: 在 `en.json` 顶层加对应 `repos` 分组(key 必须一致)**

```json
"repos": {
  "title": "Repositories",
  "addRepo": "+ Add repository",
  "searchPlaceholder": "Search repositories",
  "loading": "Loading…",
  "empty": "No repositories yet. Click \"+ Add repository\" to clone one.",
  "noMatch": "No matching repositories.",
  "expand": "Expand ▸",
  "collapse": "Collapse ▾",
  "ungrouped": "Ungrouped",
  "updating": "Updating {{name}}",
  "updateAria": "Update {{name}}",
  "deleteAria": "Delete {{name}}",
  "table": { "name": "Name", "source": "Source", "branch": "Branch", "size": "Size", "state": "State", "actions": "Actions" },
  "states": { "ready": "✓ Ready", "failed": "✗ Failed", "stale": "⚠ Incomplete", "cloning": "Cloning", "pulling": "Pulling" },
  "deleteDialog": { "title": "Delete repository", "desc": "Delete repository {{name}}? The code directory will be permanently removed." },
  "errors": { "loadFailed": "Failed to load repository list", "inUse": "Repository is in use", "deleteFailed": "Delete failed ({{status}})", "updateFailed": "Update failed ({{status}})" }
}
```

- [ ] **Step 3: 跑 locale 完整性测试确认 key 一致**

Run: `cd packages/web/frontend && npx vitest run src/i18n/locales.test.ts`
Expected: PASS(zh/en repos 分组 key 一致)。

- [ ] **Step 4: 写失败测试 `src/pages/ReposPage.test.tsx`(或追加)**

```tsx
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import i18n from "@/i18n";
import { ReposPage } from "./ReposPage";

vi.mock("@/api/client", () => ({
  listRepos: vi.fn().mockResolvedValue([]),
  deleteRepo: vi.fn(),
  pullRepo: vi.fn(),
  ApiError: class ApiError {},
}));

describe("ReposPage i18n", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  it("中文显示标题与空状态", async () => {
    render(<MemoryRouter><ReposPage /></MemoryRouter>);
    expect(await screen.findByText("仓库")).toBeInTheDocument();
    expect(screen.getByText(/暂无仓库/)).toBeInTheDocument();
  });

  it("切英文后标题变 Repositories", async () => {
    render(<MemoryRouter><ReposPage /></MemoryRouter>);
    await screen.findByText("仓库");
    i18n.changeLanguage("en");
    expect(await screen.findByText("Repositories")).toBeInTheDocument();
  });
});
```

- [ ] **Step 5: 跑测试确认失败**

Run: `cd packages/web/frontend && npx vitest run src/pages/ReposPage.test.tsx`
Expected: FAIL(切英文断言失败,标题仍硬编码「仓库」)。

- [ ] **Step 6: 迁移 `src/pages/ReposPage.tsx`**

逐处替换(参考行号为迁移前):
- 顶部 import 加 `import { useTranslation } from "react-i18next";`
- 组件内 `const { t } = useTranslation();`
- `const UNGROUPED = "未分组";`(L18)删除;`groupRepos(repos)` 改签名加第二参 `ungrouped: string`,函数体 `r.group ?? ungrouped`;调用处 `groupRepos(filtered, t("repos.ungrouped"))`。
- `STATE_BADGE`(L42-48):把 `text` 字段改为 `key`,值改 `repos.states.ready/failed/stale/cloning/pulling`;`StateCell` 内 `const m = STATE_BADGE[repo.state]; ... <Badge>{t(m.key)}</Badge>`。
- L85 `toast.error("加载仓库列表失败")` → `toast.error(t("repos.errors.loadFailed"))`
- L101 三元 → `toast.error(e.status === 409 ? t("repos.errors.inUse") : t("repos.errors.deleteFailed", { status: e.status }))`
- L111 `toast.success(\`正在更新 ${name}\`)` → `toast.success(t("repos.updating", { name }))`
- L115 `toast.error(\`更新失败（${e.status}）\`)` → `toast.error(t("repos.errors.updateFailed", { status: e.status }))`
- L140 `<h1>仓库</h1>` → `<h1>{t("repos.title")}</h1>`
- L145 `placeholder="搜索仓库名"` → `placeholder={t("repos.searchPlaceholder")}`;L147 `aria-label="搜索仓库名"` → `aria-label={t("repos.searchPlaceholder")}`
- L149 `+ 添加仓库` → `{t("repos.addRepo")}`
- L154 `加载中…` → `{t("repos.loading")}`
- L157 三元 → `repos.length === 0 ? t("repos.empty") : t("repos.noMatch")`
- L174 `{isCollapsed ? "展开 ▸" : "折叠 ▾"}` → `{isCollapsed ? t("repos.expand") : t("repos.collapse")}`
- L180-185 表头 → `t("repos.table.name")` / `.source` / `.branch` / `.size` / `.state` / `.actions`
- L231 `aria-label={\`更新 ${r.name}\`}` → `aria-label={t("repos.updateAria", { name: r.name })}`
- L234 `更新` → `{t("common.update")}`
- L240 `aria-label={\`删除 ${r.name}\`}` → `aria-label={t("repos.deleteAria", { name: r.name })}`
- L243 `删除` → `{t("common.delete")}`
- L264 `删除仓库` → `{t("repos.deleteDialog.title")}`
- L265 `删除仓库 {pendingDelete}？代码目录永久删除。` → `{t("repos.deleteDialog.desc", { name: pendingDelete })}`
- L268 `取消` → `{t("common.cancel")}`
- L269 `确认` → `{t("common.confirm")}`
- 更新 L40-41 注释:状态文本已迁移到 i18n,测试断言依赖默认中文渲染(见 spec §11)。

- [ ] **Step 7: 迁移 `src/components/AddRepoDialog.tsx`**

Run: `cd packages/web/frontend && rg -n "[一-鿿]" src/components/AddRepoDialog.tsx`
把扫到的每个硬编码中文(对话框标题、字段 label、toast 错误等)加入 `repos.addDialog.*`(或就近放 `repos.*`),en/zh 都加,替换为 `t()`。组件内加 `const { t } = useTranslation();`。

- [ ] **Step 8: 迁移 `src/components/CloneProgress.tsx`**

Run: `cd packages/web/frontend && rg -n "[一-鿿]" src/components/CloneProgress.tsx`
扫到的中文(`clone 中`/`clone 失败`/`pull 中` 等)加入 `repos.clone.*`,en/zh 都加,替换为 `t()`。

- [ ] **Step 9: 跑 locale 完整性 + ReposPage 测试**

Run: `cd packages/web/frontend && npx vitest run src/i18n/locales.test.ts src/pages/ReposPage.test.tsx`
Expected: PASS。

- [ ] **Step 10: 跑现有相关测试防回归**

Run: `cd packages/web/frontend && npx vitest run src/pages/ReposPage src/components/AddRepoDialog src/components/CloneProgress`
Expected: 已有测试仍绿(默认中文渲染不变)。

- [ ] **Step 11: tsc 验证**

Run: `cd packages/web/frontend && npx tsc -b`
Expected: 无错误(注意 `groupRepos` 签名改动波及的调用处)。

- [ ] **Step 12: Commit**

```bash
git add packages/web/frontend/src/locales packages/web/frontend/src/pages/ReposPage.tsx packages/web/frontend/src/components/AddRepoDialog.tsx packages/web/frontend/src/components/CloneProgress.tsx packages/web/frontend/src/pages/ReposPage.test.tsx
git commit -m "feat(i18n): 仓库页 ReposPage/AddRepoDialog/CloneProgress 文案接入 i18n"
```

---

## Task 5:StatusBadge 状态枚举本地化 + 未知 fallback

**Files:**
- Modify: `src/locales/zh.json`、`src/locales/en.json`(加 `workspaces.status.*`)
- Modify: `src/components/StatusBadge.tsx`
- Create/Modify: `src/components/StatusBadge.test.tsx`

**Interfaces:**
- Produces:`<StatusBadge status="running" />` 经 `t('workspaces.status.running')` 渲染本地化标签;未知 status 原样渲染。

- [ ] **Step 1: 扩充 locale `workspaces.status`**

`zh.json` 加(若已有 `workspaces` 分组则合并):
```json
"workspaces": {
  "status": {
    "running": "运行中",
    "completed": "已完成",
    "done": "已完成",
    "failed": "失败",
    "aborted": "已中止",
    "queued": "排队中",
    "paused": "已暂停"
  }
}
```
`en.json` 对应:
```json
"workspaces": {
  "status": {
    "running": "Running",
    "completed": "Completed",
    "done": "Completed",
    "failed": "Failed",
    "aborted": "Aborted",
    "queued": "Queued",
    "paused": "Paused"
  }
}
```
(实际 status 枚举值以 `api/types.ts` 的 workspace 状态为准;先扫 `rg -n "status" src/components/StatusBadge.tsx` 与 `api/types.ts` 确认全集,补全 key。)

- [ ] **Step 2: 写失败测试 `src/components/StatusBadge.test.tsx`**

```tsx
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import i18n from "@/i18n";
import { StatusBadge } from "./StatusBadge";

describe("StatusBadge i18n", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  it("已知状态中文映射", () => {
    render(<StatusBadge status="running" />);
    expect(screen.getByText("运行中")).toBeInTheDocument();
  });

  it("切英文映射", () => {
    render(<StatusBadge status="running" />);
    i18n.changeLanguage("en");
    expect(screen.getByText("Running")).toBeInTheDocument();
  });

  it("未知状态 fallback 原值不空白", () => {
    render(<StatusBadge status="some-new-state" />);
    expect(screen.getByText("some-new-state")).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd packages/web/frontend && npx vitest run src/components/StatusBadge.test.tsx`
Expected: FAIL(当前直接渲染 `running` 原值,中文断言失败)。

- [ ] **Step 4: 改 `src/components/StatusBadge.tsx`**

先读现有文件确认 props 签名与配色结构:Run `rg -n "." src/components/StatusBadge.tsx`。保留现有 props 签名与配色逻辑,只把渲染文本改为 `t()` + fallback:
```tsx
import { useTranslation } from "react-i18next";
// ... 保留现有 imports / 配色映射

export function StatusBadge(/* 保留现有 props 签名 */) {
  const { t, i18n: i18nInst } = useTranslation();
  const key = `workspaces.status.${status}`;   // status 取自现有 props
  // 未知状态 fallback 渲染原值(防后端新增枚举显示空白)
  const label = i18nInst.exists(key) ? t(key) : status;
  // ... 保留原有 className / 配色 token 逻辑
  return <Badge /* 保留原有 props */>{label}</Badge>;
}
```
(实现时保留组件原有 props 签名与配色 token,只把原本渲染 `{status}` 的位置改为 `{label}`。用从 `useTranslation()` 解构的 `i18nInst.exists(key)` 判存在性以实现未知 fallback。)

- [ ] **Step 5: 跑测试确认通过**

Run: `cd packages/web/frontend && npx vitest run src/components/StatusBadge.test.tsx`
Expected: PASS(含未知 fallback)。

- [ ] **Step 6: 跑 locale 完整性 + 现有 StatusBadge 测试防回归**

Run: `cd packages/web/frontend && npx vitest run src/i18n/locales.test.ts src/components/StatusBadge`
Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add packages/web/frontend/src/locales packages/web/frontend/src/components/StatusBadge.tsx packages/web/frontend/src/components/StatusBadge.test.tsx
git commit -m "feat(i18n): StatusBadge 状态枚举本地化 + 未知值 fallback"
```

---

## Task 6:WorkspaceListPage(消除中英混排)

**Files:**
- Modify: `src/locales/zh.json`、`src/locales/en.json`(扩充 `workspaces.*`)
- Modify: `src/pages/WorkspaceListPage.tsx`

**Interfaces:**
- Consumes: Task 5 的 `workspaces.status.*`;本 task 扩充 `workspaces` 其余 key。

- [ ] **Step 1: 扫描该文件全部中文**

Run: `cd packages/web/frontend && rg -n "[一-鿿]" src/pages/WorkspaceListPage.tsx`
已知文案:表头 `workspace/status/type/vulns/cost/time`(原本英文)、`操作`、`取消`、`删除`、`搜索 workspace...`、`+ 新建扫描`、确认 Dialog 文案。

- [ ] **Step 2: 扩充 locale `workspaces` 分组(zh + en 同 key)**

把扫到的文案组织进 `workspaces.table.*`、`workspaces.actions.*`、`workspaces.searchPlaceholder`、`workspaces.newScan`、`workspaces.deleteDialog.*` 等。表头原本英文的(`workspace/status/type/vulns/cost/time`)也纳入字典(zh 给中文如「工作区/状态/类型/漏洞数/耗时/时间」,en 给 `Workspace/Status/Type/Vulns/Cost/Time`)。zh/en 都写全。

- [ ] **Step 3: 迁移 `src/pages/WorkspaceListPage.tsx`**

组件内 `const { t } = useTranslation();`,把所有硬编码中文/英文表头替换为对应 `t()`。`取消/删除` 用 `common.cancel`/`common.delete`。

- [ ] **Step 4: 跑 locale 完整性 + 该页现有测试**

Run: `cd packages/web/frontend && npx vitest run src/i18n/locales.test.ts src/pages/WorkspaceListPage`
Expected: PASS(注意:原表头是英文,现有测试若断言英文表头,默认仍渲染对应语言文案——需检查现有断言,迁移后 zh 渲染中文表头,可能需要更新断言为 i18n 默认 zh 的值,或按 spec §11 保持)。

- [ ] **Step 5: tsc 验证 + Commit**

Run: `cd packages/web/frontend && npx tsc -b`
```bash
git add packages/web/frontend/src/locales packages/web/frontend/src/pages/WorkspaceListPage.tsx
git commit -m "feat(i18n): WorkspaceListPage 接入 i18n,消除中英混排"
```

---

## Task 7:ScanNewPage + ScanFormFields

**Files:**
- Modify: `src/locales/zh.json`、`src/locales/en.json`(加 `scan.*`)
- Modify: `src/pages/ScanNewPage.tsx`
- Modify: `src/components/ScanFormFields.tsx`

- [ ] **Step 1: 扫描中文**

Run: `cd packages/web/frontend && rg -n "[一-鿿]" src/pages/ScanNewPage.tsx src/components/ScanFormFields.tsx`
(ScanNewPage 文案密度最高,约 316 字。)

- [ ] **Step 2: 扩充 locale `scan.*` 分组(zh + en 同 key)**

按扫到的文案组织:`scan.title`、`scan.fields.*`(仓库选择/扫描类型/vuln 类等)、`scan.options.*`、`scan.submit`、`scan.errors.*` 等。zh/en 都写全。

- [ ] **Step 3: 迁移两个文件**

各组件内 `const { t } = useTranslation();`,硬编码文案替换为 `t()`。共享按钮用 `common.*`。

- [ ] **Step 4: locale 完整性 + 现有测试 + tsc**

Run: `cd packages/web/frontend && npx vitest run src/i18n/locales.test.ts src/pages/ScanNewPage src/components/ScanFormFields && npx tsc -b`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/locales packages/web/frontend/src/pages/ScanNewPage.tsx packages/web/frontend/src/components/ScanFormFields.tsx
git commit -m "feat(i18n): 扫描表单 ScanNewPage/ScanFormFields 接入 i18n"
```

---

## Task 8:DashboardPage + SettingsPage

**Files:**
- Modify: `src/locales/zh.json`、`src/locales/en.json`(加 `dashboard.*`、`settings.*`)
- Modify: `src/pages/DashboardPage.tsx`
- Modify: `src/pages/SettingsPage.tsx`

- [ ] **Step 1: 扫描 + 扩充 locale**

Run: `cd packages/web/frontend && rg -n "[一-鿿]" src/pages/DashboardPage.tsx src/pages/SettingsPage.tsx`
按扫到的文案加 `dashboard.*`(概览卡片标题/统计标签等)、`settings.*`(系统状态只读标签等)。zh/en 同 key。

- [ ] **Step 2: 迁移两个文件**

`const { t } = useTranslation();`,硬编码替换 `t()`。

- [ ] **Step 3: locale 完整性 + 现有测试 + tsc**

Run: `cd packages/web/frontend && npx vitest run src/i18n/locales.test.ts src/pages/DashboardPage src/pages/SettingsPage && npx tsc -b`
Expected: PASS。

- [ ] **Step 4: Commit**

```bash
git add packages/web/frontend/src/locales packages/web/frontend/src/pages/DashboardPage.tsx packages/web/frontend/src/pages/SettingsPage.tsx
git commit -m "feat(i18n): Dashboard/Settings 页接入 i18n"
```

---

## Task 9:RepoDetailPage

**Files:**
- Modify: `src/locales/zh.json`、`src/locales/en.json`(加 `repoDetail.*`)
- Modify: `src/pages/RepoDetailPage.tsx`

- [ ] **Step 1: 扫描 + 扩充 locale**

Run: `cd packages/web/frontend && rg -n "[一-鿿]" src/pages/RepoDetailPage.tsx`
按文案加 `repoDetail.*`(标题/元信息标签/操作按钮等)。zh/en 同 key。

- [ ] **Step 2: 迁移 + 测试 + tsc + Commit**

迁移 `src/pages/RepoDetailPage.tsx`(`const { t } = useTranslation();`)。
Run: `cd packages/web/frontend && npx vitest run src/i18n/locales.test.ts src/pages/RepoDetailPage && npx tsc -b`
Expected: PASS。
```bash
git add packages/web/frontend/src/locales packages/web/frontend/src/pages/RepoDetailPage.tsx
git commit -m "feat(i18n): 仓库详情 RepoDetailPage 接入 i18n"
```

---

## Task 10:WorkspaceDetail 5 个 tab(仅 UI 文案)

**Files:**
- Modify: `src/locales/zh.json`、`src/locales/en.json`(加 `workspaceDetail.*`,下分 `overview/report/deliverables/logs/live`)
- Modify: `src/routes/WorkspaceDetail/OverviewTab.tsx`
- Modify: `src/routes/WorkspaceDetail/ReportTab.tsx`
- Modify: `src/routes/WorkspaceDetail/DeliverablesTab.tsx`
- Modify: `src/routes/WorkspaceDetail/LogsTab.tsx`
- Modify: `src/routes/WorkspaceDetail/LiveTab.tsx`

**边界(重要)**:
- `ReportTab`:只迁移页面 UI 文案(标题/按钮/加载态/空态);**报告正文 Markdown 渲染不动**(LLM 生成的数据)。
- `LogsTab`:只迁移 UI 文案;**日志行内容不动**。
- `LiveTab`:只迁移 UI 文案;**事件流内容不动**。

- [ ] **Step 1: 逐文件扫描中文**

Run: `cd packages/web/frontend && rg -n "[一-鿿]" src/routes/WorkspaceDetail/OverviewTab.tsx src/routes/WorkspaceDetail/ReportTab.tsx src/routes/WorkspaceDetail/DeliverablesTab.tsx src/routes/WorkspaceDetail/LogsTab.tsx src/routes/WorkspaceDetail/LiveTab.tsx`

- [ ] **Step 2: 扩充 locale `workspaceDetail.*`**

按 tab 分组(zh/en 同 key),只包含 UI 文案,不含报告/日志/事件流内容:
```jsonc
"workspaceDetail": {
  "overview": { "...": "..." },
  "report": { "title": "...", "loading": "...", "empty": "..." },
  "deliverables": { "...": "..." },
  "logs": { "title": "...", "empty": "..." },
  "live": { "title": "...", "empty": "..." }
}
```

- [ ] **Step 3: 迁移 5 个 tab 文件**

各文件 `const { t } = useTranslation();`,UI 文案替换 `t()`。**逐一判断**:渲染报告/日志/事件流数据的 `{variable}` 不动;只有静态 UI 文字迁移。

- [ ] **Step 4: locale 完整性 + 现有 tab 测试 + tsc**

Run: `cd packages/web/frontend && npx vitest run src/i18n/locales.test.ts src/routes/WorkspaceDetail && npx tsc -b`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/locales packages/web/frontend/src/routes/WorkspaceDetail
git commit -m "feat(i18n): WorkspaceDetail 5 tab UI 文案接入 i18n(报告/日志/事件流内容不动)"
```

---

## Task 11:MarkdownVulnCard + MarkdownView(仅 UI 模板文案)

**Files:**
- Modify: `src/locales/zh.json`、`src/locales/en.json`(加 `vuln.*` 等 UI 模板 key)
- Modify: `src/components/MarkdownVulnCard.tsx`
- Modify: `src/components/MarkdownView.tsx`

**边界(重要)**:
- 只迁移「UI 模板文案」(如卡片固定标签、加载态、占位提示)。
- **MarkdownView 渲染的报告 Markdown 正文不动**(那是后端 LLM 生成的中文数据)。
- **绝不碰 `src/lib/vuln-block.ts`** 的中文关键词(`开放重定向`/`越权`/`弱口令`...)——它们匹配后端报告,翻译会破坏分类。

- [ ] **Step 1: 扫描两个组件的中文**

Run: `cd packages/web/frontend && rg -n "[一-鿿]" src/components/MarkdownVulnCard.tsx src/components/MarkdownView.tsx`

- [ ] **Step 2: 区分 UI 文案 vs 报告正文渲染**

对每一处中文判断:
- 若是组件自带的静态 UI 文字(标签、按钮、提示)→ 迁移到 locale。
- 若是渲染 `{markdownContent}` / `{report}` / 变量插值的报告内容 → **不动**。
加 locale key(zh/en 同 key),只含 UI 模板文案。

- [ ] **Step 3: 迁移 UI 文案**

各文件 `const { t } = useTranslation();`,只替换 UI 模板文案。

- [ ] **Step 4: 确认未误伤 vuln-block + locale 完整性 + 测试 + tsc**

Run: `cd packages/web/frontend && git diff --stat src/lib/vuln-block.ts`(Expected: 无改动 / 文件未出现在 diff)
Run: `cd packages/web/frontend && npx vitest run src/i18n/locales.test.ts src/components/MarkdownVulnCard src/components/MarkdownView && npx tsc -b`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/locales packages/web/frontend/src/components/MarkdownVulnCard.tsx packages/web/frontend/src/components/MarkdownView.tsx
git commit -m "feat(i18n): MarkdownVulnCard/MarkdownView 仅 UI 模板文案接入 i18n(报告正文/vuln-block 不动)"
```

---

## Task 12:i18next-parser 工具链 + 全量校验

**Files:**
- Create: `i18next-parser.config.js`
- Modify: `package.json`(加 `i18n:scan` script)

- [ ] **Step 1: 安装 i18next-parser(dev)**

Run: `cd packages/web/frontend && npm install -D i18next-parser`

- [ ] **Step 2: 写 `i18next-parser.config.js`**

```js
export default {
  createOldCatalogs: false,
  input: ["src/**/*.{ts,tsx}"],
  output: "src/locales/$LOCALE.json",
  locales: ["zh", "en"],
  defaultNamespace: "translation",
  namespaceSeparator: false,
  keySeparator: ".",
  verbose: true,
  // 只提取、不覆盖已有翻译值
  defaultValue: (lng, ns, key) => (lng === "zh" ? key : ""),
};
```

- [ ] **Step 3: 加 npm script —— 修改 `package.json`**

在 `scripts` 加:
```json
"i18n:scan": "i18next-parser"
```

- [ ] **Step 4: 跑 scan 校验无遗漏**

Run: `cd packages/web/frontend && npm run i18n:scan`
然后检查输出报告:`src/locales/zh.json` 应包含所有已使用的 key,`en.json` 无新增缺失。手动比对 scan 输出与现有 locale,补任何漏掉的 key(注意 `defaultValue` 对 en 返回空串,需人工填翻译)。
跑 locale 完整性测试确认:Run: `cd packages/web/frontend && npx vitest run src/i18n/locales.test.ts`,Expected: PASS。

- [ ] **Step 5: 全量回归(vitest + tsc + build)**

Run: `cd packages/web/frontend && npx vitest run && npx tsc -b && npx vite build`
Expected: 全部 PASS / 构建成功。
(遵循项目约定:只跑改动相关测试;但本 task 为收尾,跑前端全量 vitest 以确认无回归。若遇预存失败,对照 memory「pytest 全量会 hang」的前端版——只关注 i18n 相关与本次改动文件。)

- [ ] **Step 6: 浏览器冒烟(人工,记录结果)**

启动 `cd packages/web/frontend && npm run dev`,浏览器验证:
- 默认按浏览器语言显示。
- TopBar 切换器点 EN → 全站 UI 文案变英文;点 中 → 回中文;刷新保持选择。
- 仓库页/Workspaces/Scan/Settings/仓库详情/WorkspaceDetail 各 tab 中英都正确。
- 报告正文、日志内容、仓库名等数据语言不变。
- 状态徽章中英都正确,无空白。

记录冒烟结果到 commit message 或 memory。

- [ ] **Step 7: Commit**

```bash
git add packages/web/frontend/i18next-parser.config.js packages/web/frontend/package.json packages/web/frontend/package-lock.json packages/web/frontend/src/locales
git commit -m "feat(i18n): i18next-parser 工具链 + 全量校验通过(冒烟待人工)"
```

---

## 验收标准(对照 spec §13)

- [ ] 全站核心页面 zh/en 切换正确(标题/表头/按钮/空状态/确认框/toast/占位符/导航/状态徽章)。
- [ ] 首次按浏览器语言显示,切换后刷新保持。
- [ ] 状态徽章已知值本地化、未知值 fallback 不空白。
- [ ] 报告正文/日志内容/事件流/仓库名等数据语言不变。
- [ ] `lib/vuln-block.ts` 关键词匹配行为不变(回归)。
- [ ] `vitest` + `tsc -b` + `vite build` 通过。
