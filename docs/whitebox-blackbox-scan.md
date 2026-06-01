# Whitebox-Only & Blackbox-Only 扫描模式

Shannon 支持三种扫描模式。默认模式不变，新增白盒和黑盒两种独立模式。

| 模式 | 命令 | 需要 URL | 需要 Repo | 说明 |
|---|---|---|---|---|
| 完整扫描（默认） | `start -u <URL> -r <repo>` | Yes | Yes | 完整五阶段流水线 |
| 白盒扫描 | `start -r <repo> --whitebox-only` | No | Yes | 纯源码分析，不需要运行环境 |
| 黑盒扫描 | `start -u <URL> -r <repo> --blackbox-only` | Yes | Yes | 基于白盒产物执行漏洞利用 |

## 使用场景

### 决策树

```
                    是否有源代码？
                         │
            ┌────────────┴────────────┐
            │ No                      │ Yes
            │                         │
        ❌ 不支持                  是否有运行环境？
                                       │
                          ┌────────────┴────────────┐
                          │ No                      │ Yes
                          │                         │
                    白盒扫描              完整扫描 OR
                    (--whitebox-only)   白盒→黑盒分离
                                          │
                             ┌────────────┴────────────┐
                             │                        │
                      需要分离执行？              一次性完成？
                             │                        │
                      白盒 → 黑盒                 完整扫描
```

### 推荐场景

| 场景 | 推荐模式 | 理由 |
|------|----------|------|
| CI/CD 集成 | 白盒扫描 | 无需运行环境，快速反馈 |
| 本地开发测试 | 完整扫描 | 一次性获得完整结果 |
| 代码审查 | 白盒扫描 | 聚焦源码安全问题 |
| 渗透测试 | 完整扫描 | 需要实际利用验证 |
| 分阶段评估 | 白盒→黑盒 | 先分析源码，环境就绪后再验证 |

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

### 交接机制

白盒扫描的产出物写入 `<repo>/.shannon/deliverables/`（通过 Docker volume overlay）。黑盒扫描在同一 repo 上运行时，会读取该目录中的 `*_exploitation_queue.json` 文件作为输入。

**重要**：两次扫描必须使用同一个 repo 路径。黑白盒各自的 workspace 是独立的 Temporal workflow，但共享 repo 的 deliverables 目录。如果黑盒扫描找不到白盒产物，会报错退出并提示缺失的文件。

## 约束

- `--whitebox-only` 和 `--blackbox-only` 互斥，不能同时使用
- `--blackbox-only` 必须提供 `-u`（目标 URL）
- 白盒模式跳过 XSS 分析（XSS 需要线上浏览器验证）
- 黑盒模式不会重新执行源码分析，只执行漏洞利用和报告
- 这两种模式各自启动独立的 Temporal workflow，不使用 resume 机制

---

## 预期输出

### 白盒扫描成功标志

扫描完成后，检查以下文件是否存在：

```bash
# 方法 1：查看 deliverables 目录
ls <repo>/.shannon/deliverables/

# 应该看到：
# pre_recon_deliverable.md
# recon_deliverable.md
# injection_analysis_deliverable.md
# injection_exploitation_queue.json
# auth_analysis_deliverable.md
# auth_exploitation_queue.json
# authz_analysis_deliverable.md
# authz_exploitation_queue.json
# ssrf_analysis_deliverable.md
# ssrf_exploitation_queue.json
# comprehensive_security_assessment_report.md
```

```bash
# 方法 2：检查 session 状态
cat workspaces/<workspace-name>/session.json | grep status
# 输出: "status": "completed"
```

### 黑盒扫描成功标志

```bash
# 查看 deliverables 目录
ls <repo>/.shannon/deliverables/

# 黑盒扫描完成后，会新增或覆盖：
# injection_exploitation_evidence.md
# auth_exploitation_evidence.md
# authz_exploitation_evidence.md
# ssrf_exploitation_evidence.md
# comprehensive_security_assessment_report.md（覆盖白盒版本）
```

### 快速验证脚本

```bash
#!/bin/bash
# 快速检查白盒扫描是否成功完成

REPO_PATH=$1
DELIVERABLES="$REPO_PATH/.shannon/deliverables"

# 检查目录是否存在
if [ ! -d "$DELIVERABLES" ]; then
  echo "❌ deliverables 目录不存在"
  exit 1
fi

# 检查必需文件
required_files=(
  "pre_recon_deliverable.md"
  "recon_deliverable.md"
  "comprehensive_security_assessment_report.md"
)

for file in "${required_files[@]}"; do
  if [ -f "$DELIVERABLES/$file" ]; then
    echo "✓ $file"
  else
    echo "✗ $file 缺失"
  fi
done

# 检查至少有一个 exploitation_queue
queue_count=$(ls "$DELIVERABLES"/*_exploitation_queue.json 2>/dev/null | wc -l)
if [ $queue_count -gt 0 ]; then
  echo "✓ 找到 $queue_count 个 exploitation_queue 文件"
else
  echo "✗ 没有找到任何 exploitation_queue 文件"
fi
```

---

## 故障排查

### 黑盒扫描报错：找不到白盒产物

**错误信息**：
```
Error: No whitebox deliverables found. Please run whitebox scan first.
```

**诊断步骤**：

```bash
# 1. 确认 repo 路径完全一致
# 白盒和黑盒必须使用相同的 repo 路径
ls -la /path/to/repo/.shannon/deliverables/

# 2. 检查必需文件是否存在
ls /path/to/repo/.shannon/deliverables/*_exploitation_queue.json

# 3. 检查文件是否为空
for f in /path/to/repo/.shannon/deliverables/*.json; do
  echo "=== $f ==="
  cat "$f" | head -c 100
  echo ""
done
```

**常见原因**：

| 原因 | 解决方案 |
|------|----------|
| repo 路径不一致 | 使用绝对路径，确保两次扫描路径完全相同 |
| 白盒扫描未完成 | 检查白盒 workspace 的 session.json，确认 status 为 completed |
| deliverables 目录不存在 | 重新运行白盒扫描 |

---

### 白盒扫描卡住或失败

**症状**：workflow.log 长时间无更新或出现错误

**诊断步骤**：

```bash
# 1. 查看实时日志
tail -f workspaces/<workspace-name>/workflow.log

# 2. 检查 Docker 容器状态
docker ps | grep shannon-worker

# 3. 检查 Temporal 状态
# 访问 http://localhost:8233 查看 workflow 历史
```

**常见原因**：

| 原因 | 解决方案 |
|------|----------|
| API 凭证无效 | 检查 ANTHROPIC_API_KEY 或重新运行 setup |
| 网络问题 | 检查网络连接，确认能访问 Anthropic API |
| 仓库路径错误 | 确认 repo 路径存在且可读 |

---

### 扫描结果为空

**症状**：comprehensive_security_assessment_report.md 中没有发现漏洞

**可能原因**：

1. **应用确实安全** - 某些简单应用可能没有明显的可利用漏洞
2. **白盒模式限制** - 白盒模式跳过 XSS 分析，且不执行实际利用验证
3. **配置规则过滤** - 检查配置文件中的 `rules.avoid` 和 `report` 过滤器

**验证方法**：

```bash
# 查看 exploitation_queue 内容
cat <repo>/.shannon/deliverables/*_exploitation_queue.json | jq .

# 如果队列有内容但报告为空，可能是报告生成阶段的问题
# 检查 workflow.log 中报告相关日志
```

---

## 实际案例：OWASP Juice Shop

### 完整流程：白盒 → 黑盒

#### 步骤 1：准备靶场

```bash
# 下载源码
git clone https://github.com/juice-shop/juice-shop.git
cd juice-shop

# 确认目录结构
ls -la
# 应该看到：app.ts, package.json, routes/ 等
```

#### 步骤 2：白盒扫描

```bash
# 本地模式
./shannon start -r /path/to/juice-shop --whitebox-only -w juice-shop-whitebox

# npx 模式
npx @keygraph/shannon start -r /path/to/juice-shop --whitebox-only -w juice-shop-whitebox
```

**预期时间**：约 20-30 分钟

**验证白盒完成**：

```bash
# 检查 deliverables
ls juice-shop/.shannon/deliverables/
# 应该看到各类 deliverable 文件
```

#### 步骤 3：启动靶场环境

```bash
# 启动 Juice Shop
docker run -d -p 3000:3000 --name juice-shop bkimminich/juice-shop

# 等待服务就绪
sleep 10

# 验证可访问
curl http://localhost:3000
```

#### 步骤 4：黑盒扫描

```bash
# 本地模式
./shannon start -u http://host.docker.internal:3000 -r /path/to/juice-shop --blackbox-only -w juice-shop-blackbox

# npx 模式
npx @keygraph/shannon start -u http://host.docker.internal:3000 -r /path/to/juice-shop --blackbox-only -w juice-shop-blackbox
```

**预期时间**：约 30-60 分钟（取决于发现的漏洞数量）

#### 步骤 5：查看结果

```bash
# 查看最终报告
cat juice-shop/.shannon/deliverables/comprehensive_security_assessment_report.md
```

**清理环境**：

```bash
# 停止并删除容器
docker stop juice-shop && docker rm juice-shop
```
