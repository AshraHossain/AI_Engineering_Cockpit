"""Role-based access control (RBAC) (Tier 3 — not yet implemented).

Planned scope: role/permission definitions, principal-to-role assignment,
and permission checks for cockpit and project resources. Gated by
``feature_flags.is_enabled("security")`` at the framework level, but every
function here is a typed skeleton pending Tier 3 implementation. See
``docs/ARCHITECTURE.md`` for the planned design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Role(StrEnum):
    """Built-in roles recognized by the access control system.

    Attributes:
        VIEWER: Read-only access.
        DEVELOPER: Read/write access to non-production resources.
        ADMIN: Full access, including configuration and user management.
    """

    VIEWER = "viewer"
    DEVELOPER = "developer"
    ADMIN = "admin"


@dataclass(frozen=True)
class Principal:
    """An authenticated actor (user or service) subject to access control.

    Attributes:
        principal_id: Stable unique identifier for the actor.
        roles: Roles assigned to this principal.
    """

    principal_id: str
    roles: frozenset[Role] = field(default_factory=frozenset)


def has_permission(principal: Principal, resource: str, action: str) -> bool:
    """Check whether a principal may perform an action on a resource.

    Args:
        principal: The actor requesting access.
        resource: Identifier of the resource being accessed (e.g.
            "projects/02-rag-chatbot").
        action: The action being attempted (e.g. "read", "write", "delete").

    Returns:
        True if the principal is authorized.

    Raises:
        NotImplementedError: Tier 3 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 3 feature — see docs/ARCHITECTURE.md")


def assign_role(principal: Principal, role: Role) -> Principal:
    """Return a copy of a principal with an additional role assigned.

    Args:
        principal: The principal to update.
        role: The role to grant.

    Returns:
        A new :class:`Principal` with the role added.

    Raises:
        NotImplementedError: Tier 3 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 3 feature — see docs/ARCHITECTURE.md")


def revoke_role(principal: Principal, role: Role) -> Principal:
    """Return a copy of a principal with a role removed.

    Args:
        principal: The principal to update.
        role: The role to revoke.

    Returns:
        A new :class:`Principal` with the role removed.

    Raises:
        NotImplementedError: Tier 3 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 3 feature — see docs/ARCHITECTURE.md")


def require_permission(principal: Principal, resource: str, action: str) -> None:
    """Assert that a principal is authorized, raising if not.

    Args:
        principal: The actor requesting access.
        resource: Identifier of the resource being accessed.
        action: The action being attempted.

    Returns:
        None. Returns normally if authorized.

    Raises:
        PermissionError: If the principal is not authorized.
        NotImplementedError: Tier 3 feature — see docs/ARCHITECTURE.md.
    """
    raise NotImplementedError("Tier 3 feature — see docs/ARCHITECTURE.md")
