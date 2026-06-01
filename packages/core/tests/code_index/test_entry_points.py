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
