# Tasks: 添加 .env 文件支持和 OpenAI 兼容 Provider

## 1. 依赖配置

- [x] 1.1 在 `packages/core/pyproject.toml` 中添加 `python-dotenv>=1.0` 依赖
- [x] 1.2 运行 `uv sync` 安装新依赖

## 2. CLI .env 加载

- [x] 2.1 在 `packages/whitebox/src/shannon_whitebox/cli/main.py` 中添加 `from dotenv import load_dotenv` 导入
- [x] 2.2 在 `cli()` 函数开头添加 `load_dotenv()` 调用
- [x] 2.3 在 `packages/blackbox/src/shannon_blackbox/cli/main.py` 中重复步骤 2.1-2.2

## 3. openai_compatible Provider 验证

- [x] 3.1 在 `packages/core/src/shannon_core/utils/credential_validator.py` 中实现 `_validate_openai_compatible` 函数
- [x] 3.2 在 `validate_credentials` 函数中添加 `openai_compatible` 分支
- [x] 3.3 添加必需参数验证（api_key 和 base_url）

## 4. 环境变量读取更新

- [x] 4.1 更新 `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` 中的 `run_credential_check` 函数
- [x] 4.2 实现环境变量优先级逻辑：`input.api_key > SHANNON_API_KEY > ANTHROPIC_API_KEY`
- [x] 4.3 添加 `SHANNON_BASE_URL` 环境变量读取

## 5. 测试用例

- [x] 5.1 在 `packages/core/tests/test_credential_validator.py` 中添加 `TestValidateOpenAICompatible` 测试类
- [x] 5.2 添加有效凭据测试场景
- [x] 5.3 添加无效凭据（401/403）测试场景
- [x] 5.4 添加缺少 api_key 测试场景
- [x] 5.5 添加缺少 base_url 测试场景

## 6. 验证

- [x] 6.1 运行 `pytest packages/core/tests/test_credential_validator.py::TestValidateOpenAICompatible -v` 验证新测试通过
- [x] 6.2 运行 `pytest packages/core/tests/test_credential_validator.py -v` 验证所有测试通过
- [x] 6.3 创建示例 .env 文件并验证 CLI 能正确加载环境变量
