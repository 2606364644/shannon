"""Compat shim — implementation moved to supernova_core.audit."""
from supernova_core.audit import AuditSession  # noqa: F401

__all__ = ["AuditSession"]
