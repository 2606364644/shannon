"""MR 模式与 WhiteboxScanWorkflow 的接线纯函数（spec 2026-09-03 §3.1/§5.2）。

供 workflows.py 消费点调用；全量扫描（mr_meta=None）零行为变化。
"""

from typing import Iterable


def resolve_mr_vuln_classes(
    input_classes: Iterable[str] | None,
    mr_meta: dict | None,
    cfg_classes: Iterable[str] | None = None,
) -> list[str] | None:
    """MR 模式的 vuln 类优先级：用户显式 override > MR 启发式 > （回落现有链）。

    返回 None = 非 MR 模式或无可消费值 → 调用方走 select_vuln_classes 原链。
    """
    if input_classes:
        return list(input_classes)            # 用户显式指定（CLI/env）最高
    if mr_meta and mr_meta.get("selected_vuln_classes"):
        return list(mr_meta["selected_vuln_classes"])
    if cfg_classes:
        return list(cfg_classes)              # YAML（仅非 MR 时经此；MR 启发式已在上一档拦截）
    return None


def build_mr_incremental_guidance(mr_meta: dict | None) -> str:
    """LLM 轨 vuln/pre-recon prompt 的增量引导段（spec §5.2）。

    铁律：只含 git 派生物（ref、diff 命令、落盘路径提示）——不含任何
    确定性层产物（flow/sink/scope），保持 LLM 轨自给自足。
    """
    if not mr_meta:
        return ""
    base = mr_meta.get("base_commit", "")
    head = mr_meta.get("head_commit", "")
    return (
        "\n\n--- 增量扫描上下文 ---\n"
        f"本次为合并请求（MR）增量扫描：base {base}..head {head}。\n"
        "unified diff 全文已落盘于 deliverables/whitebox/intermediate/mr/diff.patch，"
        "可自行读取；也可用 `git diff <base>..<head>` 查看变更。\n"
        "请聚焦新增/修改/删除的代码区域分析：新增代码是否引入漏洞、"
        "新增入口是否接通新攻击面、删除的行是否移除了安全防护。"
    )


def filter_flows_by_mr_scope(pgraph, scope_flow_ids):
    """GN verdict 候选按 IncrementalScope 过滤（spec §5.1）。

    scope_flow_ids 为 None → 原对象直通（全量扫描零开销）。**空集 ≠ None**：
    MR 扫描 scope 合成为空（增量内无链）时过滤为零候选——若把空集当直通，
    MR 会退化成全量判定（违背增量语义 + 撞容量窗口）。调用方
    （run_gitnexus_chain_verdict）已用 `is not None` 区分两种缺席。
    """
    if scope_flow_ids is None:
        return pgraph
    wanted = set(scope_flow_ids)
    return pgraph.model_copy(update={
        "taint_flows": [f for f in pgraph.taint_flows if f.flow_id in wanted],
    })


def load_mr_scope_flow_ids(deliverables_dir) -> set[str] | None:
    """读 intermediate/mr/incremental_scope.json；非 MR 扫描（产物不存在）→ None。"""
    import json
    from pathlib import Path

    scope_path = Path(deliverables_dir) / "intermediate" / "mr" / "incremental_scope.json"
    if not scope_path.exists():
        return None
    try:
        data = json.loads(scope_path.read_text())
        return set(data.get("verdict_flow_ids", []))
    except Exception:   # noqa: BLE001 —— 产物损坏按非 MR 直通，不阻塞全量判定
        return None
