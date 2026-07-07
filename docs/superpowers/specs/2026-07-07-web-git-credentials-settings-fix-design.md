# Spec: web 容器 git 凭据注入 + settings 语义修复

- 日期:2026-07-07
- 分支:`feat/fork-py`
- 状态:设计已确认,待写实现计划

## 1. 背景与问题

用户用 `docker compose up --build` 单容器部署 web(`:7878`),想用扫描表单的 **git URL 模式**(来源类型选「git URL」,填仓库地址让系统自动 clone)。但 settings 页显示「Git: 不可用」,且一开始怀疑「容器里没有 git」、并设想「让容器改用主机上的 git、clone 到挂载目录」。

经代码探查,真实根因是**两个互相独立的小缺口**,与「主机 git vs 容器 git」无关:

### 缺口 A:git URL 模式的 GitLab 凭据进不了 web 容器

- `packages/web/src/shannon_web/config.py:13-14` 用 `os.environ.get("GITLAB_USER")` / `os.environ.get("GITLAB_TOKEN")` 读凭据——读的是**进程环境变量**,不是 `.env` 文件内容。
- web 进程**不调用 `load_dotenv`**(`packages/core/src/shannon_core/config/env_loader.py` 的 `load_env` 只给 core CLI 子进程用,web 包的导入链不碰它)。
- `docker-compose.yml` 的 web 服务只有 `environment:` 段(4 个变量:`SHANNON_WEB_MAX_CONCURRENT` / `SHANNON_WEB_SCAN_TIMEOUT` / `SHANNON_TEMPORAL_HOST` / `SHANNON_TEMPORAL_PORT`,**没列 GITLAB_**),另有 volume `./.env:/app/.env:ro`——但 **volume 挂载文件 ≠ 把变量注入容器环境**。
- 结论:即使 `.env` 写了 `GITLAB_USER/GITLAB_TOKEN`,web 容器的 `os.environ` 里也没有,`GitFetcher.available()`(`config.py:28-29`)恒为 `False`,`scan_manager.py:131` 抛 `PermissionError("git 模式不可用:缺少 GitLab 凭证")`。

> 注:`.env`(根目录)已存在且已被 `.gitignore:17` 忽略;`.env.example` 是 tracked 模板。两者**目前都不含 `GITLAB_*`**(模板从未文档化这两个变量)。

### 缺口 B:settings 页「Git 可用/不可用」文案有歧义

- `config.py:28-29` 的 `git_available` 实际是**凭据检测**:`bool(self.gitlab_user and self.gitlab_token)`。
- `api/system_status.py:50` 与 `app.py:82`(`/health`)都原样返回 `git_available`。
- 前端 `SettingsPage.tsx:57-58` 显示成「Git: 可用 / 不可用」,字面意思是「git 能不能用」,极易被读成「容器里装没装 git」——本次用户正是这样误判的。容器里**确实装了 git**(`packages/web/Dockerfile:14-17` 构建时 `apt-get install -y git ca-certificates`)。

### 已澄清的非问题(避免重复设计)

用户最初设想「主机上 git clone、容器只管扫挂载目录」——经确认该能力**已由扫描表单的「本地路径」模式 + bind mount 完整覆盖**(`models.py:8-21` 的 `PathSource`,`scan_manager.py:129-137` 对 `kind=="path"` 直接当本地路径扫,`./repos:/app/repos` 已把主机 `./repos` 映射进容器)。本 spec **不涉及**这条路径的任何改动。

## 2. 目标与非目标

**目标**

- **A.凭据可达**:git URL 模式填的 GitLab 凭据能真正进入 web 容器环境,`GitFetcher` 可用,git 模式扫描能跑通。
- **B.语义清晰**:settings 页清晰区分两个独立信号——「git 二进制已装」与「GitLab 凭据已配置」,消除「容器没 git」误判;并标明凭据仅 git URL 模式需要。
- **C.文档同步**:README 与 `.env.example` 文档化 `GITLAB_*` 变量。

**非目标(YAGNI)**

- 不改「本地路径」扫描模式(已可用,见 §1 末)。
- 不让容器改用主机 git、不引入「主机预 clone」的架构改动(不必要)。
- 不动 `docs/configuration.md`(主题是 YAML 扫描配置,与环境变量无关)。
- 不扩展 `GitFetcher` 的认证方式——当前仅支持 `https://{user}:{token}@` 注入(`git_fetcher.py:29-30`),不支持 ssh / 非 https 仓库;本次不扩。
- 不改 core `env_loader.py` 的 profile 加载逻辑。
- 不把 `GITLAB_*` 迁入 `.env.profiles/`(它们是 git 来源凭据、与引擎 profile 正交,放共享 `.env` 即可)。

## 3. 方案决策与取舍

**A 用 compose `env_file` 注入(而非 web 加 `load_dotenv`)。** 两个候选:

| 候选 | 改动 | 取舍 |
|---|---|---|
| **A1(选):`docker-compose.yml` web 加 `env_file: .env`** | 1 行 compose | compose 原生机制,标准做法;web 代码零改;与现有 `environment:` 段互补(后者管 SHANNON_TEMPORAL_HOST 等,前者补 GITLAB_* 等) |
| A2:web `app.py`/`config.py` 加 `load_dotenv("/app/.env")` | 改 web 代码 + 加 `python-dotenv` 依赖到 web 包 | 把「读 env」职责塞进 web,与 core `env_loader` 重复;web 包目前不依赖 dotenv |

选 A1:零代码、最小侵入、符合 compose 语义。`env_file: .env` 会把根 `.env` 的**所有**变量(`GITLAB_*`、`SHANNON_PROFILE`、共享项)注入 web 容器 `os.environ`;`./.env:/app/.env:ro` 文件挂载保留(core CLI 子进程仍能 `load_env("/app/.env")`)。`.env.profiles/` 不受影响(仍由 core CLI 的 `load_env` 按 `SHANNON_PROFILE` 加载)。

**B 拆成两个独立信号(而非仅改文案)。** 候选:

| 候选 | 改动 | 取舍 |
|---|---|---|
| B1(仅改文案):前端把「Git」改成「GitLab 凭据」 | 1 行前端 | 最小,但 `/health` 与 `system_status` 仍只暴露凭据信号,看不出「git 二进制在不在」 |
| **B2(选):后端拆 `git_binary_available` + `credentials_configured`,前端两行** | config + 2 个 API + 前端 + 测试 | 彻底消除歧义,两个信号各自可观测;改动仍小 |

选 B2:这次误判正是「只看凭据信号、误以为是二进制信号」所致,拆开才能根治。

**字段命名:用结构化 `git: {binary_available, credentials_configured}` 取代扁平 `git_available`。** web `/health` 与 `/api/system-status` 无外部消费者(web 容器无 healthcheck,`/health` 仅信息端点),可直接重构,不做向后兼容保留——保持单一真相,避免旧字段与新结构并存产生新歧义。

## 4. 文件布局与改动清单

```
docker-compose.yml                                    # [A] web 服务加 env_file: .env
.env                                                  # [A] 加 GITLAB_USER/GITLAB_TOKEN(用户填,已 ignored)
.env.example                                          # [A][C] 加注释占位模板(tracked)
packages/web/src/shannon_web/config.py                # [B] 加 git_binary_available 属性
packages/web/src/shannon_web/api/system_status.py     # [B] 返回 git.{binary_available,credentials_configured}
packages/web/src/shannon_web/app.py                   # [B] /health 同步新结构
packages/web/frontend/src/api/systemStatus.ts         # [B] SystemStatus 类型:git_available → git 嵌套
packages/web/frontend/src/api/systemStatus.test.ts    # [B] 同步类型/fixture
packages/web/frontend/src/pages/SettingsPage.tsx      # [B] 两行显示 + 凭据行提示
packages/web/frontend/src/pages/SettingsPage.test.tsx # [B] 断言新结构
README.md                                             # [C] .env 配置段补 GITLAB_* 说明
(docs/getting-started.md)                             # [C] 可选,轻量补一句
```

### A.凭据注入

**`docker-compose.yml`** web 服务,在 `environment:` 同级加:
```yaml
    env_file:
      - .env
```
(保留现有 `environment:` 段与 `./.env:/app/.env:ro` volume 不动。)

**`.env`** 末尾加(用户填真实值):
```
# git URL 扫描模式凭据(本地路径模式不需要);仅 https + user:token 注入,不支持 ssh
GITLAB_USER=
GITLAB_TOKEN=
```

**`.env.example`** 在共享配置段补同样两行(占位 + 注释,无真实值)。

### B.settings 语义拆分

**`config.py`**:新增
```python
import shutil  # 顶部
@property
def git_binary_available(self) -> bool:
    return shutil.which("git") is not None
```
`gitlab_user` / `gitlab_token` 保留;删除 `git_available` 属性(由 system_status 显式组合两信号取代)。

**`api/system_status.py`**:把 `"git_available": cfg.git_available` 改为
```python
"git": {
    "binary_available": cfg.git_binary_available,
    "credentials_configured": bool(cfg.gitlab_user and cfg.gitlab_token),
},
```

**`app.py`** `/health`:同步改为 `"git": {"binary_available": ..., "credentials_configured": ...}`,与 `/api/system-status` **共用同一结构**(不简化为 `{"status":"ok"}`——避免两端点结构分叉产生特例)。

**`SettingsPage.tsx`**:把
```tsx
<dt className="text-muted-foreground">Git</dt>
<dd>{data.git_available ? "可用" : "不可用"}</dd>
```
改为两行:
```tsx
<dt className="text-muted-foreground">git 二进制</dt>
<dd>{data.git.binary_available ? "已装" : "缺失"}</dd>
<dt className="text-muted-foreground">GitLab 凭据</dt>
<dd>{data.git.credentials_configured ? "已配置" : "未配置(仅 git URL 模式需要,本地路径模式无需)"}</dd>
```
`src/api/systemStatus.ts` 的 `SystemStatus` interface(行 15 `git_available: boolean`)改为嵌套类型:
```ts
git: { binary_available: boolean; credentials_configured: boolean };
```
`src/api/systemStatus.test.ts` 的 fixture 同步。

### C.文档

**`README.md`** `.env` 配置段(行 37-45 附近)补一小段:
> git URL 扫描模式(表单来源选「git URL」)需要 GitLab 凭据,在根 `.env` 配 `GITLAB_USER` / `GITLAB_TOKEN`(仅支持 https + user:token,不支持 ssh)。来源选「本地路径」时无需凭据。

**`docs/getting-started.md`**:可选,在 docker 部署段轻量补一句指向 README。

## 5. 测试策略

- **`config.py`**:`WebConfig.git_binary_available` 在装有 git 的环境为 `True`(单测 monkeypatch `shutil.which`)。
- **`system_status` API**:`/api/system-status` 返回 `git.binary_available` 与 `git.credentials_configured` 两个独立布尔;凭据项随 `GITLAB_USER/TOKEN` 环境变量变化(有现成 API 测试则同步,无则在 plan 阶段定位/补)。
- **`api/systemStatus.test.ts`**:fixture 改为含 `git: {binary_available, credentials_configured}`(替代旧 `git_available`)。
- **`SettingsPage.test.tsx`**:mock 的 `systemStatus` 数据结构改为含 `git: {binary_available, credentials_configured}`;断言两行文案分别渲染;现有「主题」断言不变。
- **回归**:`scan_manager` 的 git 模式路径(`_resolve_inputs` 调 `GitFetcher`)行为不变,无需新增端到端测试(凭据经 env_file 注入属部署配置,真机冒烟覆盖)。

## 6. 风险与回滚

- **`env_file` 要求 `.env` 存在**:compose `env_file` 默认 required,文件缺失会启动报错。本仓 `.env` 已存在(且 ignored),风险低;若未来有人在无 `.env` 环境跑 compose,需先 `cp .env.example .env`——README 已有此说明(行 42)。
- **`env_file` 注入全部根 `.env` 变量**:web 容器 `os.environ` 会多出 `SHANNON_PROFILE`、`SHANNON_BROWSER_ENGINE` 等共享项。web 不消费这些(无害);core CLI 子进程仍由自己的 `load_env` 加载 profile,父子环境不冲突。
- **`/health` 与 `/api/system-status` 契约变更**:无外部消费者(web 无 healthcheck),前端同步改即可。回滚=git revert 本次提交。
- **`shutil.which("git")` 在容器内恒 `True`**:Dockerfile 已装 git;该信号主要价值是在「裸机/非容器跑 web」场景下可观测,容器内恒 `True` 符合预期,不构成问题。

## 7. 实现顺序建议(供 writing-plans 细化)

1. C 文档(`.env.example` + README)——先行,因 A 依赖 `.env` 有内容
2. A(`docker-compose.yml` + `.env` 加变量)——核心修复
3. B(`config.py` → `system_status.py` → `app.py` → 前端 + 测试)——语义修复
4. 手动冒烟:`docker compose up --build` → settings 页见「git 二进制:已装」+「GitLab 凭据:已配置」→ git URL 模式扫描跑通
