# Spec 3: 产出物/路径配置

**日期**: 2026-06-07
**状态**: 已批准

## 背景

`shannon-py` 已在 pipeline 输入模型中有 `deliverables_subdir` 字段，支持通过代码/CLI 参数配置。但缺少环境变量入口，用户无法通过 `.env` 统一设置默认路径。

原始项目通过 `SHANNON_DELIVERABLES_SUBDIR` 和 `SHANNON_WORKER_ROOT` 两个环境变量解决此问题。

## 目标

新增 `SHANNON_DELIVERABLES_SUBDIR` 和 `SHANNON_WORKER_ROOT` 环境变量，将路径配置暴露到 `.env` 层面。

## 环境变量

```bash
# =============================================================================
# 路径配置（可选）
# =============================================================================

# 产出物存储子目录（相对于目标仓库根目录，默认 .shannon/deliverables）
# SHANNON_DELIVERABLES_SUBDIR=.shannon/deliverables

# Worker 基准目录（用于解析相对路径，默认当前工作目录）
# SHANNON_WORKER_ROOT=/path/to/worker/root
```

## 解析优先级

```
deliverables_subdir:
  CLI --output > pipeline 参数 > SHANNON_DELIVERABLES_SUBDIR > DEFAULT_DELIVERABLES_SUBDIR

workspaces_dir:
  CLI --workspaces-dir > SHANNON_WORKER_ROOT/workspaces > resolve_workspaces_dir() 默认值
```

## 改动文件

### 1. `packages/core/src/shannon_core/utils/paths.py`

新增环境变量感知的默认值获取函数：

```python
def get_default_deliverables_subdir() -> str:
    """从环境变量获取默认产出物子目录"""
    return os.getenv("SHANNON_DELIVERABLES_SUBDIR", DEFAULT_DELIVERABLES_SUBDIR)


def resolve_workspaces_dir(repo_path: str | None = None) -> Path:
    """解析 workspaces 目录（增强版）"""
    worker_root = os.getenv("SHANNON_WORKER_ROOT")
    if worker_root:
        return Path(worker_root) / "workspaces"
    # ... 原有逻辑不变 ...
```

### 2. `packages/core/src/shannon_core/models/base.py`

修改 `BasePipelineInput` 中的默认值来源：

```python
@dataclass
class BasePipelineInput:
    # ...
    deliverables_subdir: str = field(default_factory=get_default_deliverables_subdir)
```

使用 `field(default_factory=...)` 替代硬编码默认值，使环境变量在 dataclass 实例化时生效。

### 3. `.env.example`

新增路径配置部分（见上方环境变量章节）。

## 注意事项

- `SHANNON_WORKER_ROOT` 仅影响相对路径的基准目录，不影响绝对路径
- 现有 CLI 参数（`--output`、`--workspace`）优先级始终高于环境变量
- 当 Spec 2（Docker Worker）启用时，容器内会通过环境变量传入路径配置

## 测试要点

- 设置 `SHANNON_DELIVERABLES_SUBDIR=custom/output`，验证产出物写入指定目录
- 设置 `SHANNON_WORKER_ROOT=/tmp/shannon`，验证 workspaces 在 `/tmp/shannon/workspaces` 下创建
- 不设置环境变量，验证使用现有默认值（`.shannon/deliverables`、`<repo_parent>/workspaces`）
- CLI `--output` 参数优先于环境变量
