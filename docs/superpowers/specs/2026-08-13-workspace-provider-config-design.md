# Workspace Provider Configuration Design

## Goal

每个 Web 工作区必须拥有一份自洽的 Provider 配置才能启动扫描。工作区配置不再从全局环境变量、全局模型或内置默认模型自动回落；新工作区自动获得一份 OpenAI-compatible 默认模板，但 API key 必须由用户填写。

## Current Problem

`WsConfigStore.resolve_provider_config()` 当前以全局 `build_provider_config()` 结果为基底，再覆盖工作区中非 `None` 的字段。Provider 层还会在 tier model 缺失时回落到全局 `model` 或 `DEFAULT_MODELS`。结果是空工作区也可能产生一个看似有效的 `provider_config` 并提交扫描，实际运行时才因凭据或模型不匹配失败。

## Decisions

### 1. Default workspace template

新建工作区时写入 `config.yaml` 中的 Provider 默认字段：

```env
SUPERNOVA_AI_PROVIDER=openai_compatible
SUPERNOVA_OPENAI_BASE_URL=https://llm-proxy.futuoa.com/v1
SUPERNOVA_OPENAI_LARGE_MODEL=glm-5.2-coder
SUPERNOVA_OPENAI_MEDIUM_MODEL=glm-5.2-coder
SUPERNOVA_OPENAI_SMALL_MODEL=glm-5.2-coder
```

`SUPERNOVA_OPENAI_API_KEY` 不写入默认值。配置页读取没有 `config.yaml` 的旧工作区时，采用同样的默认模板；已有配置文件不被静默覆盖或补齐。

默认模板只用于初始化空工作区，不是运行时 fallback。用户保存过配置后主动删除字段，删除后的字段保持为空并使扫描校验失败。

### 2. Required fields

工作区扫描使用选定 Provider 的工作区字段进行完整性校验。对于默认的 `openai_compatible`，以下字段全部必需：

- `ai_provider`
- `base_url`
- `api_key`
- `small_model`
- `medium_model`
- `large_model`

`model`、`max_turns`、`adaptive_thinking` 是可选运行时调参，不参与必填判断。Provider 的必填字段由 `PROVIDER_SETTINGS` 声明，避免在 Web 层复制 Provider 规则；`credential` 语义使用工作区的 `api_key` 字段承载。

### 3. No global fallback for Web workspaces

当 `ws_config_store` 可用时，`resolve_provider_config(ws)` 只从该工作区配置构造 Provider 配置，不调用全局 `build_provider_config()` 作为基底，也不把缺失字段替换成全局值。校验失败抛出带缺失 env key 的 `ValueError`，由 API 转成 HTTP 422。

CLI 或旧的、未注入 `ws_config_store` 的调用路径继续使用原有全局配置行为，以保持非 Web 入口兼容。

### 4. Fail-fast scan behavior

扫描入口和 `ScanManager` 的 Provider 解析都必须阻止不完整工作区配置：

- `/api/scan` 在启动 Temporal workflow 前完成校验；
- `_resolve_provider_config()` / 提交 workflow 的内部路径再次完成校验；
- Provider 配置不完整时不创建或提交 workflow，并返回 422（错误信息明确指出缺失字段）。

### 5. Persistence and API behavior

- `POST /api/workspaces` 创建目录、元数据、成员关系后，同时写入默认 Provider 配置。
- `GET /api/workspaces/{ws}/config` 对没有配置文件的旧工作区渲染默认模板；不生成 API key。
- `PUT /api/workspaces/{ws}/config` 保持现有全量覆盖和掩码保留语义；保存后缺失字段不会被全局配置补回。
- 未设置的 API key 继续不在 GET 文本中暴露；用户通过配置文本中的 Provider API key 字段填入密钥。

## Verification

新增/更新测试覆盖：

1. 新工作区包含默认 Provider 配置，但 API key 为空。
2. 空工作区或缺 API key 时，解析 Provider 配置抛出明确错误，且 workflow 不提交。
3. 工作区字段不完整时，不从全局配置补值。
4. 完整工作区配置能够原样穿入 `PipelineInput.provider_config`。
5. 配置 API 与既有鉴权、掩码、全量覆盖语义保持兼容。
