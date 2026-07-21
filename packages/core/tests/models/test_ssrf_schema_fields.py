from supernova_core.models.queue_schemas import SsrfVulnerability, VulnerabilityQueue


def _ssrf(**kw):
    return SsrfVulnerability(
        ID="S1", vulnerability_type="URL_Manipulation",
        externally_exploitable=True, confidence="high", **kw,
    )


def test_ssrf_has_path_verdict_witness_payload_defaults_none():
    v = _ssrf()
    assert v.path is None
    assert v.verdict is None
    assert v.witness_payload is None


def test_ssrf_accepts_new_fields():
    v = _ssrf(path="req.query.url -> fetch(L12)", verdict="vulnerable",
              witness_payload="http://127.0.0.1:22/")
    assert v.path == "req.query.url -> fetch(L12)"
    assert v.verdict == "vulnerable"
    assert v.witness_payload == "http://127.0.0.1:22/"


def test_ssrf_legacy_queue_without_new_fields_parses():
    content = ('{"vulnerabilities":[{"ID":"S1","vulnerability_type":"URL_Manipulation",'
               '"externally_exploitable":true,"confidence":"high"}]}')
    result = VulnerabilityQueue.parse_lenient(content)
    assert len(result.queue.vulnerabilities) == 1
    v = result.queue.vulnerabilities[0]
    assert v.path is None and v.verdict is None and v.witness_payload is None
