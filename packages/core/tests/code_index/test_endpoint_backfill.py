# packages/core/tests/code_index/test_endpoint_backfill.py
"""F6-B endpoint 白名单验证回填（spec 2026-09-03 §3）：LLM 提名 + 全量路由
白名单验证 + 唯一命中采信；白名单外丢弃 / 多候选歧义不采信（宁缺勿错拼）。"""
from supernova_core.code_index.endpoint_backfill import backfill_endpoints
from supernova_core.models.queue_schemas import XssVulnerability

ROUTES = {"POST /login", "POST /signup", "GET /allocations/:userId"}


def _card(id_, title, evidence):
    return XssVulnerability(
        ID=id_, vulnerability_type="Reflected", externally_exploitable=True,
        confidence="low", source="p (app/routes/session.js:SessionHandler:8)",
        title=title, evidence_chain=evidence, verdict="vulnerable")


def test_backfill_unique_hit():
    cards = backfill_endpoints([_card("G", "反射型 XSS：POST /login 的 userName ...", "autoescape:false")], ROUTES)
    assert cards[0].endpoint == "POST /login"


def test_backfill_placeholder_normalized():
    cards = backfill_endpoints([_card("G", "XSS: GET /allocations/:userId 的 userId ...", "action 属性")],
                               {"GET /allocations/:userId"})
    assert cards[0].endpoint == "GET /allocations/:userId"


def test_backfill_outside_whitelist_dropped():
    cards = backfill_endpoints([_card("G", "XSS：POST /nonexistent ...", "x")], ROUTES)
    assert cards[0].endpoint is None


def test_backfill_already_set_untouched():
    card = _card("G", "POST /login ...", "x")
    card.endpoint = "POST /login"
    out = backfill_endpoints([card], set())
    assert out[0].endpoint == "POST /login"
