"""Bounded navigation, deterministic validation, and cache fingerprints."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from pydantic import ValidationError

from supernova_core.models.topology import (
    Confidence,
    NavigationClue,
    NavigationManifest,
    NavigationManifestLimits,
    NormalizedTopologyResult,
    RepositoryNavigationManifest,
    TopologyAnalysisResult,
    TopologyCandidateEdge,
    TopologyCoverage,
    TopologyEvidence,
    TopologyFingerprint,
    TopologyInvalidItem,
    TopologyNode,
    TopologyRepoFingerprint,
    TopologyUncertainClue,
)

_IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "__pycache__", "node_modules",
    "dist", "build", "target", ".next", ".turbo", ".pytest_cache", "vendor",
}
_TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".kt", ".rs", ".rb",
    ".php", ".cs", ".proto", ".graphql", ".gql", ".json", ".yaml", ".yml",
    ".toml", ".xml", ".properties", ".env", ".md", ".txt", ".tf", ".hcl",
}
_LANGUAGE_SUFFIXES = {
    ".py": "python", ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".go": "go",
    ".java": "java", ".kt": "kotlin", ".rs": "rust", ".rb": "ruby",
    ".php": "php", ".cs": "c#", ".proto": "protobuf",
}
_FRAMEWORK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("next", re.compile(r"\bnext(?:\.js)?\b", re.I)),
    ("react", re.compile(r"\bfrom\s+['\"]react['\"]", re.I)),
    ("express", re.compile(r"\bexpress\(|\bfrom\s+['\"]express['\"]", re.I)),
    ("axios", re.compile(r"\baxios\.", re.I)),
    ("fastapi", re.compile(r"\bFastAPI\s*\(", re.I)),
    ("flask", re.compile(r"\bFlask\s*\(", re.I)),
    ("django", re.compile(r"\bdjango\b|\bDJANGO_", re.I)),
    ("spring", re.compile(r"\bspringframework\b|@Rest?Controller\b")),
    ("grpc", re.compile(r"\bgrpc\b|\bgRPC\b", re.I)),
    ("graphql", re.compile(r"\bGraphQL\b|\bgraphql\b", re.I)),
)


def _safe_repo_paths(repo_paths: Mapping[str, Path]) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for name, path in repo_paths.items():
        resolved = Path(path).resolve()
        if not resolved.is_dir():
            raise ValueError(f"repository path is not a directory: {name}")
        paths[name] = resolved
    return paths


def _iter_manifest_files(root: Path, max_files: int) -> tuple[list[Path], bool]:
    files: list[Path] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in _IGNORED_DIRS)
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
                continue
            if len(files) >= max_files:
                truncated = True
                return files, truncated
            files.append(path)
    return files, truncated


def _read_head(path: Path, limit: int) -> str:
    with path.open("rb") as fh:
        data = fh.read(limit)
    return data.decode("utf-8", errors="replace")


def _package_name(path: Path, text: str) -> str | None:
    if path.name == "package.json":
        try:
            return json.loads(text).get("name")
        except (json.JSONDecodeError, TypeError):
            return None
    if path.name == "go.mod":
        match = re.search(r"^module\s+(\S+)", text, re.M)
        return match.group(1) if match else None
    if path.name == "pyproject.toml":
        match = re.search(r'^name\s*=\s*["\']([^"\']+)', text, re.M)
        return match.group(1) if match else None
    if path.name == "setup.py":
        match = re.search(r'name=["\']([^"\']+)', text)
        return match.group(1) if match else None
    return None


def _clue(kind: str, path: Path, root: Path, line_no: int, line: str,
          value: str | None, snippet_chars: int) -> NavigationClue:
    return NavigationClue(
        kind=kind,
        path=path.relative_to(root).as_posix(),
        line=line_no,
        snippet=line.strip()[:snippet_chars],
        value=value,
    )


def _collect_repo(name: str, root: Path, limits: NavigationManifestLimits) -> RepositoryNavigationManifest:
    files, truncated = _iter_manifest_files(root, limits.max_files)
    languages: Counter[str] = Counter()
    frameworks: set[str] = set()
    service: list[NavigationClue] = []
    client: list[NavigationClue] = []
    config: list[NavigationClue] = []
    package_name: str | None = None

    for path in files:
        suffix = path.suffix.lower()
        if suffix in _LANGUAGE_SUFFIXES and suffix != ".proto":
            languages[_LANGUAGE_SUFFIXES[suffix]] += 1
        text = _read_head(path, limits.max_file_bytes)
        if package_name is None:
            package_name = _package_name(path, text)
        for framework, pattern in _FRAMEWORK_PATTERNS:
            if pattern.search(text):
                frameworks.add(framework)
        for line_no, line in enumerate(text.splitlines(), 1):
            for match in re.finditer(r"^\s*service\s+(\w+)", line):
                service.append(_clue("proto-service", path, root, line_no, line,
                                     match.group(1), limits.max_snippet_chars))
            if re.search(r"\b(new\s+\w+Client|grpc\.client|GrpcClient|ServiceStub)\b", line):
                client.append(_clue("grpc-client", path, root, line_no, line, None,
                                    limits.max_snippet_chars))
            if re.search(r"\b(HttpClient|axios\.|fetch\s*\(|RestTemplate|WebClient|BaseAddress)\b", line, re.I):
                client.append(_clue("http-client", path, root, line_no, line, None,
                                    limits.max_snippet_chars))
            if re.search(r"\b(GraphQLClient|gql\s*\()", line, re.I):
                client.append(_clue("graphql-client", path, root, line_no, line, None,
                                    limits.max_snippet_chars))
            if re.search(r"@(?:Get|Post|Put|Delete|Patch)Mapping|@(?:Rest)?Controller|type\s+Query\s*\{|schema\s*\{", line):
                service.append(_clue("service-endpoint", path, root, line_no, line, None,
                                     limits.max_snippet_chars))
            if re.search(r"\b(?:app|router)\.(?:get|post|put|delete|patch|route)\s*\(", line):
                service.append(_clue("http-route", path, root, line_no, line, None,
                                     limits.max_snippet_chars))
            if re.search(r"\b[A-Z][A-Z0-9_]*(?:_URL|_HOST|_SERVICE)\b|https?://\S+", line):
                config.append(_clue("upstream-config", path, root, line_no, line, None,
                                    limits.max_snippet_chars))
            if len(service) + len(client) + len(config) >= limits.max_clues_per_repo:
                truncated = True
                break
        if truncated:
            break

    # Keep all clue lists bounded even when several patterns hit one large file.
    service = service[:limits.max_clues_per_repo]
    client = client[:limits.max_clues_per_repo]
    config = config[:limits.max_clues_per_repo]
    return RepositoryNavigationManifest(
        repo=name,
        path=str(root),
        language=languages.most_common(1)[0][0] if languages else None,
        package_name=package_name,
        frameworks=sorted(frameworks),
        service_clues=service,
        client_clues=client,
        config_clues=config,
        scanned_files=len(files),
        truncated=truncated,
    )


def _manifest_size(manifest: NavigationManifest) -> int:
    return len(manifest.model_dump_json(exclude={"limits": True, "approximate_size": True}, exclude_none=True))


def _bound_manifest(manifest: NavigationManifest, limits: NavigationManifestLimits) -> NavigationManifest:
    if _manifest_size(manifest) <= limits.max_output_chars:
        return manifest
    manifest.truncated = True
    for repo in manifest.by_repo.values():
        keep = max(0, len(repo.all_clues()) // 2)
        repo.service_clues = repo.service_clues[:keep]
        repo.client_clues = repo.client_clues[:keep]
        repo.config_clues = repo.config_clues[:keep]
        repo.truncated = True
    while _manifest_size(manifest) > limits.max_output_chars:
        reduced = False
        for repo in manifest.by_repo.values():
            if repo.service_clues:
                repo.service_clues.pop()
                reduced = True
            if repo.client_clues:
                repo.client_clues.pop()
                reduced = True
            if repo.config_clues:
                repo.config_clues.pop()
                reduced = True
            if _manifest_size(manifest) <= limits.max_output_chars:
                break
        if not reduced:
            for repo in manifest.by_repo.values():
                repo.frameworks = []
                repo.package_name = None
            break
    manifest.approximate_size = _manifest_size(manifest)
    return manifest


def collect_navigation_manifest(
    repo_paths: Mapping[str, Path], *,
    limits: NavigationManifestLimits | None = None,
) -> NavigationManifest:
    """Collect bounded navigation facts without source/sink or vulnerability hints."""
    safe_paths = _safe_repo_paths(repo_paths)
    active_limits = limits or NavigationManifestLimits()
    repositories = sorted(safe_paths)
    by_repo = {
        name: _collect_repo(name, path, active_limits)
        for name, path in safe_paths.items()
    }
    manifest = NavigationManifest(
        repositories=repositories,
        by_repo=by_repo,
        limits=active_limits,
        truncated=any(item.truncated for item in by_repo.values()),
    )
    manifest.approximate_size = _manifest_size(manifest)
    return _bound_manifest(manifest, active_limits)


_CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
_DOWN_GRADE = {"high": "medium", "medium": "low", "low": "low"}


def _validate_evidence(
    evidence: TopologyEvidence, repo_roots: Mapping[str, Path],
) -> TopologyEvidence:
    errors: list[str] = []
    root = repo_roots.get(evidence.repo)
    if root is None:
        errors.append("unknown evidence repository")
    else:
        candidate = Path(evidence.file)
        resolved = candidate if candidate.is_absolute() else root / candidate
        try:
            resolved = resolved.resolve(strict=False)
            inside = resolved.is_relative_to(root)
        except OSError:
            inside = False
        if not inside:
            errors.append("evidence path escapes repository")
        elif not resolved.is_file():
            errors.append("evidence file does not exist")
        else:
            try:
                lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                lines = []
                errors.append("evidence file is not readable")
            if evidence.line is None or evidence.line < 1 or evidence.line > max(len(lines), 1):
                errors.append("evidence line is outside file")
            elif evidence.snippet and evidence.snippet not in lines[evidence.line - 1]:
                errors.append("evidence snippet is not on the stated line")
    return evidence.model_copy(update={
        "valid": not errors,
        "validation_errors": errors,
        "file": evidence.file.replace("\\", "/"),
    })


def _edge_raw(edge: TopologyCandidateEdge) -> dict[str, Any]:
    return edge.model_dump(by_alias=True, exclude_none=True)


def _invalid(reason: str, message: str, raw: dict[str, Any]) -> TopologyInvalidItem:
    return TopologyInvalidItem(reason=reason, message=message, raw=raw)  # type: ignore[arg-type]


def _downgrade(confidence: Confidence) -> Confidence:
    return _DOWN_GRADE[confidence]  # type: ignore[index]


def _merge_edge(existing: TopologyCandidateEdge, candidate: TopologyCandidateEdge) -> TopologyCandidateEdge:
    if _CONFIDENCE_ORDER[candidate.confidence] > _CONFIDENCE_ORDER[existing.confidence]:
        return candidate
    return existing.model_copy(update={
        "client_evidence": existing.client_evidence or candidate.client_evidence,
        "handler_evidence": existing.handler_evidence or candidate.handler_evidence,
    })


def normalize_topology_result(
    raw: Mapping[str, Any] | TopologyAnalysisResult,
    repo_paths: Mapping[str, Path],
) -> NormalizedTopologyResult:
    """Validate agent output while retaining rejected/uncertain data for audit."""
    safe_paths = _safe_repo_paths(repo_paths)
    selected = set(safe_paths)
    if isinstance(raw, TopologyAnalysisResult):
        parsed = raw.model_copy(deep=True)
    else:
        try:
            parsed = TopologyAnalysisResult.model_validate(dict(raw))
        except ValidationError as exc:
            # pydantic v2 ValidationError 无 error_message()（曾调之必炸 AttributeError，
            # 被 web _run 的 except Exception 吞成 provider_failed——2026-09-03 迁移
            # worker 的 TDD 首次暴露）。str(exc) 含完整字段级错误描述，审计可读。
            invalid = [TopologyInvalidItem(
                reason="malformed_output", message=str(exc), raw={}
            )]
            return NormalizedTopologyResult(invalid=invalid)
    invalid: list[TopologyInvalidItem] = []
    uncertain = [item for item in parsed.uncertain if item.repo in selected]
    coverage: list[TopologyCoverage] = [
        item for item in parsed.coverage if item.repo in selected
    ]
    covered_names = {item.repo for item in coverage}
    for name in sorted(selected - covered_names):
        coverage.append(TopologyCoverage(repo=name, complete=False, reason="not reported"))

    roles_by_repo: dict[str, list[str]] = {}
    capabilities_by_repo: dict[str, list] = {}
    for node in parsed.nodes:
        if node.repo not in selected:
            invalid.append(_invalid("unknown_node", f"unselected node: {node.repo}",
                                    {"repo": node.repo}))
            continue
        roles = list(dict.fromkeys(node.roles))
        roles_by_repo[node.repo] = roles
        capabilities = []
        for capability in node.capabilities:
            evidence = [_validate_evidence(item, safe_paths) for item in capability.evidence]
            confidence = capability.confidence
            if any(item.valid is False for item in evidence):
                invalid.append(_invalid("invalid_evidence", "capability contains invalid evidence", {
                    "repo": node.repo, "role": capability.role,
                }))
                confidence = _downgrade(confidence)
            capabilities.append(capability.model_copy(update={
                "evidence": evidence, "confidence": confidence,
            }))
        capabilities_by_repo[node.repo] = capabilities
    nodes = [
        TopologyNode(repo=name, roles=roles_by_repo.get(name, []),
                     capabilities=capabilities_by_repo.get(name, []))
        for name in sorted(roles_by_repo)
    ]

    merged: dict[tuple[str, str, str], TopologyCandidateEdge] = {}
    for edge in parsed.edges:
        if edge.from_ not in selected or edge.to not in selected:
            invalid.append(_invalid("unknown_node", "edge references an unselected repository",
                                    _edge_raw(edge)))
            continue
        if edge.from_ == edge.to:
            invalid.append(_invalid("self_loop", f"self-loop: {edge.from_}", _edge_raw(edge)))
            continue
        if edge.protocol not in ("grpc", "http", "graphql"):
            invalid.append(_invalid("invalid_protocol", f"unsupported protocol: {edge.protocol}",
                                    _edge_raw(edge)))
            uncertain.append(TopologyUncertainClue(
                repo=edge.from_,
                message=f"Unconfirmed {edge.protocol} call to {edge.to}",
                protocol_hint=edge.protocol,
                evidence=edge.client_evidence,
            ))
            continue

        client = [_validate_evidence(item, safe_paths) for item in edge.client_evidence]
        handler = [_validate_evidence(item, safe_paths) for item in edge.handler_evidence]
        normalized = edge.model_copy(update={
            "client_evidence": client, "handler_evidence": handler
        })
        evidence_lists = [*client, *handler]
        if any(item.valid is False for item in evidence_lists):
            invalid.append(_invalid("invalid_evidence", "edge contains invalid evidence",
                                    _edge_raw(normalized)))
            normalized = normalized.model_copy(update={"confidence": "low"})
        if normalized.confidence == "high" and not any(item.valid for item in client):
            normalized = normalized.model_copy(update={"confidence": "medium"})
        key = (normalized.from_, normalized.to, normalized.protocol)
        if key in merged:
            invalid.append(_invalid("duplicate_edge", f"duplicate edge identity: {key}", _edge_raw(normalized)))
            merged[key] = _merge_edge(merged[key], normalized)
        else:
            merged[key] = normalized

    return NormalizedTopologyResult(
        nodes=nodes,
        edges=list(merged.values()),
        uncertain=uncertain,
        coverage=coverage,
        usage=parsed.usage,
        invalid=invalid,
        raw=parsed,
    )


def _run_git(repo: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args], check=False, timeout=5,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        return proc.stdout if proc.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _bounded_non_git_hash(root: Path) -> str:
    digest = hashlib.sha256()
    entries = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in _IGNORED_DIRS)
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            try:
                stat = path.stat()
            except OSError:
                continue
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(str(stat.st_size).encode())
            digest.update(str(stat.st_mtime_ns).encode())
            entries += 1
            if entries >= 5000:
                digest.update(b":truncated")
                return digest.hexdigest()
    return digest.hexdigest()


def build_topology_fingerprint(
    repo_paths: Mapping[str, Path], *, protocol_version: str = "cross-repo-topology-v1",
) -> TopologyFingerprint:
    """Hash sorted repo identities, Git HEAD/dirty state, and bounded non-git metadata."""
    safe_paths = _safe_repo_paths(repo_paths)
    repos: dict[str, TopologyRepoFingerprint] = {}
    canonical: list[str] = []
    for name in sorted(safe_paths):
        root = safe_paths[name]
        head = _run_git(root, "rev-parse", "HEAD")
        if head is None:
            fallback = _bounded_non_git_hash(root)
            item = TopologyRepoFingerprint(git_head=None, dirty=False, fallback_hash=fallback)
            canonical.append(f"{name}:nogit:{fallback}")
        else:
            clean_head = head.strip()
            status = _run_git(root, "status", "--porcelain", "--untracked-files=all") or ""
            dirty = bool(status.strip())
            bounded_status = status[:65536]
            item = TopologyRepoFingerprint(git_head=clean_head, dirty=dirty)
            canonical.append(f"{name}:git:{clean_head}:{dirty}:{hashlib.sha256(bounded_status.encode()).hexdigest()}")
        repos[name] = item
    value = hashlib.sha256(
        (f"{protocol_version}\n" + "\n".join(canonical)).encode("utf-8")
    ).hexdigest()
    return TopologyFingerprint(
        protocol_version=protocol_version, repos=repos, value=value
    )
