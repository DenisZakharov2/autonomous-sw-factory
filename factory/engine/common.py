"""Constants and small lookups shared by the engine package."""
from __future__ import annotations

from typing import Any

from ..config import PACKAGE_ROOT, resolve_model
from ..verify import parse_role

VERIFIER = "02-independent-verifier"
VERIFICATION_IDS = {"verification", "task_verification"}


def verification_policy(state: dict[str, Any]) -> str | None:
    """`role` for runs whose gates execute the verifier; `None` for history."""
    return str(state.get("verification_policy") or "") or None


def _model_for(role: str, fallback: str = "tier-2-balanced") -> dict[str, str]:
    """Resolve the model tier declared by the role card, not a hard-coded one."""
    path = PACKAGE_ROOT / "roles" / f"{role}.md"
    if path.exists():
        try:
            frontmatter, _ = parse_role(path)
            tier = str(frontmatter.get("default_model_tier", fallback))
            effort = str(frontmatter.get("reasoning_effort", "medium"))
            model = dict(resolve_model(tier))
            model["effort"] = effort
            return model
        except Exception:
            pass
    return resolve_model(fallback)

