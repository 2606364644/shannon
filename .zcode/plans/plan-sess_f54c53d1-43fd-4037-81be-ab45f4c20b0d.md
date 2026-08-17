# 第一步：先做 HTML 预览给你看（不改任何应用代码）

你要求先看预览。计划模式现在禁止我写文件，所以需要你批准这一步，我才能生成预览。

## 现在要做的（唯一动作）
创建一个**独立 HTML 预览文件**：`packages/web/frontend/design-preview/dashboard-redesign-preview.html`
- 沿用项目既有的 `design-preview/` 约定（与 `redesign-mockup.html` 等同源）。
- 内联镜像现有 DSF token（珊瑚色 `--primary`、暖色背景、`--c-red`/`--c-cyan`）、IBM Plex 字体。
- 完整呈现「带威胁信号 Hero 的操作台」设计：Hero（source→sink 图形 + 大号 Mono 发现数 + 实时信号轨迹 + 新建扫描 CTA）→ 安静的运营指标条 → 进行中卡片（青色脉动点 + 进度）→ 筛选 → 漏洞列着色的表格。
- 右下角**预览控制面板**可切换：主题（深/浅）、场景（**猎杀中 / 一切正常 / 空状态**），便于你评估所有状态与双主题。
- 完全独立、零依赖，浏览器直接打开即可。**不触碰任何应用源码。**

## 看完预览后再决定（第二步，届时再执行）
你确认视觉方向后，我再落地到真实组件：`DashboardPage.tsx` 重构 + `DashboardHero/SignalTrace/LiveScanCard` 小组件 + `index.css` 信号动画 + 少量 i18n 文案。数据获取、筛选、管理员取消、链接、空状态全部保留。

> 批准后我立即生成预览文件并告诉你打开路径。如需调整方向，看完预览再提。