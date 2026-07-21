"""Compiled regex patterns shared across code_index modules.

Lives here (not in recon_gitnexus_track.py) so it survives the removal of the
recon GitNexus track renderer — authz_gitnexus_track still consumes it.
"""
import re

# Detects ownership/authorization predicates in handler source code (e.g.
# `where user_id = req.user.id`, `findByOwnerId`). Used by authz candidate
# detection (GitNexus-track internal), NOT fed to the LLM track.
OWNERSHIP_PREDICATE_RE = re.compile(
    r"(?is)("
    r"where\s*[:(][^}\n;]{0,240}\b(user_?id|owner_?id|owner|creator_?id|author_?id)\b"
    r"[^}\n;]{0,240}(req\.user|ctx\.state\.user|currentUser|user\.id|userId)"
    r"|\.where\s*\(\s*['\"]?(user_?id|owner_?id|owner|creator_?id|author_?id)['\"]?\s*[,=]"
    r"|\bfindBy(Owner|OwnerId|UserId|CreatorId|AuthorId)\b"
    r"|\b(owner|currentUser|req\.user|ctx\.state\.user)\s*\.\s*id\b"
    # alt 5: ownership assignment to a LOCAL var from an AUTH context (req.user /
    # ctx / currentUser). RHS narrowed from bare `(req|ctx|currentUser)` — the old
    # form false-positively matched IDOR-flavor source assignments like
    # `const userId = req.params.userId` (target resource id from user input, NOT
    # ownership), short-circuiting real IDOR candidates at gate :300 (spec 子项④).
    r"|\b(user_?id|owner_?id)\s*=\s*(req\.user|ctx|currentUser)"
    r")"
)
