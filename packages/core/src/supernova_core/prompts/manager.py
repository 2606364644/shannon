import logging
import re
from pathlib import Path

from supernova_core.models.agents import BROWSER_SESSION_MAPPING
from supernova_core.models.config import Authentication, DistributedConfig, Rule
from supernova_core.models.errors import ErrorCode, PentestError
from supernova_core.services.browser_engine import BrowserEngineFactory
import supernova_core.services.engines  # noqa: F401 – registers PlaywrightEngine & AgentBrowserEngine

logger = logging.getLogger(__name__)


def strip_conditional_blocks(text: str, has_web_url: bool) -> str:
    """Select <if-live> or <if-static> content based on whether WEB_URL is present."""
    if has_web_url:
        text = re.sub(r'<if-static>.*?</if-static>', '', text, flags=re.DOTALL)
        text = text.replace('<if-live>', '').replace('</if-live>', '')
    else:
        text = re.sub(r'<if-live>.*?</if-live>', '', text, flags=re.DOTALL)
        text = text.replace('<if-static>', '').replace('</if-static>', '')
    return text


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
        has_web_url = bool(variables.get("web_url"))
        template = strip_conditional_blocks(template, has_web_url)
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
        result = result.replace("{{DELIVERABLES_PATH}}", variables.get("deliverables_path", ""))
        result = result.replace("{{SCRATCHPAD_PATH}}", variables.get("scratchpad_path", ""))

        # Resolve browser session ID (backward compat: fall back to playwright_session)
        session_id = (
            variables.get("browser_session_id")
            or variables.get("playwright_session")
            or BROWSER_SESSION_MAPPING.get(template_name, "agent1")
        )

        # Resolve the browser engine via factory
        engine = BrowserEngineFactory.get_engine(variables.get("browser_engine", "agent-browser"))

        # Legacy placeholder – kept so that existing templates still work
        result = result.replace("{{PLAYWRIGHT_SESSION}}", session_id)

        # Engine-aware placeholders for updated templates
        result = result.replace("{{BROWSER_SESSION_FLAG}}", engine.session_flag(session_id))
        result = result.replace("{{BROWSER_COMMANDS}}", engine.commands_reference())

        # Auth state save/load commands (engine-specific). Only emitted when an
        # auth-state file is in scope (auth-validation + exploit reuse path).
        auth_state_file = variables.get("AUTH_STATE_FILE", "")
        if auth_state_file:
            result = result.replace(
                "{{AUTH_SAVE_COMMAND}}",
                engine.auth_save_command(session_id, auth_state_file),
            )
            result = result.replace(
                "{{AUTH_LOAD_COMMAND}}",
                engine.auth_load_command(session_id, auth_state_file),
            )
        else:
            result = result.replace("{{AUTH_SAVE_COMMAND}}", "")
            result = result.replace("{{AUTH_LOAD_COMMAND}}", "")

        if config:
            result = result.replace("{{DESCRIPTION}}", f"Description: {config.description}" if config.description else "")
            result = result.replace("{{AUTH_CONTEXT}}", self._build_auth_context(config))
            avoid_str = "\n".join(f"- {r.description}" for r in config.avoid) if config.avoid else "None"
            focus_str = "\n".join(f"- {r.description}" for r in config.focus) if config.focus else "None"
            result = result.replace("{{RULES_AVOID}}", avoid_str)
            result = result.replace("{{RULES_FOCUS}}", focus_str)
            # code_path 类规则 → [FILE]/[GLOB] 标签行（CODE_RULES_* partial）
            result = result.replace("{{CODE_RULES_AVOID}}", self._render_code_path_rules(config.avoid))
            result = result.replace("{{CODE_RULES_FOCUS}}", self._render_code_path_rules(config.focus))
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
            result = result.replace("{{CODE_RULES_AVOID}}", "None")
            result = result.replace("{{CODE_RULES_FOCUS}}", "None")
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

        # 检测残留的未解析占位符(只匹配真变量格式 {{UPPER_CASE}},
        # 排除自然语言填空提示如 {{number of ...}})
        remaining = re.findall(r"\{\{[A-Z][A-Z0-9_]*\}\}", result)
        if remaining:
            logger.warning(
                "Unresolved prompt placeholders in %s: %s",
                template_name,
                sorted(set(remaining)),
            )

        return result

    def _render_code_path_rules(self, rules: list[Rule]) -> str:
        """把 code_path 类规则渲染成 [FILE]/[GLOB] 标签行,填 {{CODE_RULES_AVOID}} /
        {{CODE_RULES_FOCUS}}（见 prompts/shared/_code-path-rules.txt）。

        复用 settings_writer.sync_code_path_deny_rules 的筛选口径
        (type == code_path 且 value 非空);空列表 → 'None'(与 RULES_AVOID 惯例一致)。
        """
        code_rules = [
            r for r in rules
            if r.type == "code_path" and r.value and r.value.strip()
        ]
        if not code_rules:
            return "None"
        lines = []
        for r in code_rules:
            value = r.value.strip()
            tag = "[GLOB]" if any(c in value for c in "*?[]") else "[FILE]"
            desc = r.description.strip() if r.description else ""
            if desc and desc != value:
                lines.append(f"- {tag} {value}  # {desc}")
            else:
                lines.append(f"- {tag} {value}")
        return "\n".join(lines)

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
        """Generate per-class summary subsection templates.

        口径（2026-07-14，修 hr_20260713-104726 口径脱节）：Count 只数报告正文里的
        ### 单点漏洞卡片（ID 形如 PREFIX-VULN-NN / PREFIX-GN-NN）。攻击链
        （## 攻击链 章节 / llm-chain-N）里发现的缺陷【不计入】此处——它们在攻击链
        章节单独体现，避免「单点漏洞总数」与「类型汇总」口径脱节。
        """
        lines = []
        for vc in vuln_classes:
            label = vc.replace("-", " ").title()
            lines.append(
                f"### {label}\n"
                f"Count: {{只数本报告正文 ### 单点漏洞卡片（ID 形如 PREFIX-VULN-NN 或 PREFIX-GN-NN，属于 {label} 类）的数量。"
                f"攻击链（## 攻击链 / llm-chain-N）里发现的缺陷【不计入】此处——它们单独成章。"
                f"若该类无单点卡片，写 0}}\n"
                f"Severity range: {{仅基于上述单点卡片的 range；无单点卡片则 N/A}}\n"
                f"Key findings: {{1-2 句，仅概述单点卡片；勿混入攻击链内容}}"
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
