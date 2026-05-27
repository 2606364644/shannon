## ADDED Requirements

### Requirement: 根 README 提供项目概览
根目录 README.md SHALL 包含：项目名称、一句话描述（AI 驱动的自动化渗透测试框架）、核心特性列表（白盒/黑盒扫描、14 agent 协作、Temporal 工作流编排、可定制 prompt 模板）、最低系统要求（Python 3.12+）。

#### Scenario: 新用户首次打开 README
- **WHEN** 用户打开 README.md
- **THEN** 在前 10 行内能看到项目名称、用途描述和核心特性

### Requirement: README 包含安装步骤
README SHALL 提供使用 uv 的安装命令（`uv sync`），以及必要的依赖说明（Temporal Server）。

#### Scenario: 用户按 README 安装
- **WHEN** 用户按 README 安装步骤执行
- **THEN** 能成功安装三个包（shannon-core、shannon-whitebox、shannon-blackbox）

### Requirement: README 包含快速命令示例
README SHALL 展示白盒扫描和黑盒扫描的 CLI 命令示例各一个。

#### Scenario: 用户复制命令运行扫描
- **WHEN** 用户复制 README 中的命令示例
- **THEN** 命令语法与 CLI 定义完全一致（参数名、必填项正确）

### Requirement: README 链接到详细文档
README SHALL 包含指向 docs/ 目录下所有文档的链接列表。

#### Scenario: 用户从 README 导航到详细文档
- **WHEN** 用户点击 README 中的文档链接
- **THEN** 能到达对应的 docs/*.md 文件

### Requirement: 各包 README 说明定位
`packages/core/README.md`、`packages/whitebox/README.md`、`packages/blackbox/README.md` SHALL 各包含：包名、一句话用途说明、主要依赖、与其他包的关系。每个 README 不超过 20 行。

#### Scenario: 开发者浏览单包目录
- **WHEN** 开发者打开 `packages/whitebox/README.md`
- **THEN** 能看到该包的用途、依赖关系和核心模块列表
