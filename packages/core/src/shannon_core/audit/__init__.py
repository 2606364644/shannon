"""Audit layer: AuditSession / WorkflowLogger / registry / display lifecycle.

Promoted from packages/whitebox/audit (package-agnostic; depends only on
shannon_core). Whitebox keeps a re-export shim at shannon_whitebox.audit.
"""
from .session import AuditSession
from .audit_logger import AuditLogger, create_audit_logger

__all__ = ["AuditSession", "AuditLogger", "create_audit_logger"]
