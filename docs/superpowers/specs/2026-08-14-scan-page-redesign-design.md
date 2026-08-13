# 扫描页重设计 · 设计 spec

> 日期 2026-08-14 · 状态 draft · 方案 A（适度重构）
> 关联简报 `docs/frontend-design/scan-cards-redesign-brief.md` · 视觉参考 `packages/web/frontend/design-preview/scan-cards-preview.html`

## 1. 背景与目标

### 1.1 为什么要重做
当前扫描页（`pages/ScanNewPage.tsx` + `components/ScanFormFields.tsx` 1157 行）是「三个 tab + 单 Card」结构，与已定稿的设计简报（两张镜像姿式卡片 + 统一四段骨架 + 攻击方向签名图形）存在三处结构性差距，并伴随实现债：

- **外壳**：tab 三选 vs 两张姿式卡片（含签名 SVG）。
- **骨架**：白盒收窄 `max-w-2xl`、黑盒满宽 → 切 tab 布局跳动；简报核心体感「切姿式零跳动」拿不到。
- **认证双轨（最痛）**：黑盒走 `RightAuthCore` / `InlineRightEnhance`（含 loginFlow + 存档），白盒组合走 `AuthFields` / `BottomInlineBlock`（**无 loginFlow、不能存档**）；同一套字段两套 UI + 不同功能子集，与多处「两条分支字段映射一致」的注释自相矛盾。

### 1.2 目标
把简报忠实落到真实代码，同时清理阻碍「统一骨架」的实现债。范围聚焦 **UI 层 + 状态收拢**，`buildBody` / payload / 校验语义 / API 全不动。

### 1.3 非目标
- 不改后端 `/scan` 契约、不改 `buildBody` 字段映射。
- 不引入表单库、不引入 SWR（属方案 C，本轮不做）。
- 不动报告页 / 列表页 / live 页。
- 不动 `RerunPreset` 已埋点未通的字段（保留现状，留后续）。

## 2. 设计方向锚定（不可破）

遵循已定稿简报，**不引入新主色、不引入新字体家族、不破双主题**：

- 两张镜像姿式卡片（白盒 INSIDE-OUT / 黑盒 OUTSIDE-IN），含攻击方向签名 SVG。
- 统一四段骨架（主要输入 / 上下文 / 认证 / 进阶），切姿式零跳动。
- coral 主色 `--primary`（深 `15 60% 56%` / 浅 `15 58% 50%`）只用于「选中 / 强调 / 行动」。
- IBM Plex（Serif 标题 / Sans 正文 / Mono 标签·数据·URL）。
- 暖纸张感（低饱和暖底 + hairline 边框 + 柔阴影），深暖炭黑 / 浅暖纸张双主题。
- 不给卡片加 ①② 编号；不让两姿式配置区长得不一样。

签名 SVG path 数据从 `packages/web/frontend/design-preview/scan-cards-preview.html` 逐字复用（不重画）。

## 3. 不变量（硬约束，重构不可破）

| # | 不变量 | 理由 |
|---|---|---|
| INV-1 | `buildBody(type, f, workspace)` 的字段映射与 payload 形状不变 | 后端契约稳定，UI 重构不应碰 |
| INV-2 | 提交 endpoint `/api/scan` 不变；成功导航（`/live` 或 `live/{id}`）不变 | 跳转 / ws 链路稳定 |
| INV-3 | 校验规则**语义**不变（profile / inline 双来源、primary 锁定、http(s) scheme、reuseScanId 黑盒必填） | 安全：放宽校验 = 放过非法提交 |
| INV-4 | 认证 `profile` / `inline` 双来源、HOST `profile` / `url` 双来源保留 | 业务模型 |
| INV-5 | `accounts[0]` 恒为 primary（`lockFirstRow` + append 语义）保留 | CredentialRows 契约 |
| INV-6 | 黑盒 exploitation-only + 必填 `reuseScanId`（复用白盒结果）保留 | 黑盒语义 |
| INV-7 | correlation 仍发 `{type:"correlation", config_yaml}`，功能不丢，只改入口 | 功能保留 |

> 校验逻辑**位置**从渲染期散落收拢到 reducer / selectors 单一真相源（见 §7.4）；**语义**不变。

## 4. 新信息架构

自上而下纵轴：

```
新建扫描 · 从哪一侧接近你的目标？

┌────────姿态区────────┐
│ [姿式卡 白盒 INSIDE-OUT] [姿式卡 黑盒 OUTSIDE-IN]   ← 等宽镜像，选中=coral描边+柔光+右上勾
└──────────────────────┘
┄┄ ⇄ 跨服务关联（默认关） ┄┄                         ← correlation 降级为虚线开关
┌────────统一四段骨架（切姿式只换字段、不变形）────────┐
│ ▎主要输入   [白盒:repo / 黑盒:url]            必填 │
│ ▎上下文     [workspace (+黑盒:reuseScanId)]   必填 │
│ ▎认证       摘要 · 配置登录（默认收起）         可选 │
│ ▎进阶       [白盒:combined开关 / 黑盒:HOST]        │
└──────────────────────────────────────────────────┘
[ ❯ 开始审计 / 开始渗透 ]              ⌘+Enter
```

四段骨架对两姿式**共用同一套 JSX 容器**，仅每段内部字段组件不同 → 切姿式时容器的宽 / 高 / 间距 / 节奏恒定，视觉零跳动（简报核心体感）。

## 5. 四段骨架字段映射

| 段 | 白盒（纯） | 白盒（combined 开） | 黑盒 |
|---|---|---|---|
| **主要输入** | repo（多选标签 + 添加） | repo + 目标 url | 目标 url（等宽 + coral 描边 + 验证链接） |
| **上下文** | workspace | workspace | workspace + reuseScanId（最新带 `LATEST`） |
| **认证**（可选） | 灰态占位「组合扫描未启用」 | AuthEditor（激活） | AuthEditor |
| **进阶** | combined 开关「同时发起黑盒扫描」 | combined 开关 + HostEditor（开关下方） | HostEditor（profile / url） |

要点：
- 白盒 combined 关时，认证段显示灰态占位（骨架仍在，保证零跳动）；combined 开时激活 AuthEditor，且进阶段 combined 开关下方出现 HostEditor（组合扫描需打黑盒靶，HOST 代理必需）。
- 黑盒认证段 + 进阶 HOST 常驻。
- 认证 / 进阶默认折叠为一行摘要 + 「配置登录」展开式（降首屏噪音）。

## 6. 组件结构 + 文件拆分

### 6.1 新文件树
```
pages/ScanNewPage.tsx              外壳：姿式区 + 跨服务开关 + 骨架编排 + 行动条（瘦身后）
components/scan-form/
  PostureCard.tsx                  姿式卡片（含签名 SVG + 选中态）
  CrossServiceToggle.tsx           跨服务关联虚线开关
  ConfigSkeleton.tsx               四段骨架容器（编排四段，保证零跳动）
  Section.tsx                      段通用：coral 竖条眉头 + 必填/可选标 + 折叠
  fields/
    PrimaryInput.tsx               段内：白盒 RepoPicker / 黑盒 TargetUrl
    ContextFields.tsx              段内：workspace (+ 黑盒 reuseScanId)
  AuthEditor.tsx                   ★ 统一认证编辑器（替代双轨，含 loginFlow + 存档）
  HostEditor.tsx                   统一 HOST 编辑器（profile / url）
  scanReducer.ts                   useReducer state / actions / selectors / validators
  signatures.ts                    两姿式签名 SVG path 数据（从 design-preview 复用）
```

### 6.2 关键：AuthEditor 统一（消除双轨）
单一 `AuthEditor`，两种姿式共用，功能完整：
- 来源分段：`inline` / `profile`（两姿式都有，不再分裂）。
- inline：loginType 分段（form / sso / api / basic）+ loginUrl + CredentialRows（primary 锁定）+ **loginFlow textarea**（白盒组合此前缺失，补齐）+ **存为认证档案**（白盒组合此前缺失，补齐）。
- profile：档案列表 + 角色多选。
- header 摘要 + 「配置登录」展开式，**只写一份**（删 `ScanFormFields.tsx:1097-1122` 重复 header + 重复 `hasAuthDraft`）。

**删除清单**：`RightAuthCore`、`InlineRightEnhance`、`ProfileRightSummary`、`SaveAsProfileInline`（并入 AuthEditor）、`BottomInlineBlock`、`BottomProfileBlock`、旧 `AuthFields`。

### 6.3 ScanNewPage 瘦身
原 487 行里的 `buildBody` / validators / 类型定义迁到 `scanReducer.ts`（types 可留 ScanNewPage 或独立 model 文件，plan 阶段定）；ScanNewPage 只剩外壳编排 + 提交。

## 7. 状态模型（useReducer 收拢）

### 7.1 state
```ts
interface ScanFormState {
  posture: "whitebox" | "blackbox";
  workspace: string;            // 收拢进 state（原独立 state，解 §6.4）
  primary: { repo: string; url: string };
  context: { reuseScanId: string };
  auth: AuthFormState;          // 复用现有类型（字段不变）
  host: HostFormState;          // 复用现有类型（字段不变）
  combined: boolean;
  correlation: { enabled: boolean; yaml: string };
}
```

### 7.2 actions
`SET_POSTURE` / `PATCH` / `SET_AUTH` / `SET_HOST` / `TOGGLE_COMBINED` / `TOGGLE_CORRELATION` / `RESET`。

### 7.3 切姿式字段残留（解 §6.5）
`SET_POSTURE` 时清除对目标姿式无意义的字段（白盒→黑盒清 repo；黑盒→白盒清 reuseScanId），**保留**共用草稿（workspace / url / auth / host）。消除切 tab 字段残留困惑。

### 7.4 校验单一真相源（解 §6.6）
validators 收进 `scanReducer.ts` 为纯函数 selectors（`selectAuthError(state)` 等），渲染期（控制 UI 红框 / 提交按钮 disabled）与提交期**共用同一套**；删除 `buildBody` 内二次 host 校验，`buildBody` 信任已校验 state。

### 7.5 correlation 降级（INV-7）
`correlation` 不再是独立 tab / 姿式，而是姿式区下方的虚线开关（`CrossServiceToggle`）。`enabled` 开时展开现有 `YamlEditor`（**复用，不重写**）。提交：`correlation.enabled && !yamlErr` 时发 `{type:"correlation", config_yaml}`。

## 8. 关键交互

1. **点姿式卡即换姿式**：dispatch `SET_POSTURE`，骨架原地换字段、容器不变形（零跳动）。
2. **选中态**：coral 描边 + 柔光浮起 + 右上勾；主按钮文案（开始审计 / 开始渗透）+ 底部提示同步切换。
3. **跨服务关联**默认关，虚线轻量；开则展开 yaml 编辑器。
4. **认证 / 进阶**展开式：默认一行摘要 + 「配置登录」。
5. **黑盒目标 url**：唯一 coral 描边输入框 + 紧邻「验证」。
6. **切姿式清残留**：见 §7.3。

## 9. 痛点 → 解决映射

| 痛点 | 解决 |
|---|---|
| §6.1 认证双轨 | §6.2 单一 AuthEditor，两姿式共用，功能完整 |
| §6.2 档案重复拉取 | 本轮提升到 ScanNewPage 一次拉取传下（不引入 SWR，留方案 C） |
| §6.3 文件过大（1157 行） | §6.1 拆分为 scan-form/ 多文件 |
| §6.4 状态分散 + 12-prop drilling | §7 useReducer 收拢 workspace，prop 大幅减少 |
| §6.5 切 tab 字段残留 | §7.3 SET_POSTURE 清无关字段 |
| §6.6 双重 host 校验 | §7.4 单一 selectors 真相源，删 buildBody 二次校验 |
| §6.7 RerunPreset 文档漂移 | 本轮不动（非目标，留后续） |
| 切 tab 布局跳动 | §4 / §5 统一骨架共用 JSX，零跳动 |

## 10. 迁移策略

1. 新建 `components/scan-form/` + `scanReducer.ts`（先写 state / validators，配单测）。
2. 逐个新建组件（PostureCard / ConfigSkeleton / Section / AuthEditor / HostEditor / fields）。
3. ScanNewPage 切到新骨架（保持 `buildBody` 调用签名不变）。
4. 删除旧 `ScanFormFields.tsx` 双轨子组件 + 旧 `AuthFields`。
5. 回归：提交 payload 逐字段对比（白盒纯 / 白盒组合 / 黑盒 / correlation 四态）确保与旧版一致。
6. 新文案纳入 i18n：所有用户可见文案走 `t()` key，同步 `zh.json` / `en.json`（本项目有 zh 值漏翻致部分仍英文的踩坑史，locale 测试只校验 key 不校验值语义，需人工核对译文）。

## 11. 测试策略

- **单元**：scanReducer（`SET_POSTURE` 清残留、validators 各分支、combined 开关效应）。
- **契约回归**：四态 `buildBody` payload 快照对比旧实现（守 INV-1）。
- **组件**：AuthEditor 两姿式渲染一致（loginFlow / 存档都在）、切姿式零跳动（骨架容器尺寸断言）。
- **不动**：现有测试中 `test_run_scan_rerun` 等预存失败勿误修。

## 12. 风险与权衡

| 风险 | 缓解 |
|---|---|
| buildBody 误改破坏后端契约 | INV-1 + payload 快照回归 |
| 双轨统一遗漏某姿式字段子集 | AuthEditor 单测覆盖两姿式全字段 |
| 签名 SVG 复用失真 | 逐字复用 design-preview path，不重画 |
| 切姿式清残留误删用户草稿 | 只清「对目标姿式无意义」字段，共用草稿保留 |

---

## 一句话总纲
两张姿式卡片取代 tab、单一 AuthEditor 消灭认证双轨、useReducer 收拢状态；UI 层与状态重构，`buildBody` / payload / 校验语义 / 双来源模式全锁死不动。
