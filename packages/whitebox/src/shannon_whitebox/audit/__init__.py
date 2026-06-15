"""Compat shim — implementation moved to shannon_core.audit."""
from shannon_core.audit import AuditSession, AuditLogger, create_audit_logger  # noqa: F401

__all__ = ["AuditSession", "AuditLogger", "create_audit_logger"]
