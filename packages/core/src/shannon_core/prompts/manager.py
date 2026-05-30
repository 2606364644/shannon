import re
from pathlib import Path

from shannon_core.models.agents import PLAYWRIGHT_SESSION_MAPPING
from shannon_core.models.config import Authentication, DistributedConfig
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
        template = self._interpolate(template, variables, config, template_name)
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
        template_name: str = "",
    ) -> str:
        result = template
        result = result.replace("{{WEB_URL}}", variables.get("web_url", ""))
        result = result.replace("{{REPO_PATH}}", variables.get("repo_path", ""))
        playwright_session = variables.get("playwright_session") or PLAYWRIGHT_SESSION_MAPPING.get(template_name, "agent1")
        result = result.replace("{{PLAYWRIGHT_SESSION}}", playwright_session)

        if config:
            result = result.replace("{{DESCRIPTION}}", f"Description: {config.description}" if config.description else "")
            result = result.replace("{{AUTH_CONTEXT}}", self._build_auth_context(config))
            avoid_str = "\n".join(f"- {r.description}" for r in config.avoid) if config.avoid else "None"
            focus_str = "\n".join(f"- {r.description}" for r in config.focus) if config.focus else "None"
            result = result.replace("{{RULES_AVOID}}", avoid_str)
            result = result.replace("{{RULES_FOCUS}}", focus_str)
            result = result.replace("{{VULN_CLASSES_TESTED}}", ", ".join(config.vuln_classes) if config.vuln_classes else "injection, xss, auth, authz, ssrf")
            result = result.replace("{{EXPLOITATION}}", "enabled" if config.exploit else "disabled")
            roe = config.rules_of_engagement.strip() if config.rules_of_engagement else ""
            result = result.replace("{{RULES_OF_ENGAGEMENT}}", roe)

            report_filters_block = self._build_report_filters_block(config)
            result = result.replace("{{REPORT_FILTERS_BLOCK}}", report_filters_block)

            if config.report:
                report_rules = self._build_report_filter_rules(config.report)
                result = result.replace("{{REPORT_FILTER_RULES}}", report_rules)

            vuln_subsections = self._build_vuln_summary_subsections(config.vuln_classes)
            result = result.replace("{{VULN_SUMMARY_SUBSECTIONS}}", vuln_subsections)
        else:
            result = result.replace("{{DESCRIPTION}}", "")
            result = result.replace("{{AUTH_CONTEXT}}", "No authentication configured")
            result = result.replace("{{RULES_AVOID}}", "None")
            result = result.replace("{{RULES_FOCUS}}", "None")
            result = result.replace("{{VULN_CLASSES_TESTED}}", "injection, xss, auth, authz, ssrf")
            result = result.replace("{{EXPLOITATION}}", "enabled")
            result = result.replace("{{RULES_OF_ENGAGEMENT}}", "")
            result = result.replace("{{REPORT_FILTERS_BLOCK}}", "")
            result = result.replace("{{REPORT_FILTER_RULES}}", "")
            result = result.replace("{{VULN_SUMMARY_SUBSECTIONS}}", "")

        if config and config.authentication and config.authentication.login_flow:
            login_instructions = self.build_login_instructions(config.authentication)
            result = result.replace("{{LOGIN_INSTRUCTIONS}}", login_instructions)
        else:
            result = result.replace("{{LOGIN_INSTRUCTIONS}}", "")

        # Remove <shared_authenticated_session> block when no auth configured
        if not (config and config.authentication):
            result = re.sub(
                r"<shared_authenticated_session>[\s\S]*?</shared_authenticated_session>\s*",
                "",
                result,
            )

        for key, value in variables.items():
            token = "{{" + key.upper() + "}}"
            if token in result:
                result = result.replace(token, value)

        result = re.sub(r"\n{3,}", "\n\n", result)
        return result

    def _build_report_filters_block(self, config) -> str:
        """Render the REPORT_FILTERS_BLOCK conditional section."""
        report = config.report
        if not report or not any([
            report.min_severity, report.min_confidence, report.guidance,
        ]):
            return ""
        rules_text = self._build_report_filter_rules(report)
        return (
            "<report_filters>\n"
            "Apply the following filters to the report:\n"
            f"{rules_text}\n"
            "</report_filters>"
        )

    def _build_report_filter_rules(self, report) -> str:
        """Generate human-readable filter rules from ReportConfig."""
        lines = []
        if report.min_severity:
            lines.append(f"- Exclude vulnerabilities below **{report.min_severity.upper()}** severity")
        if report.min_confidence:
            lines.append(f"- Exclude vulnerabilities below **{report.min_confidence.upper()}** confidence")
        if report.guidance:
            lines.append(f"- Additional guidance: {report.guidance}")
        return "\n".join(lines)

    def _build_vuln_summary_subsections(self, vuln_classes: list[str]) -> str:
        """Generate per-class summary subsection templates."""
        lines = []
        for vc in vuln_classes:
            label = vc.replace("-", " ").title()
            lines.append(
                f"### {label}\n"
                f"Count: {{number of confirmed {vc} vulnerabilities}}\n"
                f"Severity range: {{range}}\n"
                f"Key findings: {{1-2 sentence summary}}"
            )
        return "\n\n".join(lines)

    def _build_auth_context(self, config: DistributedConfig) -> str:
        if not config.authentication:
            return "No authentication configured - unauthenticated testing only"
        auth = config.authentication
        lines = [
            f"- Login type: {auth.login_type.upper()}",
            f"- Username: {auth.credentials.username}",
            f"- Login URL: {auth.login_url}",
        ]
        if auth.credentials.totp_secret:
            lines.append("- MFA: TOTP enabled")
        return "\n".join(lines)

    def build_login_instructions(self, authentication: Authentication) -> str:
        """Assemble login instructions from the shared template based on login_type."""
        template_path = self.prompts_dir / "shared" / "login-instructions.txt"
        if not template_path.exists():
            raise PentestError(
                f"Login instructions template not found: {template_path}",
                "prompt",
                error_code=ErrorCode.PROMPT_LOAD_FAILED,
            )

        full_template = template_path.read_text(encoding="utf-8")

        def get_section(content: str, section_name: str) -> str:
            pattern = rf"<!-- BEGIN:{section_name} -->([\s\S]*?)<!-- END:{section_name} -->"
            match = re.search(pattern, content)
            return match.group(1).strip() if match else ""

        login_type = authentication.login_type.upper()
        common = get_section(full_template, "COMMON")
        auth_section = get_section(full_template, login_type)
        verification = get_section(full_template, "VERIFICATION")

        if not common and not auth_section and not verification:
            login_instructions = full_template
        else:
            login_instructions = "\n\n".join(filter(None, [common, auth_section, verification]))

        # Interpolate credential placeholders in login_flow steps
        user_instructions = "\n".join(authentication.login_flow or [])
        creds = authentication.credentials

        if creds:
            user_instructions = user_instructions.replace("$username", creds.username)
            if creds.password:
                user_instructions = user_instructions.replace("$password", creds.password)
            if creds.totp_secret:
                user_instructions = user_instructions.replace(
                    "$totp", f'generated TOTP code using secret "{creds.totp_secret}"'
                )
            if creds.email_login:
                user_instructions = user_instructions.replace(
                    "$email_address", creds.email_login.address
                )
                user_instructions = user_instructions.replace(
                    "$email_password", creds.email_login.password
                )
                if creds.email_login.totp_secret:
                    user_instructions = user_instructions.replace(
                        "$email_totp",
                        f'generated TOTP code using secret "{creds.email_login.totp_secret}"',
                    )

        login_instructions = login_instructions.replace("{{user_instructions}}", user_instructions)

        if creds and creds.totp_secret:
            login_instructions = login_instructions.replace("{{totp_secret}}", creds.totp_secret)

        return login_instructions
