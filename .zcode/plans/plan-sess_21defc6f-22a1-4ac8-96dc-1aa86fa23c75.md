## 工作区设置页:点击配置项 → 注入左侧编辑框(带默认值)

### 目标
`WsSettingsTab` 右侧「可用配置项」面板里,点击某个 key 时,从「复制 key 名到剪贴板」改为「把 `KEY=默认值` 注入左侧 textarea」。API key 类凭据留空等用户填。

### 文件改动

#### 1. `packages/web/frontend/src/routes/WorkspaceDetail/WsSettingsTab.tsx`

**(a) 给每个 `CfgKey` 增加默认值字段**

`CfgKey` 接口加 `defaultValue: string`。凭据类的 defaultValue 设为空字符串 `""`,代表「等用户填」。

生效组(`EFFECTIVE_GROUPS`)默认值(与后端 `ws_config_store.DEFAULT_WS_*` 对齐 + PLACEHOLDER):
- `SUPERNOVA_AI_PROVIDER` = `openai_compatible`
- `SUPERNOVA_OPENAI_BASE_URL` = `https://llm-proxy.futuoa.com/v1`
- `SUPERNOVA_OPENAI_API_KEY` = `""`(凭据,留空)
- `SUPERNOVA_MODEL` = `glm-5.2-coder`
- `SUPERNOVA_OPENAI_SMALL_MODEL` = `glm-5.2-coder`
- `SUPERNOVA_OPENAI_MEDIUM_MODEL` = `glm-5.2-coder`
- `SUPERNOVA_OPENAI_LARGE_MODEL` = `glm-5.2-coder`
- `SUPERNOVA_MAX_TURNS` = `120`
- `SUPERNOVA_ADAPTIVE_THINKING` = `true`
- `GITLAB_USER` = `""`(凭据,留空)
- `GITLAB_TOKEN` = `""`(凭据,留空)

进程级组(`PROCESS_KEYS`)默认值(从 `concurrency.py` 推断):
- `SUPERNOVA_MAX_CONCURRENT` = `4`
- `SUPERNOVA_PRICING_OVERRIDE` = `""`(JSON,留空)
- `SUPERNOVA_LLM_TRACK_ENABLED` = `true`
- `SUPERNOVA_GITNEXUS_LLM_ENABLED` = `true`
- `SUPERNOVA_AGENT_NARRATION_LANG` = `zh`
- `CLAUDE_CODE_MAX_OUTPUT_TOKENS` = `32000`

**(b) 新增 `injectKey(k: CfgKey)` 函数**

行为:
1. 解析当前 `envText`,逐行检查是否已有「同 key 已存在」(行首 `KEY=` 或 `#KEY=`,容忍前导空白与 `#` 注释)。
2. 若已存在 → `toast.info(t("wsConfig.keys.exists", { key }))`,不改动。
3. 若不存在 → 在文本末尾追加 `\nKEY=defaultValue`(若文本为空则直接设为该行;确保不以多余空行开头),`toast.success(t("wsConfig.keys.inserted", { key }))`。

**(c) 改造 `KeyRow` 组件**

- 移除 `copied` / `onCopy` props 与复制图标(Copy/Check)。
- 新增 `onInject: (k: CfgKey) => void` prop,点击行调用注入。
- 凭据类(defaultValue 为空)在 key 后显示一个小的「需填写」标记(如琥珀色小点或文字 `·`),提示用户注入后要填值。
- 保留 `kind` 颜色标记与 processLevel 琥珀色。

**(d) 移除 `copyKey` 函数与 `copied` state**

**(e) 更新 `KeyRow` 调用处**(生效组 + 进程级组两处)传 `onInject={injectKey}`。

**(f) 调整面板文案**

`wsConfig.keys.panelDesc` 从「点击键名复制到剪贴板…」改为「点击键名注入左侧配置框(API key 等凭据需自行填值)」。

#### 2. `packages/web/frontend/src/locales/zh.json` & `en.json`

- 新增 `wsConfig.keys.inserted`:`已注入 {{key}}(默认值已填,凭据请自行填值)` / `Inserted {{key}} with default value; fill in credentials yourself`
- 新增 `wsConfig.keys.exists`:`{{key}} 已存在,已跳过` / `{{key}} already exists, skipped`
- 改 `wsConfig.keys.panelDesc`:中 `点击键名注入左侧配置框(凭据请自行填值)` / 英 `Click a key to insert it into the editor (fill in credentials yourself)`
- 改 `wsConfig.keys.copied`:可保留(key 被引用)或删除。检查无其它引用后删除。

#### 3. `packages/web/frontend/src/routes/WorkspaceDetail/WsSettingsTab.test.tsx`

- 现有测试不依赖复制行为,无需大改;但需确认「渲染可用配置项词典」测试仍通过(key 文本仍显示)。
- 新增一个测试:点击某个生效 key(如 `SUPERNOVA_MAX_TURNS`)→ textarea 出现 `SUPERNOVA_MAX_TURNS=120`。
- 新增一个测试:点击凭据 key(如 `SUPERNOVA_OPENAI_API_KEY`)→ textarea 出现 `SUPERNOVA_OPENAI_API_KEY=`(值为空)。
- 新增一个测试:已存在的 key 再点击 → 不重复注入(可用 `queryAllByText` 或检查 value 不含两行)。

### 不改动
- `insertTemplate`「填入完整模板」按钮保持不变(仍是注释模板)。
- 后端、API 不动。
- 进程级键的「无效告警」逻辑不变(后端仍会 warnings)。

### 验证
- `pnpm --filter web-frontend test WsSettingsTab`
- `pnpm --filter web-frontend typecheck`(或 build)
- 手动在 `/p/<ws>/settings` 点击各 key,确认注入与默认值、凭据留空、重复跳过。