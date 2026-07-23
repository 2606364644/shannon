# Plan: GitNexus 轨 taint LLM 走 output_format 源头约束(TS 式,根因治本)

日期: 2026-07-22 | 分支: feat/fork-py | 关联: `2026-07-22-gitnexus-taint-timeout-fallback-plan.md`(已落地 P0+P1 兜底) / memory `gitnexus-ssrf-taint-flow-drop-root-cause`

## 问题陈述

扫 sentinel_dashboard 时 `taint-analysis` 阶段每个含 sink 函数刷两条 WARNING:

```
WARNING llm_taint_analyzer: Failed to parse LLM taint response (Expecting value: line 1 column 1 (char 0)); snippet='# Taint Propagation Analysis\n\n## 分析过程\n...'
WARNING llm_taint_analyzer: LLM taint response unparseable for ...executeCommand:319; using deterministic fallback.
```

**根因链(经源码 + TS 对照实证)**:

1. taint 的 `llm_client`(`activities.py:672 _make_gitnexus_llm_client`)调 `run_claude_prompt` 时**没传 `output_format`**,返回 `result.text`(纯文本)。
2. GLM 经 Claude Code CLI 返回 **Markdown 文本**(`# Taint Propagation Analysis` + 嵌 ```java 代码块 + 末尾 ```json 块),`parse_llm_response`(`llm_taint_analyzer.py:196`)裸 `json.loads(raw)`,首字符非 `{` -> `Expecting value: line 1 column 1` -> 解析失败 -> fallback。
3. `2026-07-22-gitnexus-taint-timeout-fallback-plan.md` P1 已让"解析失败->走 `_deterministic_intra_fallback`"(保召回),用户刷的 WARNING 正是 P1 的兜底日志。但 P1 只兜底不救回 -- 解析失败后降级到 `is_entry_hint` 启发式(精度 < LLM 精确判断),且每函数刷 2 WARNING。

## TS 对照(子 agent 实证 /root/shannon)

- **TS 侧无 per-function taint LLM 逻辑**(这是 Python 侧新增,无对应物可移植)。
- TS 结构化输出**完全靠 Claude SDK 的 `outputFormat`**:`queue-schemas.ts` 给每个 vuln agent 造 `JsonSchemaOutputFormat`,`claude-executor.ts` 透传给 SDK `query()`,SDK 强制模型吐合法 JSON 并预解析,TS 直接读 `message.structured_output` -- **从不 `JSON.parse` LLM 原始文本,所以根本不遇 Markdown 问题**。
- TS 侧无任何 markdown-fence / JSON 子串提取工具(11 处 `JSON.parse` 全解析 SDK 消息或磁盘文件,失败就重跑,零容错)。

**结论**: 路径 A(源头 `output_format` 约束)是 TS 对齐的正解;事后 extract 是 GLM 兜底,两者共用同一份 extract 基建。

## 关键发现:provider 基建已全就位(路径 A 比预期省事)

`run_claude_prompt` 有 `output_format` 通道(`runner.py:112`),`ClaudeRunResult.structured_output` 字段已存在(`runner.py:101`)。anthropic provider 已实现完整链路(`providers_anthropic.py`):

- `_build_options`(:285): `output_format` -> `{"type":"json_schema","schema":...}` 信封 -> CLI `--json-schema` 参数(协议级结构化输出 + AJV 校验 + SDK `error_max_structured_output_retries` 失败重试)。
- `_extract_result`(:399-417): 优先读 SDK 原生 `result_message.structured_output`;GLM 经 CLI 拿不到时,用 `_extract_json_payload(text)` **从 final 文本兜底提取 JSON**(注释明确写"GLM 后端常见,final 文本夹中文说明+JSON")。

**缺的只是调用方传参 + 读 structured_output**:taint `llm_client` 没传 `output_format`、读 `result.text` 而非 `result.structured_output`,所以 provider 的兜底链路根本没被激活。

## 修复方案

### 步骤 1: 提取 `_extract_json_payload` 到无 SDK 依赖模块(仍需做,provider 兜底用它)

**新建** `packages/core/src/supernova_core/agents/llm_json.py`:把 `_extract_json_payload` 从 `openai_output_schema.py` 搬来(纯字符串工具,不 import openai-agents SDK)。

**改** `openai_output_schema.py`:删原定义,改 `from .llm_json import _extract_json_payload` re-export -- 保持 `providers_anthropic`/`providers_openai` 现有 `from .openai_output_schema import _extract_json_payload` 不破。

### 步骤 2: 增强 `_extract_json_payload`(provider 兜底 + code_index 兜底共用,向后兼容)

**改** `agents/llm_json.py` 新定义,处理 GLM"Markdown + 中间 ```java 代码块(含{}) + 末尾 ```json"形态:

```python
import json, re

def _extract_json_payload(text: str) -> str | None:
    if not text or not text.strip(): return None
    s = text.strip()
    # 1. 纯 JSON 直通(最快路径 + 向后兼容)
    try: json.loads(s); return s
    except (json.JSONDecodeError, ValueError): pass
    # 2. 所有 ```fence 块,从后往前取首个能 json.loads 的
    #    GLM 常在 Markdown + ```java 代码示例后用 ```json 包裹结果;
    #    从后往前避免前面代码块 {} 干扰"首{末}"提取。
    candidates = [m.group(1).strip()
                  for m in re.finditer(r"```[a-zA-Z]*\s*\n?(.*?)```", s, re.DOTALL)]
    for body in reversed(candidates):
        try: json.loads(body); return body
        except (json.JSONDecodeError, ValueError): continue
    # 3. 回退:首个 { 到末个 } 子串(前导叙述 + 无 fence JSON / 旧兼容)
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return s[start:end+1]
    return None
```

**已验证(/tmp/verify_extract.py)**: OLD 对 GLM Markdown+```java+```json FAIL(首{落在```java代码块),NEW OK 解析出 `tainted_params=['ip','port']`;现有 8 个 `test_openai_output_schema.py` 用例全部等价通过,零回归。

### 步骤 3: taint `llm_client` 传 output_format + 读 structured_output(路径 A 核心)

**改** `packages/whitebox/src/supernova_whitebox/pipeline/activities.py:672-685` `_make_gitnexus_llm_client`:

```python
from supernova_core.code_index.parameter_models import TaintAnalysisResult

TAINT_ANALYSIS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "tainted_params": {"type": "array", "items": {"type": "string"}},
        "propagation_paths": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_param": {"type": "string"},
                    "sink_id": {"type": "string"},
                    "sink_arg_index": {"type": "integer"},
                    "intermediate_vars": {"type": "array", "items": {"type": "string"}},
                    "sanitized": {"type": "boolean"},
                    "sanitizer_description": {"type": ["string", "null"]},
                    "post_sanitized_concat": {"type": "boolean"},
                    "confidence": {"type": "number"},
                },
                "required": ["source_param", "sink_id", "sink_arg_index"],
            },
        },
    },
    "required": ["tainted_params", "propagation_paths"],
}

def _make_gitnexus_llm_client(repo_path: str):
    if not is_gitnexus_llm_enabled():
        return None
    async def _client(prompt: str, **kwargs) -> str:
        result = await run_claude_prompt(
            prompt=prompt, repo_path=repo_path, model_tier="medium",
            structured_output_schema=TAINT_ANALYSIS_SCHEMA,
        )
        # 优先 structured_output(SDK 原生或 provider extract 兜底);空则回退 .text
        # 让 parse_llm_response 走原 _extract_json_payload + fallback 路径。
        if result.structured_output is not None:
            return json.dumps(result.structured_output)
        return result.text
    return _client
```

**schema 手写 dict**(对齐项目惯例 `AUTH_VALIDATION_SCHEMA`/`LLM_REQUEST_SCHEMA`,非 pydantic `.model_json_schema()`)避免 `str|None` union 生成 `anyOf` 在 CLI AJV draft-07 校验器上的兼容坑(TS `queue-schemas.ts:103` 警告)。

**契约不变**: `_client` 仍返回 `str`,`analyze_taint_llm`/`parse_llm_response` 签名不变(它们 `json.loads` 一个合法 JSON 字符串)。区别只是:成功时该字符串来自 SDK 强制约束的 JSON(经 `json.dumps`),失败时回退 `.text` 让原 extract+fallback 兜底。**三重防线**: SDK 原生 structured_output -> provider `_extract_json_payload` 兜底 -> code_index `parse_llm_response` 的 `_extract_json_payload`+`_deterministic_intra_fallback` 兜底。

### 步骤 4: TDD 测试

**改** `tests/agents/test_openai_output_schema.py`(步骤 2 增强用例,不删原):
- `test_extract_json_payload_markdown_with_java_block_then_json`: `# 分析\n```java\nfoo() {\n}\n```\n```json\n{"k":"v"}\n``` ` -> `{"k":"v"}`
- `test_extract_json_payload_multiple_fences_takes_last_valid`: 多 fence 取最后合法 JSON

**改** `tests/code_index/test_llm_taint_analyzer.py`(`TestParseLLMResponse`):
- `test_glm_markdown_with_java_block_parses`: mock GLM 返回 Markdown+```java+```json,断言解析出参数(当前 fail,步骤 2 后 pass)

**改** `packages/whitebox/tests/`(activities 集成):
- `test_taint_client_passes_output_format`: mock `run_claude_prompt`,断言 `_make_gitnexus_llm_client` 的 `_client` 调用时传了 `structured_output_schema=TAINT_ANALYSIS_SCHEMA`,且 `structured_output` 非空时返回 `json.dumps` 结果。

**回归**: 跑 `test_llm_taint_analyzer.py` / `test_openai_output_schema.py` / `test_chain_verdict.py` / `test_build_code_index_orchestration.py`(只相关,勿广跑全套 -- 预存挂起/失败)。

## 效果

- taint-analysis WARNING 刷屏消除(GLM 经 `--json-schema` 强制 JSON -> SDK 原生 structured_output -> 解析成功)。
- taint 精度回升:LLM 精确判断(`tainted_params`/`propagation_paths`/sanitizer)替代 `is_entry_hint` 启发式;sanitizer 管道(`local_steps` summary step)恢复。
- provider 兜底 `_extract_json_payload` 增强,惠及所有经 provider 的 structured_output 路径(auth/poc/chain_verdict)。
- 三重防线冗余:SDK 原生 -> provider extract -> code_index extract+fallback,任一层失败不丢召回。

## 端到端验证

`uv run shannon-whitebox start --repo /root/shannon-py/repos/frontend/sentinel_dashboard`,确认:
- events.ndjson `taint-analysis` 阶段无 `Failed to parse LLM taint response` / `unparseable` WARNING。
- `taint-analysis ✓ taint flow in X` 计数上升(真实 tainted_params 而非 fallback 全参)。
- inj/xss/ssrf queue 召回与精度对照(不回退)。

## 铁律边界(守)

- 不改 LLM 轨(纯 LLM 自给自足,不吃确定性产物) -- 只动 GitNexus 轨 taint 调用方 + provider 兜底基建。
- 不改 prompt(`build_taint_prompt` 已要求 JSON schema,问题在没传 output_format + parse,不在 prompt)。
- 不改 sink/source 规则。
- 双引擎无关(core 层;openai 引擎 provider 已有 `_extract_json_payload` 兜底,anthropic 亦有)。
- 向后兼容: 增强 `_extract_json_payload` 不破现有用例;已落地 P0+P1 兜底作为最后防线保留。

## 回滚

- 步骤 1+2(extract 提取+增强)与步骤 3(taint 传 output_format)解耦: 步骤 3 不依赖步骤 1+2(provider 内部 `_extract_json_payload` 已存在,步骤 1+2 只是把同一份代码搬位置+增强)。
- 步骤 3 可独立 revert(去掉 `structured_output_schema=` 参数即回到现状)。
- 步骤 1+2 可独立 revert(还原 `openai_output_schema.py` 原定义)。

## 待办 / follow-up(不在本 plan)

- `chain_verdict.py:268` 同根因裸 `json.loads`(紧随 taint 跑,刷 `chain-verdict LLM returned non-JSON` WARNING)。同样可传 output_format(ChainVerdict schema),但 chain_verdict 走另一 `llm_client`(`_make_verdict_llm_client` activities.py:1211),需单独改。**列 follow-up**。
- `sink_discovery_llm._parse_verdicts`/`_parse_sink_verdicts` 同根因(`logger.debug` 静默 + 降级规则 sink,不刷屏但损召回)。**列 follow-up**。
