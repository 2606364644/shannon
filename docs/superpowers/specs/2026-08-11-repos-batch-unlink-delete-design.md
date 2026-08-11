# 仓库批量取消关联 / 删除 设计

> 日期: 2026-08-11 ｜ 分支: feat/fork-py ｜ 主题: 工作区仓库管理支持批量「删除选中」（关联仓自动取消关联、私有克隆物理删除）
> 范围: 仅 web 前后端（`packages/web`），worker / 扫描引擎零改

## 1. 背景与动机

仓库管理是工作区内的一个 tab（`packages/web/frontend/src/routes/WorkspaceDetail/ReposTab.tsx`，349 行），按 `Repo.group` 分组折叠展示。现有删除是**单条**：

- 前端按钮 `ReposTab.tsx:306-316` → `setPendingDelete(r.name)`（`pendingDelete: string | null`，`:103`，单值）→ `doDelete()`（`:125-143`）→ `deleteRepo(workspace, name)`（`api/client.ts:124-125`）。
- 后端单端点 `DELETE /api/workspaces/{ws}/repos/{name:path}`（`api/repos.py:116-129`）→ `RepoManager.delete`（`repo_manager.py:416-426`），**按 `linked` 自动分叉**：关联仓 → `unlink_repo`（仅删 `linked_repos.json` 记录，不动源文件，`repo_manager.py:471-479`）；私有克隆 → `shutil.rmtree` 仓库目录。删除 vs 取消关联在 API 上本就是**同一个动作**，前端仅按 `r.linked` 切图标/文案（`ReposTab.tsx:313-316`）。
- 两道 409 保护：仓库正在 clone/pull（`_jobs`，`repo_manager.py:417-418`）；仓库正被在跑 scan 引用（`repos.py:123-124` → `ScanManager.active_repo_sources()`，`scan_manager.py:109-119`）。路径穿越校验完备（`_validate_ws_segment` / `_validate_repo_name` / `_resolve_repo_dir`，`repo_manager.py:29-66`）。

痛点：清理一批测试仓库 / 迁移时只能逐条点删除 + 逐条确认，N 个仓库 = N 次往返 + N 次确认，繁琐。前端**无任何多选基建**（`ReposTab.tsx` grep `checkbox|selected|bulk` 零命中），后端**无任何批量端点**（全仓 `batch|bulk|delete_many` 零命中）。

目标：新增「删除选中」批量动作 —— 用户多选仓库（含分组全选 / 全选），一键提交；后端按 `linked` 自动分叉（关联仓取消关联、私有克隆物理删），部分被占用则跳过并反馈。不破坏现有单条删除语义与不变量。

## 2. 核心设计：后端批量端点 + 前端多选

### 2.1 已定决策

1. **统一「删除选中」按钮**：后端按 `linked` 自动分叉，确认框分别说明「取消关联 N / 删除 M」。不拆成两个独立动作（贴合现有单条语义，UX 最简）。
2. **部分成功 + skipped 列表**：能删的删，被 scan 引用 / clone-pull 忙碌 / 不存在的跳过，返回 `skipped:[{name,reason}]` 并在 UI 反馈。对齐已有 `link-dir` 批量关联端点（`repos.py:53-61` → `repo_manager.py:488-529`）的 `imported/skipped` 结果收集模式。
3. **不级联历史 scan**：与单条删除一致，仅挡住正在跑的 scan（409 门），历史 scan 记录里残留的 `source_repo`/`repo_path` 引用原样保留。
4. **API 形态**：新增后端批量端点（而非前端循环单条）—— 一次往返、引用门一次性快照（一致）、skipped 结构化、避开前端并发 `rmtree` 竞争（同 ws 并发 delete 同一仓存在 `shutil.rmtree(ignore_errors=False)` 对已删目录抛的竞争窗口）。

### 2.2 不变量（增量，不破坏现有）

- 单条 `DELETE /{ws}/repos/{name:path}` 语义、签名、不变量**零改**。
- 批量端点逐项复用 `RepoManager.delete`（同一分叉逻辑、同一 `_jobs` 忙碌判定、同一路径校验），不另起删除路径。
- 「关联仓仅取消关联、绝不删源文件」不变量延续：批量内 linked 项走 `unlink_repo`，私有克隆才 `rmtree`。
- 权限模型不变：普通成员（`workspace_member`）可批量删除（对齐单条 delete 用 `workspace_member`，`repos.py:118`）；admin 专属的关联操作（`link-dir`）不在本次范围。
- 路径穿越防线复用：`_validate_repo_name` 在 body 校验阶段对每个 name 执行，任一非法 → 422 拒整批（恶意 name 不得混入处理流）。

## 3. 详细设计

### 3.1 后端端点：`POST /api/workspaces/{ws}/repos/batch-delete`

落点 `packages/web/src/supernova_web/api/repos.py`，紧邻 `link_repos_in_dir`（`:53-61`）声明，**必须在 `{name:path}` 贪婪路由之前**（`repos.py:64-67` 已注明该陷阱；`link-dir` 即按此位序）。

```python
class BatchDeleteRequest(BaseModel):
    names: list[str]

@router.post("/{ws}/repos/batch-delete", status_code=200)
async def batch_delete_repos(
    ws: str,
    body: BatchDeleteRequest,
    request: Request,
    user=Depends(workspace_member),
):
    names = dedupe(body.names)
    if not names:
        raise HTTPException(422, "names 不能为空")
    if len(names) > BATCH_DELETE_MAX_NAMES:        # 限长，见 §7
        raise HTTPException(422, f"单次最多 {BATCH_DELETE_MAX_NAMES} 个")
    for n in names:
        _validate_repo_name(n)                     # 路径穿越防线，非法 → ValueError → 422

    sm: ScanManager = request.app.state.scan_manager
    rm: RepoManager = request.app.state.repo_manager
    busy_sources = sm.active_repo_sources()        # 一次性快照（set[(ws,name)]）

    deleted: list[str] = []
    unlinked: list[str] = []
    skipped: list[dict] = []
    for name in names:
        if (ws, name) in busy_sources:
            skipped.append({"name": name, "reason": "scanning"}); continue
        outcome = await rm.delete_one(ws, name)    # 见 §3.2，返回枚举/归类
        if outcome.kind == "deleted":   deleted.append(name)
        elif outcome.kind == "unlinked": unlinked.append(name)
        else: skipped.append({"name": name, "reason": outcome.kind})  # busy / not_found
    return {"deleted": deleted, "unlinked": unlinked, "skipped": skipped}
```

要点：
- 鉴权 `workspace_member`（对齐单条 delete `repos.py:118`）；router 级 `_require_auth`（`app.py:476`）仍生效；CSRF header 沿用。
- `active_repo_sources()` **快照一次**后遍历，避免分次检查窗口。
- HTTP **200** 即使有 skipped（部分成功是预期语义，结果结构化反馈；全 skipped 也 200）。
- 路径穿越：body 校验阶段逐项 `_validate_repo_name`，非法 → 422 拒整批（比单条转 409 更早阻断，批量入口应严校验）。
- 路由声明顺序：`batch-delete` 是字面段，必须在 `{name:path}`（`repo_events` `:68` / `get_repo` `:107` / `delete_repo` `:116` / `pull_repo` `:132` / `checkout_repo` `:145`）之前，否则被贪婪吞。照搬 `link-dir` 位序。

### 3.2 RepoManager：新增 `delete_one(ws, name) -> DeleteOutcome`

落点 `repo_manager.py`，紧邻 `delete`（`:416-426`）。不改动 `delete` 本身（保持单条端点行为不变），新增一个**带归类返回值**的薄包装，供批量端点复用：

```python
class DeleteOutcome(TypedDict):
    kind: Literal["deleted", "unlinked", "busy", "not_found"]

async def delete_one(self, ws: str, name: str) -> DeleteOutcome:
    _validate_repo_name(name)
    linked = self._is_linked(ws, name)
    exists = linked or (self._repo_dir(ws, name).is_dir() and _is_repo(self._repo_dir(ws, name)))
    if not exists:
        return {"kind": "not_found"}
    try:
        await self.delete(ws, name)        # 复用：linked→unlink，私有→rmtree；_jobs 忙碌抛 ValueError
    except ValueError:
        return {"kind": "busy"}
    return {"kind": "unlinked" if linked else "deleted"}
```

要点：
- 复用 `delete`（`:416-426`）的分叉与 `_jobs` 忙碌判定，不重复 rmtree/unlink 逻辑。
- `linked` 在删除前判定用于归类（`delete` 内部也判 `_is_linked` 分叉，二者一致）。
- `not_found`：linked 记录不存在且文件系统无仓库目录 → 跳过（单条 `delete` 遇此静默无操作，批量需显式归类供前端反馈）。
- `busy`：`delete` 对 `(ws,name) in self._jobs` 抛 `ValueError`（`:417-418`），捕获归类（对应单条端点的 409）。
- 扫描占用（`scanning`）不在 rm 层判断（rm 不持有 ScanManager），由端点层预过滤。

### 3.3 前端：多选 + 批量操作栏（`ReposTab.tsx`）

新增选择态与 UI（复用已有 `src/components/ui/checkbox.tsx`，本页当前未引）：

- **state**：`const [selected, setSelected] = useState<Set<string>>(new Set())`（`ReposTab.tsx:100-107` 现有 state 区追加）。`pendingDelete`（单值，`:103`）保留供单条删除，不动。
- **每行 Checkbox**：表格首列加 `<Checkbox>`（行渲染 `:237-321`），`checked={selected.has(r.name)}`，`onCheckedChange` toggle。
- **分组头全选**：分组头（`:210-222`）加 Checkbox，选中/反选该组全部仓库。
- **全选/反选**：表头（`repos.table.*` 区）加全选，作用于当前 `filtered`（`:165-169`）结果集。
- **批量操作栏**：`selected.size > 0` 时在表格上方浮出（粘顶）：「已选 N 项」+「删除选中」按钮（Trash2 图标）+「取消选择」。批量进行中（busy）禁用其它行操作。

确认对话框（复用现有 Dialog 结构 `:334-345`，新增动态文案）：
- 统计选中里 linked 数（取消关联）vs 私有数（删除目录）。
- 纯 linked：「将取消关联 N 个仓库（仅移除引用，不删源文件）。」
- 纯私有：「将删除 N 个仓库目录。此操作不可撤销。」
- 混合：「将取消关联 X 个仓库（仅移除引用），删除 Y 个仓库目录。此操作不可撤销。」

提交流程：
- 确认 → 调 `deleteRepos(workspace, [...selected])`（新 client 方法，§3.4）。
- 结果 toast：「已删除/取消关联 X 个，跳过 Y 个」；Y>0 时可展开看 skipped 列表（name + 原因：`scanning`/`busy`/`not_found` 本地化）。
- 成功后 `await refresh()`（`:109-117`）+ `selected.clear()`。

### 3.4 前端 client：`deleteRepos`（`api/client.ts`）

紧邻 `deleteRepo`（`:124-125`）：

```ts
export async function deleteRepos(ws: string, names: string[]): Promise<BatchDeleteResult> {
  return apiFetch(`/workspaces/${encodeURIComponent(ws)}/repos/batch-delete`, {
    method: "POST",
    body: JSON.stringify({ names }),
    csrf: true,
  });
}
// BatchDeleteResult = { deleted: string[]; unlinked: string[]; skipped: {name:string; reason:string}[] }
```

`names` 直接传字符串数组（含 `group/repo` 形态），无需 `encRepo`（`client.ts:102-104`）—— 走 request body 而非 path 段，避免路径编码歧义。

### 3.5 i18n（`src/locales/{en,zh}.json` 的 `repos` 段）

新增 `repos.bulk.*`（en/zh 两份，结构一致）：
`selectAll` / `selectGroup` / `selected`（"已选 {{count}} 项"）/ `deleteSelected`（"删除选中"）/ `clear`（"取消选择"）/ `confirmTitle` / `confirmBodyLinked` / `confirmBodyDelete` / `confirmBodyMixed` / `success`（"已删除/取消关联 {{done}} 个，跳过 {{skipped}} 个"）/ `skippedListTitle` / `skippedReason.{scanning,busy,not_found}` / `error`。

## 4. 测试策略（TDD）

### 4.1 RepoManager（`tests/test_repo_manager.py`）

- `delete_one` 私有克隆 → `{kind:"deleted"}`，目录被 rmtree。
- `delete_one` 关联仓 → `{kind:"unlinked"}`，源文件目录仍在、`linked_repos.json` 移除该记录。
- `delete_one` 不存在（无 linked 记录且无目录）→ `{kind:"not_found"}`，无副作用。
- `delete_one` 仓库在 `_jobs` → `{kind:"busy"}`，无副作用。
- 现有 `delete` 单条行为回归（`test_delete_does_not_rmtree_group_dir` 等，`:159-166`）不受影响。

### 4.2 API（`tests/test_api_repos.py`）

- `POST .../repos/batch-delete` 混合（linked + 私有）→ 200，`deleted`/`unlinked` 正确分类。
- 部分仓库被在跑 scan 引用（mock `active_repo_sources` 含 `(ws,name)`）→ 该项 `skipped: scanning`，其余成功。
- 部分仓库忙碌（mock `(ws,name) in _jobs`）→ `skipped: busy`。
- 不存在 name → `skipped: not_found`。
- 路径穿越 name（`..` / `/` / 空）→ **422**（body 校验阶段拒整批）。
- 空 `names` → 422；超限长（> 阈值）→ 422；含重复 → 去重后处理。
- 鉴权：`workspace_member` 通过；非成员 → 403。
- **路由声明顺序**：`POST /repos/batch-delete` 命中本端点（而非被 `create_repo` 的 `POST /repos` 或 `{name:path}` 吞）。
- CSRF：缺 header → 拒。

### 4.3 前端（`ReposTab` 测试）

- 行 Checkbox toggle 更新 `selected`；分组头全选 / 表头全选正确增删。
- `selected.size > 0` 才渲染批量操作栏；「取消选择」清空。
- 确认框文案随选中构成动态（纯 linked / 纯私有 / 混合）。
- 确认 → 调 `deleteRepos`（mock）→ 成功 toast（含 done/skipped 计数）+ skipped>0 展开列表 + `refresh` 调用 + `selected` 清空。
- busy 态期间禁用其它操作。

## 5. 端到端验证（真机）

前提：rebuild web 镜像（后端 + 前端）；worker / 扫描引擎零改。

1. 进入某 ws 仓库 tab，多选若干（含关联仓 + 私有克隆），点「删除选中」→ 确认框文案分别列出取消关联数 / 删除数。
2. 确认 → 关联仓从列表消失但源文件目录仍在（`linked_repos.json` 移除记录）；私有克隆目录被删；toast 显示成功数。
3. 混入一个正在跑 scan 的仓库 → 该项 `skipped: scanning`，其余成功，toast 提示跳过数 + 展开 name/原因。
4. 单条删除按钮、pull、checkout 等现有操作不受影响（回归）。
5. 非 manager 成员可批量删除（`workspace_member` 即可）；路径穿越 name 在前端虽不构造，但直接调 API 带 `..` → 422。

## 6. 不做（明确排除）

- **不做批量 pull / checkout / 其它操作**：本次仅批量「删除选中」（含自动取消关联）。
- **不级联历史 scan**：删仓库不清理历史 scan 记录里的 `source_repo`/`repo_path` 残留引用（与单条一致）。
- **不加分页 / 虚拟化**：现有 `listRepos` 全量拉取（`client.ts:111-112`），仓库规模不构成瓶颈。
- **不改单条删除端点 / 不改 `RepoManager.delete` 签名**：新增 `delete_one` 包装而非改 `delete`。
- **不加「按状态快速选择」**（如全选 failed/cloning）：polish，MVP 不做。
- **不做软删除 / 回收站**：物理删除不可撤销，确认框明示。

## 7. 待 plan 确认项

- `BATCH_DELETE_MAX_NAMES` 限长阈值（拟 200，plan 定）。
- 批量操作栏具体形态（表格上方粘顶条 vs 浮层）与 skipped 展示形态（toast 内展开 vs 独立告警 vs 结果 Dialog）—— plan 阶段定最终交互。
- `DeleteOutcome` 落点（`repo_manager.py` 模块级 TypedDict vs 复用现有效率类型）与 `delete_one` 是否同步暴露给单条端点（当前单条 `delete_repo` 不改，保持 `delete`）。
- 前端 Checkbox 在分组折叠下的可见性（折叠组的选择是否保留、展开时回显）细节。
