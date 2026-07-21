import textwrap
from pathlib import Path
import pytest
from pydantic import ValidationError

from supernova_core.models.multi_repo_config import MultiRepoConfig
from supernova_core.config.parser import parse_multi_repo_config


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "multi-repo.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_parse_valid_graph_config(tmp_path):
    p = _write(tmp_path, """
        description: "gw -> go grpc"
        repos:
          gateway:
            path: /r/gw
            role: entrypoint
          order-svc:
            path: /r/order
            role: backend
        relations:
          - from: gateway
            to: order-svc
            protocol: grpc
        correlation:
          out_workspace: my-corr
    """)
    cfg = parse_multi_repo_config(p)
    assert cfg.repos["gateway"].role == "entrypoint"
    assert cfg.repos["order-svc"].role == "backend"  # default
    assert cfg.relations[0].from_ == "gateway"
    assert cfg.relations[0].protocol == "grpc"
    assert cfg.correlation.out_workspace == "my-corr"


def test_missing_entrypoint_rejected(tmp_path):
    p = _write(tmp_path, """
        repos:
          a: {path: /r/a, role: backend}
        relations: []
        correlation: {out_workspace: o}
    """)
    with pytest.raises(ValidationError) as ei:
        parse_multi_repo_config(p)
    assert "entrypoint" in str(ei.value).lower()


def test_relation_ref_undeclared_rejected(tmp_path):
    p = _write(tmp_path, """
        repos:
          gateway: {path: /r/gw, role: entrypoint}
        relations:
          - {from: gateway, to: missing-svc}
        correlation: {out_workspace: o}
    """)
    with pytest.raises(ValidationError):
        parse_multi_repo_config(p)


def test_repo_without_path_or_workspace_rejected(tmp_path):
    p = _write(tmp_path, """
        repos:
          gateway: {role: entrypoint}
        relations: []
        correlation: {out_workspace: o}
    """)
    with pytest.raises(ValidationError):
        parse_multi_repo_config(p)


def test_protocol_enum(tmp_path):
    p = _write(tmp_path, """
        repos:
          gateway: {path: /r/gw, role: entrypoint}
          b: {path: /r/b}
        relations:
          - {from: gateway, to: b, protocol: http}
        correlation: {out_workspace: o}
    """)
    cfg = parse_multi_repo_config(p)
    assert cfg.relations[0].protocol == "http"
