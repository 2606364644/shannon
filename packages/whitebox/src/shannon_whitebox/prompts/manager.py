import re
from pathlib import Path

from shannon_core.models.config import DistributedConfig
from shannon_core.models.errors import ErrorCode, PentestError

class PromptManager:
    def __init__(self, prompts_dir: Path):
        self.prompts_dir = prompts_dir

    def load_sync(
        self,
        template_name: str,
        variables: dict[str, str],
        config: DistributedConfig | None = None,
        pipeline_testing: bool = False,
    ) -> str:
        base_dir = self.prompts_dir
        if pipeline_testing:
            base_dir = base_dir / "pipeline-testing"

        template_path = base_dir / f"{template_name}.txt"
        if not template_path.exists():
            raise PentestError(
                f"Prompt file not found: {template_path}",
                "prompt",
                error_code=ErrorCode.PROMPT_LOAD_FAILED,
                context={"template_name": template_name},
            )

        template = template_path.read_text(encoding="utf-8")
        template = self._process_includes(template, base_dir)
        template = self._interpolate(template, variables, config)
        return template

    def _process_includes(self, content: str, base_dir: Path) -> str:
        include_re = re.compile(r"@include\(([^)]+)\)")

        def replace_include(match: re.Match) -> str:
            raw_path = match.group(1)
            if not raw_path:
                return ""
            include_path = (base_dir / raw_path).resolve()
            base_resolved = base_dir.resolve()
            if not str(include_path).startswith(str(base_resolved)):
                raise PentestError(
                    f"Path traversal in @include: {raw_path}",
                    "prompt",
                    error_code=ErrorCode.PROMPT_LOAD_FAILED,
                )
            if include_path.exists():
                return include_path.read_text(encoding="utf-8")
            return ""

        return include_re.sub(replace_include, content)

    def _interpolate(
        self,
        template: str,
        variables: dict[str, str],
        config: DistributedConfig | None,
    ) -> str:
        result = template
        result = result.replace("{{WEB_URL}}", variables.get("web_url", ""))
        result = result.replace("{{REPO_PATH}}", variables.get("repo_path", ""))
        result = result.replace("{{PLAYWRIGHT_SESSION}}", variables.get("playwright_session", "agent1"))

        if config:
            result = result.replace("{{DESCRIPTION}}", f"Description: {config.description}" if config.description else "")
            result = result.replace("{{AUTH_CONTEXT}}", "No authentication configured" if not config.authentication else f"Login type: {config.authentication.login_type}")
            avoid_str = "\n".join(f"- {r.description}" for r in config.avoid) if config.avoid else "None"
            focus_str = "\n".join(f"- {r.description}" for r in config.focus) if config.focus else "None"
            result = result.replace("{{RULES_AVOID}}", avoid_str)
            result = result.replace("{{RULES_FOCUS}}", focus_str)
            result = result.replace("{{VULN_CLASSES_TESTED}}", ", ".join(config.vuln_classes) if config.vuln_classes else "injection, xss, auth, authz, ssrf")
            result = result.replace("{{EXPLOITATION}}", "enabled" if config.exploit else "disabled")
            roe = config.rules_of_engagement.strip() if config.rules_of_engagement else ""
            result = result.replace("{{RULES_OF_ENGAGEMENT}}", roe)
        else:
            result = result.replace("{{DESCRIPTION}}", "")
            result = result.replace("{{AUTH_CONTEXT}}", "No authentication configured")
            result = result.replace("{{RULES_AVOID}}", "None")
            result = result.replace("{{RULES_FOCUS}}", "None")
            result = result.replace("{{VULN_CLASSES_TESTED}}", "injection, xss, auth, authz, ssrf")
            result = result.replace("{{EXPLOITATION}}", "enabled")
            result = result.replace("{{RULES_OF_ENGAGEMENT}}", "")

        result = result.replace("{{LOGIN_INSTRUCTIONS}}", "")

        for key, value in variables.items():
            token = "{{" + key.upper() + "}}"
            if token in result:
                result = result.replace(token, value)

        result = re.sub(r"\n{3,}", "\n\n", result)
        return result
