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
    r"|\b(user_?id|owner_?id)\s*=\s*(req|ctx|currentUser)"
    r")"
)
