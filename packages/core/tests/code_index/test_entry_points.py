from shannon_core.code_index.models import FuncBlock
from shannon_core.code_index.entry_points import detect_entry_points


def _block(**overrides) -> FuncBlock:
    defaults = dict(
        id="src/app.py:f:1",
        file_path="src/app.py",
        function_name="f",
        start_line=1,
        end_line=5,
        source_code="def f(): pass",
        parameters=[],
        language="python",
    )
    defaults.update(overrides)
    return FuncBlock(**defaults)


class TestPythonEntryPoints:
    def test_flask_route(self):
        block = _block(
            decorators=["@app.route('/api/users', methods=['GET'])"],
            function_name="list_users",
        )
        eps = detect_entry_points([block], "python")
        assert len(eps) == 1
        assert eps[0].entry_type == "http_route"
        assert eps[0].route == "/api/users"
        assert eps[0].http_method == "GET"
        assert eps[0].confidence == 0.95
        assert eps[0].needs_llm_review is False

    def test_flask_route_post(self):
        block = _block(
            decorators=["@app.route('/api/users', methods=['POST'])"],
            function_name="create_user",
        )
        eps = detect_entry_points([block], "python")
        assert len(eps) == 1
        assert eps[0].http_method == "POST"

    def test_fastapi_route(self):
        block = _block(
            decorators=["@router.get('/users')"],
            function_name="get_users",
        )
        eps = detect_entry_points([block], "python")
        assert len(eps) == 1
        assert eps[0].confidence == 0.95

    def test_django_view(self):
        block = _block(
            decorators=["@api_view(['GET'])"],
            function_name="user_list",
        )
        eps = detect_entry_points([block], "python")
        assert len(eps) == 1
        assert eps[0].confidence == 0.90

    def test_celery_task(self):
        block = _block(
            decorators=["@shared_task"],
            function_name="process_queue",
        )
        eps = detect_entry_points([block], "python")
        assert len(eps) == 1
        assert eps[0].entry_type == "message_consumer"
        assert eps[0].confidence == 0.90

    def test_async_undecorated_needs_review(self):
        block = _block(
            source_code="async def process(): pass",
            function_name="process",
        )
        eps = detect_entry_points([block], "python")
        assert len(eps) == 1
        assert eps[0].needs_llm_review is True
        assert eps[0].confidence == 0.40

    def test_async_private_function_excluded(self):
        block = _block(
            source_code="async def _internal(): pass",
            function_name="_internal",
        )
        eps = detect_entry_points([block], "python")
        assert len(eps) == 0

    def test_async_in_test_file_excluded(self):
        block = _block(
            source_code="async def test_handler(): pass",
            function_name="test_handler",
            file_path="tests/test_app.py",
        )
        eps = detect_entry_points([block], "python")
        assert len(eps) == 0

    def test_async_in_conftest_excluded(self):
        block = _block(
            source_code="async def setup_fixtures(): pass",
            function_name="setup_fixtures",
            file_path="conftest.py",
        )
        eps = detect_entry_points([block], "python")
        assert len(eps) == 0

    def test_async_in_test_suffix_file_excluded(self):
        block = _block(
            source_code="async def helper(): pass",
            function_name="helper",
            file_path="src/app_test.py",
        )
        eps = detect_entry_points([block], "python")
        assert len(eps) == 0

    def test_async_in_spec_dir_excluded(self):
        block = _block(
            source_code="async def run_spec(): pass",
            function_name="run_spec",
            file_path="spec/runner.py",
        )
        eps = detect_entry_points([block], "python")
        assert len(eps) == 0

    def test_async_valid_candidate_detected(self):
        block = _block(
            source_code="async def handle_request(): pass",
            function_name="handle_request",
            file_path="app/handlers.py",
        )
        eps = detect_entry_points([block], "python")
        assert len(eps) == 1
        assert eps[0].confidence == 0.40
        assert eps[0].entry_type == "unknown"

    def test_plain_function_no_entry_point(self):
        block = _block(function_name="helper")
        eps = detect_entry_points([block], "python")
        assert len(eps) == 0


class TestGoEntryPoints:
    def test_net_http_handler(self):
        block = _block(
            parameters=["w http.ResponseWriter", "r *http.Request"],
            function_name="handleUsers",
            language="go",
        )
        eps = detect_entry_points([block], "go")
        assert len(eps) == 1
        assert eps[0].entry_type == "http_route"
        assert eps[0].confidence == 0.95

    def test_gin_handler(self):
        block = _block(
            parameters=["c *gin.Context"],
            function_name="handleUsers",
            language="go",
        )
        eps = detect_entry_points([block], "go")
        assert len(eps) == 1
        assert eps[0].confidence == 0.95

    def test_plain_go_function_no_entry_point(self):
        block = _block(
            parameters=["x int", "y int"],
            function_name="add",
            language="go",
        )
        eps = detect_entry_points([block], "go")
        assert len(eps) == 0


class TestTypeScriptEntryPoints:
    def test_nestjs_get(self):
        block = _block(
            decorators=["@Get()"],
            function_name="listUsers",
            language="typescript",
        )
        eps = detect_entry_points([block], "typescript")
        assert len(eps) == 1
        assert eps[0].entry_type == "http_route"
        assert eps[0].confidence == 0.95

    def test_nestjs_post(self):
        block = _block(
            decorators=["@Post()"],
            function_name="createUser",
            language="typescript",
        )
        eps = detect_entry_points([block], "typescript")
        assert len(eps) == 1
        assert eps[0].http_method == "POST"

    def test_plain_ts_function_no_entry_point(self):
        block = _block(
            function_name="helper",
            language="typescript",
        )
        eps = detect_entry_points([block], "typescript")
        assert len(eps) == 0


class TestJavaEntryPoints:
    def test_spring_get_mapping(self):
        block = _block(
            decorators=["@GetMapping"],
            function_name="listUsers",
            language="java",
        )
        eps = detect_entry_points([block], "java")
        assert len(eps) == 1
        assert eps[0].entry_type == "http_route"
        assert eps[0].confidence == 0.95

    def test_spring_request_mapping(self):
        block = _block(
            decorators=['@RequestMapping("/api/users")'],
            function_name="users",
            language="java",
        )
        eps = detect_entry_points([block], "java")
        assert len(eps) == 1
        assert eps[0].confidence == 0.95

    def test_rabbit_listener(self):
        block = _block(
            decorators=['@RabbitListener(queues = "orders")'],
            function_name="processOrder",
            language="java",
        )
        eps = detect_entry_points([block], "java")
        assert len(eps) == 1
        assert eps[0].entry_type == "message_consumer"
        assert eps[0].confidence == 0.90


class TestPhpEntryPoints:
    def test_laravel_route_get(self):
        # Laravel routes are typically in Route::get('/path', ...) calls,
        # which are detected from source_code, not decorators.
        block = _block(
            source_code="Route::get('/api/users', function () { return getUsers(); });",
            function_name="getUsers",
            language="php",
        )
        eps = detect_entry_points([block], "php")
        # Route::get doesn't decorate getUsers, so this specific function shouldn't be detected

    def test_symfony_route_attribute(self):
        block = _block(
            decorators=["#[Route('/api/users', methods: ['GET'])]"],
            function_name="listUsers",
            language="php",
        )
        eps = detect_entry_points([block], "php")
        assert len(eps) == 1
        assert eps[0].confidence == 0.95

    def test_plain_php_no_entry_point(self):
        block = _block(
            function_name="helper",
            language="php",
        )
        eps = detect_entry_points([block], "php")
        assert len(eps) == 0


class TestUnknownLanguage:
    def test_unknown_language_returns_empty(self):
        block = _block(language="rust")
        eps = detect_entry_points([block], "rust")
        assert len(eps) == 0


class TestExpressEntryPoints:
    """Express.js route detection — Pass 1 (FuncBlock source_code scan)."""

    def test_express_app_get_in_func_block(self):
        """Routes registered inside a function body (e.g., NodeGoat's index(app, db))."""
        block = _block(
            id="src/routes.ts:setupRoutes:10",
            file_path="src/routes.ts",
            function_name="setupRoutes",
            start_line=10,
            source_code=(
                "app.get('/api/users', (req, res) => {\n"
                "  res.json(getUsers());\n"
                "});\n"
            ),
            language="typescript",
        )
        eps = detect_entry_points([block], "typescript")
        express_eps = [ep for ep in eps if ep.evidence.startswith("Express")]
        assert len(express_eps) == 1
        assert express_eps[0].entry_type == "http_route"
        assert express_eps[0].route == "/api/users"
        assert express_eps[0].http_method == "GET"
        assert express_eps[0].confidence == 0.90
        assert express_eps[0].needs_llm_review is False

    def test_express_router_post_in_func_block(self):
        block = _block(
            id="src/routes.ts:registerRoutes:5",
            file_path="src/routes.ts",
            function_name="registerRoutes",
            start_line=5,
            source_code=(
                "router.post('/api/users/:id', async (req, res) => {\n"
                "  const result = await saveUser(req.params.id, req.body);\n"
                "  res.json(result);\n"
                "});\n"
            ),
            language="typescript",
        )
        eps = detect_entry_points([block], "typescript")
        express_eps = [ep for ep in eps if ep.evidence.startswith("Express")]
        assert len(express_eps) == 1
        assert express_eps[0].http_method == "POST"
        assert express_eps[0].route == "/api/users/:id"
        assert express_eps[0].confidence == 0.90

    def test_express_app_all_route(self):
        block = _block(
            id="src/app.ts:catchAll:20",
            file_path="src/app.ts",
            function_name="catchAll",
            start_line=20,
            source_code="app.all('/api/*', (req, res, next) => { next(); });",
            language="typescript",
        )
        eps = detect_entry_points([block], "typescript")
        express_eps = [ep for ep in eps if ep.evidence.startswith("Express")]
        assert len(express_eps) == 1
        assert express_eps[0].http_method == "*"
        assert express_eps[0].confidence == 0.85

    def test_express_app_use_with_path(self):
        block = _block(
            id="src/app.ts:setup:1",
            file_path="src/app.ts",
            function_name="setup",
            start_line=1,
            source_code="app.use('/api', router);",
            language="typescript",
        )
        eps = detect_entry_points([block], "typescript")
        express_eps = [ep for ep in eps if ep.evidence.startswith("Express")]
        assert len(express_eps) == 1
        assert express_eps[0].http_method == "MIDDLEWARE"
        assert express_eps[0].route == "/api"
        assert express_eps[0].confidence == 0.80

    def test_express_app_use_without_path_excluded(self):
        """app.use() without a string path argument (framework middleware) is excluded."""
        block = _block(
            id="src/server.ts:setup:5",
            file_path="src/server.ts",
            function_name="setup",
            start_line=5,
            source_code="app.use(express.json());",
            language="typescript",
        )
        eps = detect_entry_points([block], "typescript")
        express_eps = [ep for ep in eps if ep.evidence.startswith("Express")]
        assert len(express_eps) == 0

    def test_express_app_use_bare_function_excluded(self):
        """app.use(bodyParser()) without route string is excluded."""
        block = _block(
            id="src/server.ts:middleware:3",
            file_path="src/server.ts",
            function_name="middleware",
            start_line=3,
            source_code="app.use(session({ secret: 'keyboard cat' }));",
            language="typescript",
        )
        eps = detect_entry_points([block], "typescript")
        express_eps = [ep for ep in eps if ep.evidence.startswith("Express")]
        assert len(express_eps) == 0

    def test_multiple_routes_in_one_block(self):
        """Multiple routes in one function (NodeGoat pattern)."""
        block = _block(
            id="src/routes.ts:register:1",
            file_path="src/routes.ts",
            function_name="register",
            start_line=1,
            source_code=(
                "app.get('/users', getUsersHandler);\n"
                "app.post('/users', createUserHandler);\n"
                "app.delete('/users/:id', deleteUserHandler);\n"
            ),
            language="typescript",
        )
        eps = detect_entry_points([block], "typescript")
        express_eps = [ep for ep in eps if ep.evidence.startswith("Express")]
        assert len(express_eps) == 3
        methods = {ep.http_method for ep in express_eps}
        assert methods == {"GET", "POST", "DELETE"}
        # All share the same func_block_id
        assert all(ep.func_block_id == block.id for ep in express_eps)

    def test_express_put_patch_delete(self):
        block = _block(
            id="src/routes.ts:crud:10",
            file_path="src/routes.ts",
            function_name="crud",
            start_line=10,
            source_code=(
                "router.put('/users/:id', updateHandler);\n"
                "router.patch('/users/:id', patchHandler);\n"
                "router.delete('/users/:id', deleteHandler);\n"
            ),
            language="typescript",
        )
        eps = detect_entry_points([block], "typescript")
        express_eps = [ep for ep in eps if ep.evidence.startswith("Express")]
        assert len(express_eps) == 3
        methods = {ep.http_method for ep in express_eps}
        assert methods == {"PUT", "PATCH", "DELETE"}

    def test_no_express_in_python_block(self):
        """Express patterns in Python files are not scanned."""
        block = _block(
            source_code="app.get('/api/users', handler)",
            function_name="setup",
            language="python",
        )
        eps = detect_entry_points([block], "python")
        express_eps = [ep for ep in eps if ep.evidence.startswith("Express")]
        assert len(express_eps) == 0
