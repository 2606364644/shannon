from shannon_core.code_index.models import EntryPoint, FuncBlock
from shannon_core.code_index.recon_gitnexus_track import scan_endpoint_security


def _block(handler_id, source, decorators=None):
    file_path, func_name, line = handler_id.rsplit(":", 2)
    return FuncBlock(
        id=handler_id,
        file_path=file_path,
        function_name=func_name,
        start_line=int(line),
        end_line=int(line) + 5,
        source_code=source,
        parameters=[],
        decorators=decorators or [],
        language="typescript",
    )


def _ep(handler_id, path, method="GET", auth=None):
    return EntryPoint(
        func_block_id=handler_id,
        entry_type="http_route",
        route=path,
        http_method=method,
        confidence=0.9,
        evidence="",
        needs_llm_review=False,
        authentication=auth,
    )


def test_auth_present_when_decorator_matches():
    ep = _ep("u.js:getProfile:45", "/api/users/me")
    block = _block("u.js:getProfile:45", "@UseGuards(AuthGuard)\nexport function getProfile() { }")
    out = scan_endpoint_security([ep], {"u.js:getProfile:45": block})
    assert len(out) == 1
    assert out[0].auth == "present"


def test_auth_none_when_no_guard_keyword():
    ep = _ep("u.js:public:1", "/api/open")
    block = _block("u.js:public:1", "export function public() { return data; }")
    out = scan_endpoint_security([ep], {"u.js:public:1": block})
    assert len(out) == 1
    assert out[0].auth == "none"


def test_middleware_extracted_from_source():
    ep = _ep("a.js:admin:5", "/api/admin")
    block = _block("a.js:admin:5", "router.use(requireAdmin);\nexport function admin() {}")
    out = scan_endpoint_security([ep], {"a.js:admin:5": block})
    assert "requireAdmin" in out[0].middleware


def test_ownership_guarded_when_orm_predicate_present():
    ep = _ep("u.js:update:10", "/api/users/:id", method="PUT")
    block = _block(
        "u.js:update:10",
        "async function update(req) {\n"
        "  const row = await db.user.findFirst({ where: { userId: req.user.id } });\n"
        "}",
    )
    out = scan_endpoint_security([ep], {"u.js:update:10": block})
    assert out[0].ownership == "guarded"
    assert out[0].ownership_evidence is not None
    assert "userId" in out[0].ownership_evidence


def test_ownership_none_when_no_predicate():
    ep = _ep("u.js:list:1", "/api/users")
    block = _block("u.js:list:1", "async function list(req) { return db.user.findMany(); }")
    out = scan_endpoint_security([ep], {"u.js:list:1": block})
    assert out[0].ownership == "none"


def test_missing_handler_block_yields_unknown_auth():
    ep = _ep("x.js:ghost:1", "/api/ghost")
    out = scan_endpoint_security([ep], {})
    assert len(out) == 1
    assert out[0].auth == "unknown"
    assert out[0].ownership == "unknown"


def test_decorators_field_used_as_supplementary_signal():
    ep = _ep("Ctrl.java:show:20", "/api/show")
    block = _block(
        "Ctrl.java:show:20",
        "public void show() {}",
        decorators=["@PreAuthorize(\"hasRole('USER')\")"],
    )
    out = scan_endpoint_security([ep], {"Ctrl.java:show:20": block})
    assert out[0].auth == "present"
