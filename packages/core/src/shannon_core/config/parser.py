import os
import re
from pathlib import Path

import yaml
from shannon_core.models.config import (
    ALL_VULN_CLASSES,
    Authentication,
    Config,
    Credentials,
    DistributedConfig,
    EmailLogin,
    ReportConfig,
    Rule,
    Rules,
    SuccessCondition,
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

def _sanitize_rule(rule: Rule) -> Rule:
    return Rule(
        description=rule.description.strip(),
        type=rule.type.strip().lower(),
        value=rule.value.strip(),
    )


def _sanitize_authentication(auth: Authentication) -> Authentication:
    email_login = None
    if auth.credentials.email_login:
        email_login = EmailLogin(
            address=auth.credentials.email_login.address.strip(),
            password=auth.credentials.email_login.password.strip(),
            totp_secret=(
                auth.credentials.email_login.totp_secret.strip()
                if auth.credentials.email_login.totp_secret
                else None
            ),
        )
    return Authentication(
        login_type=auth.login_type.strip().lower(),
        login_url=auth.login_url.strip(),
        credentials=Credentials(
            username=auth.credentials.username.strip(),
            password=auth.credentials.password.strip() if auth.credentials.password else None,
            totp_secret=auth.credentials.totp_secret.strip() if auth.credentials.totp_secret else None,
            email_login=email_login,
        ),
        login_flow=[s.strip() for s in auth.login_flow] if auth.login_flow else None,
        success_condition=SuccessCondition(
            type=auth.success_condition.type.strip().lower(),
            value=auth.success_condition.value.strip(),
        ),
    )


def _sanitize_raw_dict(raw: dict) -> dict:
    """Sanitize the raw dict before Pydantic validation.

    Normalizes whitespace and casing on enum/string fields so that
    ``Literal`` validators receive clean values.
    """
    auth = raw.get("authentication")
    if isinstance(auth, dict):
        _sanitize_raw_auth(auth)
    rules = raw.get("rules")
    if isinstance(rules, dict):
        for key in ("avoid", "focus"):
            rule_list = rules.get(key)
            if isinstance(rule_list, list):
                for rule in rule_list:
                    if isinstance(rule, dict):
                        _sanitize_raw_rule(rule)
    return raw


def _sanitize_raw_rule(rule: dict) -> dict:
    for field in ("description", "type", "value"):
        v = rule.get(field)
        if isinstance(v, str):
            rule[field] = v.strip()
    # lowercase type for Literal validation
    t = rule.get("type")
    if isinstance(t, str):
        rule["type"] = t.lower()
    return rule


def _sanitize_raw_auth(auth: dict) -> dict:
    for field in ("login_type", "login_url"):
        v = auth.get(field)
        if isinstance(v, str):
            auth[field] = v.strip()
    # lowercase login_type for Literal validation
    lt = auth.get("login_type")
    if isinstance(lt, str):
        auth["login_type"] = lt.lower()
    creds = auth.get("credentials")
    if isinstance(creds, dict):
        for field in ("username", "password", "totp_secret"):
            v = creds.get(field)
            if isinstance(v, str):
                creds[field] = v.strip()
        el = creds.get("email_login")
        if isinstance(el, dict):
            for field in ("address", "password", "totp_secret"):
                v = el.get(field)
                if isinstance(v, str):
                    el[field] = v.strip()
    lf = auth.get("login_flow")
    if isinstance(lf, list):
        auth["login_flow"] = [s.strip() if isinstance(s, str) else s for s in lf]
    sc = auth.get("success_condition")
    if isinstance(sc, dict):
        for field in ("type", "value"):
            v = sc.get(field)
            if isinstance(v, str):
                sc[field] = v.strip()
        # lowercase type for Literal validation
        sct = sc.get("type")
        if isinstance(sct, str):
            sc["type"] = sct.lower()
    return auth


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

    # Environment variable override for browser engine
    if env_engine := os.environ.get("SHANNON_BROWSER_ENGINE"):
        raw["browser_engine"] = env_engine

    # Sanitize raw dict before Pydantic validation (normalizes case/whitespace)
    raw = _sanitize_raw_dict(raw)

    try:
        config = Config.model_validate(raw)
    except Exception as e:
        raise PentestError(
            f"Configuration validation failed: {e}",
            "config",
            error_code=ErrorCode.CONFIG_VALIDATION_FAILED,
            context={"original_error": str(e)},
        ) from e

    # Post-Pydantic sanitization (defense-in-depth)
    if config.authentication:
        sanitized_auth = _sanitize_authentication(config.authentication)
        config = config.model_copy(update={"authentication": sanitized_auth})
    if config.rules:
        sanitized_avoid = [_sanitize_rule(r) for r in config.rules.avoid] if config.rules.avoid else []
        sanitized_focus = [_sanitize_rule(r) for r in config.rules.focus] if config.rules.focus else []
        config = config.model_copy(update={
            "rules": config.rules.model_copy(update={
                "avoid": sanitized_avoid,
                "focus": sanitized_focus,
            })
        })

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
