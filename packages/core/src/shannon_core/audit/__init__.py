"""Audit layer: AuditSession / WorkflowLogger / registry / display lifecycle.

Promoted from packages/whitebox/audit (package-agnostic; depends only on
shannon_core). Whitebox keeps a re-export shim at shannon_whitebox.audit.
"""
from .session import AuditSession

__all__ = ["AuditSession"]
