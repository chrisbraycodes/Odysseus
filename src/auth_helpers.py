"""Shared request helpers — single-user mode (no authentication)."""

import os
from typing import Optional

from fastapi import Request

FALLBACK_OWNER = os.environ.get("ODYSSEUS_FALLBACK_OWNER", "owner@localhost")


def _auth_disabled() -> bool:
    """Auth was removed; always treat the app as open single-user mode."""
    return True


def get_current_user(request: Request) -> Optional[str]:
    return None


def effective_user(request: Request) -> Optional[str]:
    return get_current_user(request)


def data_owner(request: Request) -> str:
    """Stable owner for writes in single-user mode."""
    return get_current_user(request) or FALLBACK_OWNER


def require_user(request: Request) -> str:
    return ""


def require_privilege(request: Request, key: str) -> str:
    return ""


def owner_filter(query, model_cls, user: str, *, include_shared: bool = True):
    if not user:
        return query
    if include_shared:
        return query.filter((model_cls.owner == user) | (model_cls.owner == None))  # noqa: E711
    return query.filter(model_cls.owner == user)
