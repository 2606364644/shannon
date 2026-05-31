import re
from pathlib import Path

import yaml
from shannon_core.models.config import (
    ALL_VULN_CLASSES,
    Authentication,
    Config,
    DistributedConfig,
    ReportConfig,
    Rule,
    VulnClass,
)
from shannon_core.models.errors import ErrorCode, PentestError

DANGEROUS_PATTERNS: list[re.Pattern] = [
    re.compile(r"\.\./"),
    re.compile(r"[<>]"),
    re.compile(r"javascript:", re.IGNORECASE),
    re.compile(r"data:", re.IGNORECASE),
    re.compile(r"file:", re.IGNORECASE),
]

def _check_dangerous_patterns(value: str, field: str) -> None:
    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(value):
            raise PentestError(
                f"{field} contains potentially dangerous pattern: {pattern.pattern}",
                "config",
                error_code=ErrorCode.CONFIG_VALIDATION_FAILED,
                context={"field": field, "pattern": pattern.pattern},
            )

def _validate_config_security(config: Config) -> None:
    if config.description:
        _check_dangerous_patterns(config.description, "description")
    if config.rules_of_engagement:
        _check_dangerous_patterns(config.rules_of_engagement, "rules_of_engagement")
    if config.authentication:
        _check_dangerous_patterns(config.authentication.login_url, "authentication.login_url")
        _check_dangerous_patterns(config.authentication.credentials.username, "credentials.username")

def _validate_login_flow(authentication: Authentication) -> None:
    """Validate login_flow steps for length and dangerous patterns."""
    if not authentication.login_flow:
        return
    for i, step in enumerate(authentication.login_flow):
        if len(step) > 500:
            raise PentestError(
                f"login_flow step {i + 1} exceeds 500 characters",
                "config",
                error_code=ErrorCode.CONFIG_VALIDATION_FAILED,
            )
        _check_dangerous_patterns(step, f"login_flow step {i + 1}")

def _validate_url_path_rules(rules: list[Rule], rule_type: str) -> None:
    for i, rule in enumerate(rules):
        if rule.type == "url_path" and not rule.value.startswith("/"):
            raise PentestError(
                f"rules.{rule_type}[{i}].value for type 'url_path' must start with '/'",
                "config",
                error_code=ErrorCode.CONFIG_VALIDATION_FAILED,
            )

def parse_config(config_path: str) -> Config:
    path = Path(config_path)
    if not path.exists():
        raise PentestError(
            f"Configuration file not found: {config_path}",
            "config",
            error_code=ErrorCode.CONFIG_NOT_FOUND,
            context={"config_path": config_path},
        )

    content = path.read_text(encoding="utf-8")
    if not content.strip():
        raise PentestError(
            "Configuration file is empty",
            "config",
            error_code=ErrorCode.CONFIG_VALIDATION_FAILED,
        )

    try:
        raw = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise PentestError(
            f"YAML parsing failed: {e}",
            "config",
            error_code=ErrorCode.CONFIG_PARSE_ERROR,
            context={"original_error": str(e)},
        ) from e

    if raw is None:
        raise PentestError(
            "Configuration file resulted in null after parsing",
            "config",
            error_code=ErrorCode.CONFIG_PARSE_ERROR,
        )

    try:
        config = Config.model_validate(raw)
    except Exception as e:
        raise PentestError(
            f"Configuration validation failed: {e}",
            "config",
            error_code=ErrorCode.CONFIG_VALIDATION_FAILED,
            context={"original_error": str(e)},
        ) from e

    _validate_config_security(config)
    if config.authentication:
        _validate_login_flow(config.authentication)
    if config.rules:
        _validate_url_path_rules(config.rules.avoid, "avoid")
        _validate_url_path_rules(config.rules.focus, "focus")

    return config

def distribute_config(config: Config | None) -> DistributedConfig:
    if config is None:
        return DistributedConfig(
            avoid=[], focus=[], description="",
            vuln_classes=list(ALL_VULN_CLASSES), exploit=True,
            report=ReportConfig(), rules_of_engagement="",
        )

    return DistributedConfig(
        avoid=config.rules.avoid if config.rules else [],
        focus=config.rules.focus if config.rules else [],
        description=config.description or "",
        vuln_classes=config.vuln_classes if config.vuln_classes else list(ALL_VULN_CLASSES),
        exploit=config.exploit,
        report=config.report or ReportConfig(),
        rules_of_engagement=config.rules_of_engagement or "",
        authentication=config.authentication,
    )
