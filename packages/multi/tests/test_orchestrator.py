from shannon_core.models.multi_repo_config import MultiRepoConfig, RepoSpec, Relation, CorrelationConfig
from shannon_multi.orchestrator import plan_repo_scans, RepoScanPlan


def _cfg(**overrides):
    repos = {
        "gateway": RepoSpec(path="/r/gw", role="entrypoint"),
        "order-svc": RepoSpec(path="/r/order", workspace="existing-order", role="backend"),
        "payment-svc": RepoSpec(path="/r/pay", role="backend"),
    }
    return MultiRepoConfig(
        repos=repos,
        relations=[Relation(**{"from": "gateway", "to": "order-svc"})],
        correlation=CorrelationConfig(out_workspace="out"),
        **overrides,
    )


def test_reuse_when_workspace_declared():
    plans = plan_repo_scans(_cfg())
    by_svc = {p.service: p for p in plans}
    # order-svc 声明了 workspace → 复用
    assert by_svc["order-svc"].reuse is True
    assert by_svc["order-svc"].workspace == "existing-order"
    # gateway 只给 path → 现扫
    assert by_svc["gateway"].reuse is False
    assert by_svc["gateway"].repo_path == "/r/gw"


def test_all_three_repos_planned():
    plans = plan_repo_scans(_cfg())
    assert {p.service for p in plans} == {"gateway", "order-svc", "payment-svc"}
