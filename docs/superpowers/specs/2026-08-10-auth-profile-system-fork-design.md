# 系统认证档案「复制到工作区」(fork) 设计

> 日期: 2026-08-10 ｜ 分支: feat/fork-py ｜ 主题: 让全局只读系统档案可在工作区 fork 成可编辑副本
> 上游 spec: `specs/2026-08-06-auth-profile-system-seed-design.md`(系统档案 seed + 只读不变量；本文为**增量**，不破坏其任何不变量)

## 1. 背景与动机

系统认证档案(`workspaces/.system/auth-profiles.yaml`，由 `configs/*.yaml` 启动 seed 产出)全局共享、**只读**。原 spec §7 决策: configs 是唯一真相源，改 configs + 重启。前端表现为 ws 认证管理页(`/p/:ws/auth-profiles`)的系统档案行带只读徽章 + 隐藏 Edit/Delete。

痛点: 用户在 ws 认证页看到 futunn/moomoo 等系统档案，想调整(改密码 / 登录流程 / 加角色)却改不了，只能改 configs 文件 + 重启容器，流程重。

目标: 保留系统档案只读不变量(原型仍以 configs 为源、全局共享、重启 seed 不受影响)，新增一条「复制到工作区」出口 —— 用户在任意 ws 把系统档案 **按需 fork** 成该 ws 的可编辑副本。

不做「启动时给每个 ws 各 copy 一份」(原用户诉求): 会产生 N ws × M 档案的副本重复 + 副本间分叉(正是上游 spec §7 排除的理由)。按需 fork 只在用户需要的 ws 生成副本，零冗余。

## 2. 核心设计: 同 id fork + ws-priority 覆盖

fork 副本**保留系统档案的原 `profile.id`**，落到目标 ws 段。现有不变量 `get(ws, id)`: ws 段优先、miss 回落 system —— 天然变成「ws 副本覆盖系统原型」:

- fork 后，当前 ws 看到可编辑副本(scope=workspace)；其他 ws 仍看系统只读原型。
- 删掉 ws 副本 → `get` 回落 system → 回到只读原型视图(自然撤销 fork)。
- scan 选该档案 → `store.get` ws-priority 命中副本 → 用(可能改过的)副本凭据。

这是纯增量，不动原 spec「系统档案只读」不变量: 系统原型仍 403 拒 PUT/DELETE，seed 仍只写 `.system`。

### 2.1 不变量(增量)

- `read(ws)` 按 `profile.id` 去重: ws 段优先，system 段排除已被 ws 同 id 覆盖的(fork 后当前 ws 列表不重复显示同 id 两份)。
- `get(ws, id)` ws-priority 语义不变。
- fork 副本 = ws 段一份 scope=workspace 档案；`profile.id` = 系统 `profile.id`；`credential.id` 重新生成；`verify_status` 重置 `unverified`。
- fork 端点仅接受 `scope==system` 的 pid；ws 段已有同 id → 409(防覆盖用户已编辑的副本)。

## 3. 详细设计

### 3.1 store: `read(ws)` 按 id 去重(`auth_profile_store.py:160`)

当前 `read(ws)` 是 ws 段 + system 段纯拼接不去重(`:164-167`)，fork 后同 id 会显示两份。改为 ws 段优先、排除 system 段被同 id 覆盖的:

```python
def read(self, ws: str) -> list[AuthProfile]:
    ws_profiles = self._read_segment(ws)
    if ws == SYSTEM_WS:
        return ws_profiles
    ws_ids = {p.id for p in ws_profiles}
    return ws_profiles + [p for p in self._read_segment(SYSTEM_WS) if p.id not in ws_ids]
```

影响面(均为安全):
- `get`(已 ws-priority 取第一个) → 不变。
- `read_masked` / `list_profiles` API → 受益(fork 后不重复)。
- `scan_manager` 的 `store.get` → 不变。
- `create_profile` 的 ws 内 name 唯一检查(`api/auth_profiles.py:44` 走 `store.read(ws)`) → 去重后 fork 副本覆盖 system 同 id；按 name 检查时 fork 过的同名 ws 副本占用 name，合理。

### 3.2 store: 新增 `fork_from_system`

```python
class AlreadyForked(Exception): ...

def fork_from_system(self, ws: str, profile_id: str) -> AuthProfile | None:
    """把 .system 段的系统档案 fork 成 ws 段的可编辑副本。
    返回 fork 后的 ws 副本；系统段无该 id → None；ws 段已有同 id → raise AlreadyForked。"""
    sys_profile = next((p for p in self._read_segment(SYSTEM_WS) if p.id == profile_id), None)
    if sys_profile is None:
        return None
    if any(p.id == profile_id for p in self._read_segment(ws)):
        raise AlreadyForked(profile_id)
    forked = sys_profile.model_copy(deep=True)  # 明文凭据(系统段读时已解密)
    forked.scope = "workspace"
    forked.created_at = None
    forked.updated_at = None
    for c in forked.credentials:
        c.id = ""                       # 重新生成 cred id(独立实体)
        c.verify_status = VerifyStatus()  # 重置 unverified
    # profile.id 保留系统的 → ws-priority 覆盖;upsert_profile 不会重填非空 id
    return self.upsert_profile(ws, forked)
```

要点:
- `_read_segment(SYSTEM_WS)` 读的是**明文**(解密后)，`model_copy(deep=True)` 复制明文凭据，`upsert_profile` → `write` 重新加密落盘到 ws。✓
- `profile.id` 非空 → `upsert_profile` 保留(`:203` 只在 `not profile.id` 时生成)。
- `credential.id` 清空 → `upsert_profile` 重新生成(`:208-210`)。
- `verify_status` 重置: fork 意在「改」，旧验证态不可信，改完凭据需重新测试登录。

### 3.3 API: `POST /{ws}/auth-profiles/{pid}/fork`(`api/auth_profiles.py`)

```python
@router.post("/{ws}/auth-profiles/{pid}/fork")
async def fork_profile(ws, pid, request, user=Depends(workspace_manager)):
    store = _store(request)
    existing = store.get(ws, pid)
    if existing is None:
        raise HTTPException(404, "认证档案不存在")
    if existing.scope != "system":
        raise HTTPException(422, "该档案已在工作区，可直接编辑")
    try:
        forked = store.fork_from_system(ws, pid)
    except AlreadyForked:
        raise HTTPException(409, "已复制到本工作区")
    if forked is None:
        raise HTTPException(404, "系统档案不存在")
    return next(m for m in store.read_masked(ws) if m.id == forked.id).model_dump(mode="json")
```

- 鉴权 `workspace_manager`(创建 ws 副本属改操作，对齐 create/update/delete)。
- 仅接受 `scope==system` pid(ws 档案无需 fork → 422)。
- 重复 fork → 409。
- 返回 fork 后 ws 副本(masked，前端刷新展示)。

### 3.4 前端

`AuthProfilesPage.tsx`(系统行 `p.scope === "system"`，当前 `:93-102` 隐藏 Edit/Delete 处):
- 系统行加「复制到工作区」按钮(Copy 图标，`workspace_manager` 可见)。
- 点击 → `forkProfile(ws, pid)` → 成功刷新列表(该行变 scope=workspace: 徽章消失、Edit/Delete 出现、复制按钮消失) → toast 成功。
- 错误: 409 → toast「已复制到本工作区」；其他 → toast。

`api/authProfiles.ts`: 加 `forkProfile(ws, pid)` → `POST /{ws}/auth-profiles/{pid}/fork`。

i18n(`locales/{en,zh}.json` 的 `authProfiles`): 加 `forkLabel` / `forkSuccess` / `forkAlready` / `forkFailed` 文案。

### 3.5 worker / seed: 零改

- worker 读明文 `scan-config.yaml`，与档案来源无关。
- `seed_from_config` 仍 seed 到 `.system` 只读；fork 副本在 ws 段独立，重 seed 不受影响(系统段重 seed 跳过已存在，不动 ws 段)。

## 4. 测试策略(TDD)

### 4.1 store(`test_auth_profile_store.py`)
- `fork_from_system`: 系统档案 → ws 副本(`profile.id` 同系统、`credential.id` 新、`verify_status=unverified`、`scope=workspace`、凭据明文相等)。
- 已 fork 再 fork → `AlreadyForked`。
- 系统段无该 id → None。
- `read(ws)` 去重: fork 后当前 ws 列表该 id 只出现一次(ws 副本)，不重复 system 原型；其他 system 档案仍可见。
- `get(ws, id)` fork 后返回 ws 副本(ws-priority 不破)。
- `delete_profile(ws, pid)` fork 副本后 → ws 段无该 id → `read` 回到 system 原型(撤销 fork)。
- fork 副本改凭据 + set_verify_status → 写回 ws 段，不动 `.system`。

### 4.2 API(`test_api_auth_profiles.py`)
- `POST .../fork` 系统 pid → 200 返回 ws 副本。
- ws 档案 pid fork → 422。
- 重复 fork → 409。
- 不存在 pid → 404。
- 鉴权: workspace_manager 通过、workspace_member 拒(403)。

### 4.3 前端(`AuthProfilesPage.test.tsx`)
- 系统行渲染「复制到工作区」按钮、ws 行不渲染。
- 点击 → 调 `forkProfile` → 列表刷新、该行变可编辑。
- 409 → toast 已复制。

## 5. 端到端验证(真机)

前提: rebuild web 镜像(后端 + 前端)；worker 零改；configs/ 已挂载。

1. 启动 → log `Seeded N system auth profile(s)` → 任意 ws 认证页见 futunn/moomoo(系统徽章、只读、复制按钮)。
2. 点 futunn「复制到工作区」→ 该行变可编辑(无徽章、Edit/Delete)；ws2 仍见系统只读 futunn。
3. 编辑 fork 副本凭据 + 测试登录 → verify_status 写回 ws 段，`.system` 不动。
4. PUT/DELETE 系统 futunn 原型 → 仍 403(只读不变量不破)。
5. 删 fork 副本 → 回到系统只读视图。
6. 选 fork 副本发黑盒扫描 → scan-config.yaml 用副本凭据。

## 6. 不做(明确排除)

- **不加 fork 来源标记**(「源自系统」徽章 / `forked_from` 元数据): fork 后外观等同新建 ws 档案；是否区分是 polish，MVP 不做。
- **不自动同步**系统档案更新到 ws 副本: ws 副本独立；想更新就删副本重新 fork。
- **不改编辑形态**: 沿用 `AuthProfileDialog` 结构化表单；认证档案的「yaml 文本编辑」是独立话题(对比 `2026-08-10-ws-config-env-textarea-design.md` 的 ws config env 文本区)，不混入本次。
- **不改 seed 逻辑 / 不改 worker / 不改原 spec 只读不变量**。

## 7. 待 plan 确认项

无重大待确认项(两个设计决策已闭合: fork 后 verify_status 重置 unverified、MVP 不加来源标记)。plan 阶段细化 `AlreadyForked` 异常的落点(store 模块级异常 vs 复用 ValueError)、以及前端按钮的图标/位置最终形态。
