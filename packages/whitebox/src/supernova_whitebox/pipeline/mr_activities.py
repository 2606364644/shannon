"""MR 增量扫描前置 activities（spec 2026-09-03 §3.1 步骤 2/3）。

run_mr_repo_prepare：fetch → checkout head → merge-base 解析（fail-fast）。
run_git_diff：git diff -U3 merge-base..head → DiffManifest + patch 落盘。
run_protection_removal_analysis / run_incremental_scope：后续接入。

产物集中 deliverables/whitebox/intermediate/mr/：
  diff_manifest.json / diff.patch / removed_protections.json / incremental_scope.json
"""

import json
import logging
import subprocess
from pathlib import Path

from temporalio import activity

from supernova_core.models.errors import ErrorCode, PentestError
from supernova_core.utils.paths import intermediate_dir
from supernova_whitebox.pipeline.shared import ActivityInput

logger = logging.getLogger(__name__)

MR_DIR_NAME = "mr"


def _mr_dir(input: ActivityInput) -> Path:
    """deliverables/whitebox/intermediate/mr/（与 _get_paths 同源的桶内路径）。"""
    from supernova_core.utils.paths import WHITEBOX_SUBDIR
    if input.workspace_path:
        deliverables = Path(input.workspace_path) / input.deliverables_subdir
    else:  # activity 直调（无 workspace_path）回落 repo 同级 workspaces —— 与测试/调试兼容
        deliverables = Path(input.repo_path).parent / "workspaces"
    track_dir = deliverables / WHITEBOX_SUBDIR
    mr_dir = intermediate_dir(track_dir) / MR_DIR_NAME
    mr_dir.mkdir(parents=True, exist_ok=True)
    return mr_dir


def _git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise PentestError(
            f"git {' '.join(args)} failed: {proc.stderr.strip()}",
            category="mr_prepare", error_code=ErrorCode.REPO_NOT_FOUND,
        )
    return proc.stdout.strip()


def _rev_parse(repo: Path, ref: str) -> str:
    out = _git(repo, "rev-parse", "--verify", ref + "^{commit}")
    if not out:
        raise PentestError(
            f"MR ref 解析失败: {ref}", category="mr_prepare",
            error_code=ErrorCode.REPO_NOT_FOUND,
        )
    return out


@activity.defn
async def run_mr_repo_prepare(input: ActivityInput) -> dict:
    """fetch（best-effort）→ 解析 base/head → merge-base → checkout head。

    fail-fast 语义（spec §7）：ref 解析不到 / 无共同祖先 → PentestError。
    """
    repo = Path(input.repo_path)
    base_ref = input.mr_base_ref or ""
    head_ref = input.mr_head_ref or ""
    if not base_ref or not head_ref:
        raise PentestError(
            "MR 模式需要 base_ref 与 head_ref", category="mr_prepare",
            error_code=ErrorCode.REPO_NOT_FOUND,
        )

    # fetch best-effort：本地仓无 remote（测试/本地调试）时忽略失败，本地 ref 已可用
    _git(repo, "fetch", "origin", head_ref, check=False)
    _git(repo, "fetch", "origin", base_ref, check=False)

    head_commit = _rev_parse(repo, head_ref)
    base_commit = _rev_parse(repo, base_ref)

    merge_base = _git(repo, "merge-base", base_commit, head_commit)
    if not merge_base:
        raise PentestError(
            f"无共同祖先（merge-base 为空）: {base_ref}..{head_ref}",
            category="mr_prepare", error_code=ErrorCode.REPO_NOT_FOUND,
        )

    _git(repo, "checkout", "-q", head_commit)
    logger.info("MR repo prepared: base=%s head=%s (merge-base=%s)",
                base_commit[:8], head_commit[:8], merge_base[:8])
    return {
        "base_commit": base_commit,
        "head_commit": head_commit,
        "merge_base": merge_base,
    }


@activity.defn
async def run_git_diff(input: ActivityInput) -> dict:
    """merge-base..head 的 unified diff → DiffManifest + patch 落盘。

    返回 stats + select_vuln_classes（child workflow 的 vuln 类选择输入）。
    自包含重解析 ref（activity 重试幂等，不依赖 repo_prepare 的内存返回）。
    """
    from supernova_core.mr_scan.diff_manifest import parse_unified_diff
    from supernova_core.mr_scan.incremental_scope import select_vuln_classes

    repo = Path(input.repo_path)
    head_commit = _rev_parse(repo, input.mr_head_ref or "")
    base_commit = _rev_parse(repo, input.mr_base_ref or "")
    merge_base = _git(repo, "merge-base", base_commit, head_commit)

    diff_text = _git(repo, "diff", "-U3", "--no-color", f"{merge_base}..{head_commit}")
    manifest = parse_unified_diff(diff_text, base_commit=merge_base, head_commit=head_commit)

    mr_dir = _mr_dir(input)
    (mr_dir / "diff_manifest.json").write_text(manifest.model_dump_json(indent=2))
    (mr_dir / "diff.patch").write_text(diff_text)

    return {
        "stats": manifest.stats.model_dump(),
        "selected_vuln_classes": select_vuln_classes(manifest),
        "base_commit": merge_base,
        "head_commit": head_commit,
    }


def _make_protection_llm_client(provider_config: dict | None = None):
    """run_claude_prompt 封装成 detect_removed_protections 的 LLMClient 契约。

    对齐 activities._make_gitnexus_llm_client：structured_output 优先还原 str 契约；
    env 关（is_gitnexus_llm_enabled False）返回 None → detect 内部静默降级。
    """
    from supernova_core.agents.runner import run_claude_prompt

    if not _gitnexus_llm_enabled():
        return None

    async def _client(prompt: str, **kwargs) -> str:
        result = await run_claude_prompt(
            prompt=prompt, repo_path="", model_tier="medium",
            structured_output_schema=kwargs.get("output_format"),
            provider_config=provider_config,
        )
        so = result.structured_output
        if so is not None:
            return json.dumps(so)
        return result.text
    return _client


def _gitnexus_llm_enabled() -> bool:
    from supernova_core.config.concurrency import is_gitnexus_llm_enabled
    return is_gitnexus_llm_enabled()


@activity.defn
async def run_protection_removal_analysis(input: ActivityInput) -> dict:
    """diff.patch → 删防护 LLM 判定 → removed_protections.json（spec §5.1）。

    降级（degraded=True）也落盘——来源 C 缺席但 A/B 不受影响，报告标注降级。
    """
    mr_dir = _mr_dir(input)
    patch_path = mr_dir / "diff.patch"
    if not patch_path.exists():
        raise PentestError(
            f"MR diff 产物缺失: {patch_path}", category="mr_prepare",
            error_code=ErrorCode.DELIVERABLE_NOT_FOUND,
        )

    from supernova_core.mr_scan.protection_removal import detect_removed_protections

    diff_text = patch_path.read_text()
    outcome = await detect_removed_protections(
        diff_text, llm_client=_make_protection_llm_client(input.provider_config),
    )
    payload = {
        "degraded": outcome.degraded,
        "protections": [p.model_dump() for p in outcome.protections],
    }
    (mr_dir / "removed_protections.json").write_text(json.dumps(payload, indent=2))
    return payload


@activity.defn
async def run_incremental_scope(input: ActivityInput) -> dict:
    """diff_manifest + removed_protections + code_index → IncrementalScope 落盘。

    在 child workflow 的 pre-recon 之后跑（head 索引已产）。
    """
    mr_dir = _mr_dir(input)
    from supernova_core.code_index.models import CodeIndex
    from supernova_core.mr_scan.diff_manifest import DiffManifest
    from supernova_core.mr_scan.incremental_scope import (
        RemovedProtection, build_incremental_scope,
    )

    manifest = DiffManifest.model_validate_json(
        (mr_dir / "diff_manifest.json").read_text())
    index_path = mr_dir.parent / "code_index.json"
    index = CodeIndex.model_validate_json(index_path.read_text())
    pgraph = index.parameter_graph or type(index.parameter_graph)()

    protections: list[RemovedProtection] = []
    rp_path = mr_dir / "removed_protections.json"
    if rp_path.exists():
        rp_data = json.loads(rp_path.read_text())
        protections = [RemovedProtection(**p) for p in rp_data.get("protections", [])]

    scope = build_incremental_scope(
        diff=manifest, index=index, pgraph=pgraph, removed_protections=protections,
    )
    (mr_dir / "incremental_scope.json").write_text(scope.model_dump_json(indent=2))
    return {
        "verdict_flow_count": len(scope.verdict_flow_ids),
        "selected_vuln_classes": scope.selected_vuln_classes,
        "new_entry_point_count": len(scope.new_entry_point_ids),
        "removed_protection_count": len(scope.removed_protection_flows),
    }
