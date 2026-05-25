# Whitebox-Only & Blackbox-Only 扫描模式

Shannon 支持三种扫描模式。默认模式不变，新增白盒和黑盒两种独立模式。

| 模式 | 命令 | 需要 URL | 需要 Repo | 说明 |
|---|---|---|---|---|
| 完整扫描（默认） | `start -u <URL> -r <repo>` | Yes | Yes | 完整五阶段流水线 |
| 白盒扫描 | `start -r <repo> --whitebox-only` | No | Yes | 纯源码分析，不需要运行环境 |
| 黑盒扫描 | `start -u <URL> -r <repo> --blackbox-only` | Yes | Yes | 基于白盒产物执行漏洞利用 |

## 白盒扫描

只分析源代码，不访问任何线上服务。适用于：

- 只有源码，还没有部署环境
- 快速静态安全审查
- CI/CD 中集成安全检查

### 用法

```bash
# 本地模式
./shannon start -r my-repo --whitebox-only

# npx 模式
npx @keygraph/shannon start -r my-repo --whitebox-only

# 指定绝对路径
./shannon start -r /path/to/repo --whitebox-only

# 指定配置文件（白盒模式仍可使用规则和范围配置）
./shannon start -r my-repo --whitebox-only -c config.yaml

# 指定工作区名称
./shannon start -r my-repo --whitebox-only -w my-audit

# 指定输出目录
./shannon start -r my-repo --whitebox-only -o ./results
```

### 执行流程

```
Pre-Flight（轻量）
  ✓ 仓库路径验证
  ✓ 配置文件解析
  ✓ API 凭证验证
  ✗ 跳过 URL 可达性检查
  ✗ 跳过认证验证

Pre-Recon（源码架构分析）
  → pre_recon_deliverable.md

Recon（纯静态侦察）
  → recon_deliverable.md
  注：使用 recon-static prompt，不启动浏览器

漏洞分析（4 类并行）
  → injection_analysis_deliverable.md + injection_exploitation_queue.json
  → auth_analysis_deliverable.md + auth_exploitation_queue.json
  → authz_analysis_deliverable.md + authz_exploitation_queue.json
  → ssrf_analysis_deliverable.md + ssrf_exploitation_queue.json
  注：跳过 XSS（需要线上环境验证）

报告生成
  → comprehensive_security_assessment_report.md
```

### 产出物

所有文件位于 `<repo>/.shannon/deliverables/` 目录下：

| 文件 | 说明 |
|---|---|
| `pre_recon_deliverable.md` | 源码架构与安全组件分析 |
| `recon_deliverable.md` | 攻击面映射（基于静态分析） |
| `*_analysis_deliverable.md` | 各类漏洞的源码级分析（source→sink 追踪） |
| `*_exploitation_queue.json` | 结构化漏洞队列（可直接用于后续黑盒扫描） |
| `*_findings.md` | 各类漏洞的可读摘要 |
| `comprehensive_security_assessment_report.md` | 完整安全评估报告 |

## 黑盒扫描

读取白盒扫描的产出物，对线上环境执行漏洞利用。前提是同一 repo 目录下已经跑过白盒扫描。

### 用法

```bash
# 本地模式
./shannon start -u https://target.example.com -r my-repo --blackbox-only

# npx 模式
npx @keygraph/shannon start -u https://target.example.com -r my-repo --blackbox-only

# 带认证配置
./shannon start -u https://target.example.com -r my-repo --blackbox-only -c config.yaml
```

### 执行流程

```
Pre-Flight（完整）
  ✓ 仓库路径验证
  ✓ 配置文件解析
  ✓ API 凭证验证
  ✓ URL 可达性检查
  ✓ 认证验证（浏览器模拟登录）

验证已有产物
  ✓ 检查 recon_deliverable.md 存在
  ✓ 检查至少一个 *_exploitation_queue.json 存在且非空

漏洞利用（按队列内容执行）
  → injection_exploitation_evidence.md
  → xss_exploitation_evidence.md（如果白盒队列中有 XSS 数据）
  → auth_exploitation_evidence.md
  → authz_exploitation_evidence.md
  → ssrf_exploitation_evidence.md

报告生成
  → comprehensive_security_assessment_report.md（覆盖白盒报告）
```

### 前提条件

- 同一 repo 目录下必须存在白盒扫描的产出物
- 至少有一个 `*_exploitation_queue.json` 文件
- 目标 URL 必须可达

## 典型工作流：白盒 → 黑盒

```bash
# 步骤 1：拿到源码，先跑白盒
./shannon start -r my-repo --whitebox-only

# ... 查看白盒报告，确认发现 ...
cat my-repo/.shannon/deliverables/comprehensive_security_assessment_report.md

# 步骤 2：环境就绪后，在同一 repo 上跑黑盒
./shannon start -u https://target.example.com -r my-repo --blackbox-only

# 步骤 3：查看最终报告（含漏洞利用证据）
cat my-repo/.shannon/deliverables/comprehensive_security_assessment_report.md
```

白盒和黑盒各自动创建独立 workspace，通过 repo 中的 `.shannon/deliverables/` 目录共享产出物。

## 约束

- `--whitebox-only` 和 `--blackbox-only` 互斥，不能同时使用
- `--blackbox-only` 必须提供 `-u`（目标 URL）
- 白盒模式跳过 XSS 分析（XSS 需要线上浏览器验证）
- 黑盒模式不会重新执行源码分析，只执行漏洞利用和报告
- 这两种模式各自启动独立的 Temporal workflow，不使用 resume 机制
