"""Role-based access control (RBAC) for cockpit and project resources.

The whole design goal is that the answer to "who can do what" is *readable*.
The role-to-permission mapping is a table (:data:`DIRECT_PERMISSIONS`) and the
role hierarchy is a table (:data:`ROLE_INHERITS`); no permission is granted by
a conditional buried in a function. A reviewer can diff the tables and know
exactly what changed.

**Deny by default.** Every lookup path resolves an unknown role, an unknown
permission, or a missing principal to *deny*. Fail-open is the classic RBAC
bug: a typo'd role name that falls through to a permissive branch grants
everything, and nothing in the test suite notices because the happy path
still passes. So unknown inputs here never reach a grant -- they return an
empty permission set and log.

**Untrusted roles never inherit.** :data:`Role.EXTERNAL` is deliberately
outside the admin > developer > viewer chain and is listed in
:data:`UNTRUSTED_ROLES`, which the resolver refuses to expand. On top of
that, :func:`_validate_matrix` runs at import and raises if any untrusted
role ends up holding a permission from :data:`PRIVILEGED_PERMISSIONS` -- so a
future edit that accidentally wires ``EXTERNAL`` into the hierarchy fails at
import time rather than in production.

Note on feature flags: authorization decisions here are **not** gated on
``feature_flags.is_enabled("security")``. A permission check that returns
"allowed" because a flag is off is a fail-open kill switch on the access
control system, which is precisely the failure this module exists to
prevent. The flag governs optional scanning and protective processing
elsewhere in the package, not who is allowed to do what.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ParamSpec, TypeVar

from cockpit.utils.logging_config import get_logger

_logger = get_logger(__name__)

_P = ParamSpec("_P")
_R = TypeVar("_R")


class AuthorizationError(PermissionError):
    """Raised when a principal is denied an action.

    Subclasses the builtin :class:`PermissionError` so existing handlers
    that catch it keep working, while callers who want to distinguish an
    RBAC denial from a filesystem permission error can catch this instead.

    Attributes:
        principal_id: Identifier of the denied actor, or ``None`` when no
            principal was supplied at all.
        permission: The permission that was required, as a string.
    """

    def __init__(self, message: str, *, principal_id: str | None, permission: str) -> None:
        """Record the denial details alongside the message.

        Args:
            message: Human-readable explanation of the denial.
            principal_id: Identifier of the denied actor, if known.
            permission: The permission that was required.
        """
        super().__init__(message)
        self.principal_id = principal_id
        self.permission = permission


class Role(StrEnum):
    """Built-in roles recognized by the access control system.

    Attributes:
        EXTERNAL: Untrusted outside party (e.g. a demo or shared-link user).
            Sits outside the role hierarchy and inherits nothing.
        VIEWER: Read-only access.
        DEVELOPER: Read/write access to non-production resources.
        ADMIN: Full access, including configuration and user management.
    """

    EXTERNAL = "external"
    VIEWER = "viewer"
    DEVELOPER = "developer"
    ADMIN = "admin"


class Permission(StrEnum):
    """Discrete capabilities that can be granted to a role.

    Named ``resource:action`` so a permission reads as a sentence at the
    call site and new resources extend the set without renaming anything.

    Attributes:
        DOCS_READ_PUBLIC: Read published, non-sensitive documentation.
        PROJECT_READ: Read project code, configuration, and results.
        PROJECT_WRITE: Create or modify project resources.
        PROJECT_DELETE: Delete a project and its artifacts.
        METRICS_READ: Read cost and performance telemetry.
        EVALUATION_RUN: Execute evaluation suites against a model.
        RED_TEAM_RUN: Execute adversarial/red-team suites.
        MODEL_INVOKE: Send prompts to a model provider (a billable, and
            data-exfiltrating, action).
        AUDIT_LOG_READ: Read the security audit log.
        SECRETS_MANAGE: Create, rotate, or reveal stored secrets.
        KEYS_ROTATE: Rotate data-encryption keys.
        CONFIG_MANAGE: Change cockpit configuration and feature flags.
        USERS_MANAGE: Assign or revoke roles for other principals.
    """

    DOCS_READ_PUBLIC = "docs:read_public"
    PROJECT_READ = "project:read"
    PROJECT_WRITE = "project:write"
    PROJECT_DELETE = "project:delete"
    METRICS_READ = "metrics:read"
    EVALUATION_RUN = "evaluation:run"
    RED_TEAM_RUN = "red_team:run"
    MODEL_INVOKE = "model:invoke"
    AUDIT_LOG_READ = "audit_log:read"
    SECRETS_MANAGE = "secrets:manage"
    KEYS_ROTATE = "keys:rotate"
    CONFIG_MANAGE = "config:manage"
    USERS_MANAGE = "users:manage"


# Permissions granted to a role *directly*, before hierarchy expansion.
# This is the table to edit when granting something; keep it minimal per role
# and let the hierarchy below do the accumulating.
DIRECT_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.EXTERNAL: frozenset({Permission.DOCS_READ_PUBLIC}),
    Role.VIEWER: frozenset(
        {
            Permission.DOCS_READ_PUBLIC,
            Permission.PROJECT_READ,
            Permission.METRICS_READ,
        }
    ),
    Role.DEVELOPER: frozenset(
        {
            Permission.PROJECT_WRITE,
            Permission.EVALUATION_RUN,
            Permission.RED_TEAM_RUN,
            Permission.MODEL_INVOKE,
        }
    ),
    Role.ADMIN: frozenset(
        {
            Permission.PROJECT_DELETE,
            Permission.AUDIT_LOG_READ,
            Permission.SECRETS_MANAGE,
            Permission.KEYS_ROTATE,
            Permission.CONFIG_MANAGE,
            Permission.USERS_MANAGE,
        }
    ),
}

# Role hierarchy: admin implies developer implies viewer. Written as explicit
# data rather than an ordering, so a non-linear hierarchy (two peer roles both
# implying viewer) stays expressible without rewriting the resolver.
#
# EXTERNAL maps to the empty set on purpose. It is not "not yet configured";
# it is the statement that an untrusted party inherits nothing.
ROLE_INHERITS: dict[Role, frozenset[Role]] = {
    Role.EXTERNAL: frozenset(),
    Role.VIEWER: frozenset(),
    Role.DEVELOPER: frozenset({Role.VIEWER}),
    Role.ADMIN: frozenset({Role.DEVELOPER}),
}

# Roles held by parties outside the trust boundary. The resolver will not
# expand inheritance for these even if a future edit adds an entry to
# ROLE_INHERITS for one -- belt and braces, because this is the failure that
# would be both silent and severe.
UNTRUSTED_ROLES: frozenset[Role] = frozenset({Role.EXTERNAL})

# Permissions an untrusted role must never hold under any composition of the
# tables above. Enforced at import by _validate_matrix().
PRIVILEGED_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.PROJECT_WRITE,
        Permission.PROJECT_DELETE,
        Permission.EVALUATION_RUN,
        Permission.RED_TEAM_RUN,
        Permission.MODEL_INVOKE,
        Permission.AUDIT_LOG_READ,
        Permission.SECRETS_MANAGE,
        Permission.KEYS_ROTATE,
        Permission.CONFIG_MANAGE,
        Permission.USERS_MANAGE,
    }
)


def _resolve_role(role: Role, seen: set[Role]) -> frozenset[Permission]:
    """Expand a role's direct permissions with everything it inherits.

    Args:
        role: The role to expand.
        seen: Roles already visited on this path, which both terminates a
            cycle in the hierarchy table and prevents redundant work.

    Returns:
        The union of the role's direct permissions and those of every role
        it inherits, transitively.
    """
    if role in seen:
        return frozenset()
    seen.add(role)

    permissions = set(DIRECT_PERMISSIONS.get(role, frozenset()))
    if role in UNTRUSTED_ROLES:
        # Untrusted roles get their direct grants and nothing else, whatever
        # the hierarchy table happens to say.
        return frozenset(permissions)

    for parent in ROLE_INHERITS.get(role, frozenset()):
        permissions |= _resolve_role(parent, seen)
    return frozenset(permissions)


def _build_matrix() -> dict[Role, frozenset[Permission]]:
    """Compute the effective permission set for every known role.

    Returns:
        A mapping of role to its fully expanded permission set.
    """
    return {role: _resolve_role(role, set()) for role in Role}


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = _build_matrix()


def _validate_matrix() -> None:
    """Assert the safety invariants of the permission tables.

    Runs at import so a bad edit fails immediately and loudly instead of
    becoming a live privilege escalation.

    Raises:
        RuntimeError: If an untrusted role holds a privileged permission, or
            if a role is missing from the resolved matrix.
    """
    for role in Role:
        if role not in ROLE_PERMISSIONS:
            raise RuntimeError(f"Role {role!r} has no entry in the resolved permission matrix.")

    for role in UNTRUSTED_ROLES:
        escalated = ROLE_PERMISSIONS[role] & PRIVILEGED_PERMISSIONS
        if escalated:
            names = ", ".join(sorted(str(p) for p in escalated))
            raise RuntimeError(
                f"Untrusted role {role!r} would hold privileged permission(s): {names}. "
                "Check DIRECT_PERMISSIONS and ROLE_INHERITS."
            )


_validate_matrix()


@dataclass(frozen=True)
class Principal:
    """An authenticated actor (user or service) subject to access control.

    A principal may hold several roles; its effective permissions are the
    **union** across them. Union, not intersection: roles are additive
    grants, so holding both ``viewer`` and ``developer`` means holding both
    sets. A principal with no roles has no permissions.

    Attributes:
        principal_id: Stable unique identifier for the actor.
        roles: Roles assigned to this principal.
    """

    principal_id: str
    roles: frozenset[Role] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        """Normalize ``roles`` to a frozenset regardless of what was passed.

        Accepting a list or set at the call site is convenient and avoids a
        whole class of "I passed a list and the set operations silently did
        the wrong thing" bug. Unknown role values are *kept* rather than
        dropped, so they surface as an explicit denial at check time instead
        of a principal that looks role-less.

        Raises:
            TypeError: If ``roles`` is not iterable.
        """
        # mypy calls the inner branch unreachable because the annotation says
        # frozenset. Annotations are not enforced at runtime, and a Principal
        # is routinely built from deserialized input, so the guard stays.
        # Rejecting a bare string matters most: frozenset("admin") silently
        # becomes {"a","d","m","i","n"}, i.e. five unknown roles rather than
        # one real one, and the principal would authorize as nothing at all.
        if not isinstance(self.roles, frozenset):
            if isinstance(self.roles, str) or not isinstance(  # type: ignore[unreachable]
                self.roles, Iterable
            ):
                raise TypeError("roles must be an iterable of Role values.")
            object.__setattr__(self, "roles", frozenset(self.roles))

    @property
    def permissions(self) -> frozenset[Permission]:
        """Effective permissions, unioned across every assigned role.

        Returns:
            The union of the permission sets of all known roles held. Roles
            that are not recognized contribute nothing.
        """
        granted: set[Permission] = set()
        for role in self.roles:
            granted |= permissions_for_role(role)
        return frozenset(granted)

    def has_role(self, role: Role | str) -> bool:
        """Check whether this principal directly holds a role.

        Does not consider the hierarchy -- an admin does not "have" the
        viewer role, it merely has the viewer permissions. Ask about
        permissions, not roles, when making an access decision.

        Args:
            role: The role to look for.

        Returns:
            True if the role is directly assigned.
        """
        return role in self.roles


@dataclass(frozen=True)
class AccessDecision:
    """The outcome of a permission check, with the reason it went that way.

    Exists so a denial can be audited: "denied" on its own is not
    actionable, "denied: role 'wizard' is not a recognized role" is.

    Attributes:
        allowed: True if access is granted.
        principal_id: Identifier of the actor, or ``None`` if none was given.
        permission: The permission string that was checked.
        reason: Human-readable explanation of the decision.
    """

    allowed: bool
    principal_id: str | None
    permission: str
    reason: str


def permissions_for_role(role: Role | str) -> frozenset[Permission]:
    """Return the effective permissions for a role.

    Args:
        role: A :class:`Role`, or its string value.

    Returns:
        The role's fully expanded permission set, or an **empty** set if the
        role is not recognized. Unknown roles deny; they never fall through
        to a default grant.
    """
    try:
        known = Role(role)
    except ValueError:
        _logger.warning("Unknown role %r requested; granting no permissions.", role)
        return frozenset()
    return ROLE_PERMISSIONS.get(known, frozenset())


def _coerce_permission(permission: Permission | str) -> Permission | None:
    """Resolve a permission value, returning ``None`` when unrecognized.

    Args:
        permission: A :class:`Permission` or its string value.

    Returns:
        The matching :class:`Permission`, or ``None`` if there is no such
        permission -- which the callers treat as a denial.
    """
    try:
        return Permission(permission)
    except ValueError:
        return None


def check_permission(
    subject: Principal | Role | str | None, permission: Permission | str
) -> AccessDecision:
    """Evaluate a permission check and explain the result.

    Args:
        subject: The actor requesting access. Accepts a :class:`Principal`,
            a bare :class:`Role` (or role string) for role-level checks, or
            ``None``.
        permission: The permission being requested.

    Returns:
        An :class:`AccessDecision` recording the outcome and its reason.
    """
    permission_label = str(permission)
    resolved = _coerce_permission(permission)
    if resolved is None:
        # An unknown permission is almost always a typo at the call site. It
        # must deny: treating "porject:read" as "grant everything" is how
        # fail-open happens.
        _logger.warning("Unknown permission %r requested; denying.", permission)
        return AccessDecision(
            allowed=False,
            principal_id=getattr(subject, "principal_id", None),
            permission=permission_label,
            reason=f"Unknown permission {permission_label!r}.",
        )

    if subject is None:
        return AccessDecision(
            allowed=False,
            principal_id=None,
            permission=permission_label,
            reason="No principal supplied.",
        )

    if isinstance(subject, Principal):
        principal_id: str | None = subject.principal_id
        granted = subject.permissions
        holder = f"principal {subject.principal_id!r}"
    else:
        principal_id = None
        granted = permissions_for_role(subject)
        holder = f"role {str(subject)!r}"

    if resolved in granted:
        return AccessDecision(
            allowed=True,
            principal_id=principal_id,
            permission=permission_label,
            reason=f"{holder} holds {permission_label!r}.",
        )
    return AccessDecision(
        allowed=False,
        principal_id=principal_id,
        permission=permission_label,
        reason=f"{holder} does not hold {permission_label!r}.",
    )


def has_permission(subject: Principal | Role | str | None, permission: Permission | str) -> bool:
    """Check whether a subject may exercise a permission.

    Args:
        subject: A :class:`Principal`, a :class:`Role`, a role string, or
            ``None``.
        permission: The permission being requested.

    Returns:
        True only if the subject demonstrably holds the permission.
        Anything unrecognized or missing returns False.
    """
    return check_permission(subject, permission).allowed


def require_permission(
    subject: Principal | Role | str | None, permission: Permission | str
) -> None:
    """Assert that a subject is authorized, raising if not.

    Args:
        subject: The actor requesting access.
        permission: The permission being requested.

    Returns:
        None. Returns normally if authorized.

    Raises:
        AuthorizationError: If the subject is not authorized. Also raised
            for an unknown permission or a missing principal.
    """
    decision = check_permission(subject, permission)
    if decision.allowed:
        return

    _logger.warning("Access denied: %s", decision.reason)
    raise AuthorizationError(
        f"Access denied: {decision.reason}",
        principal_id=decision.principal_id,
        permission=decision.permission,
    )


def _find_principal(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Principal | None:
    """Locate the :class:`Principal` argument of a guarded call.

    Args:
        args: Positional arguments the wrapped function was called with.
        kwargs: Keyword arguments the wrapped function was called with.

    Returns:
        The first :class:`Principal` found in ``kwargs["principal"]`` or in
        the positional arguments, or ``None`` if there is none -- which the
        decorator treats as a denial.
    """
    candidate = kwargs.get("principal")
    if isinstance(candidate, Principal):
        return candidate
    for value in args:
        if isinstance(value, Principal):
            return value
    return None


def requires_permission(
    permission: Permission | str,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Guard a function so it only runs for an authorized principal.

    The wrapped function must receive a :class:`Principal` -- either as the
    keyword argument ``principal`` or as one of its positional arguments. A
    call with no principal at all is *denied*, not waved through; a guard
    that silently disables itself when its input is missing is not a guard.

    Args:
        permission: The permission the caller must hold.

    Returns:
        A decorator that wraps the target function with the check.

    Example:
        >>> @requires_permission(Permission.PROJECT_DELETE)
        ... def delete_project(principal: Principal, name: str) -> None: ...
    """

    def decorator(func: Callable[_P, _R]) -> Callable[_P, _R]:
        @functools.wraps(func)
        def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            principal = _find_principal(args, kwargs)
            if principal is None:
                _logger.warning(
                    "Denied call to %s: no Principal argument was supplied.", func.__qualname__
                )
                raise AuthorizationError(
                    f"Access denied: {func.__qualname__} requires a Principal argument.",
                    principal_id=None,
                    permission=str(permission),
                )
            require_permission(principal, permission)
            return func(*args, **kwargs)

        return wrapper

    return decorator


def assign_role(principal: Principal, role: Role) -> Principal:
    """Return a copy of a principal with an additional role assigned.

    Principals are immutable, so a grant produces a new object rather than
    mutating one another thread may be authorizing against mid-check.

    Args:
        principal: The principal to update.
        role: The role to grant.

    Returns:
        A new :class:`Principal` with the role added. Returns an equivalent
        principal if the role was already held.

    Raises:
        ValueError: If ``role`` is not a recognized role. Granting an
            unknown role is a bug worth surfacing, unlike *checking* one.
    """
    known = Role(role)
    return Principal(principal_id=principal.principal_id, roles=principal.roles | {known})


def revoke_role(principal: Principal, role: Role) -> Principal:
    """Return a copy of a principal with a role removed.

    Args:
        principal: The principal to update.
        role: The role to revoke. Revoking a role the principal does not
            hold is a no-op, and revoking an unrecognized role is allowed so
            a bad assignment can always be cleaned up.

    Returns:
        A new :class:`Principal` without that role.
    """
    return Principal(principal_id=principal.principal_id, roles=principal.roles - {role})
