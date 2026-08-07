"""Unit tests for cockpit/security/access_control.py.

The load-bearing property here is **deny by default**. An RBAC bug that
denies too much is an annoyance; one that allows too much is an incident,
so the negative cases below matter more than the positive ones.
"""

from __future__ import annotations

import pytest

from cockpit.security.access_control import (
    DIRECT_PERMISSIONS,
    PRIVILEGED_PERMISSIONS,
    ROLE_INHERITS,
    ROLE_PERMISSIONS,
    UNTRUSTED_ROLES,
    AuthorizationError,
    Permission,
    Principal,
    Role,
    assign_role,
    check_permission,
    has_permission,
    permissions_for_role,
    require_permission,
    requires_permission,
    revoke_role,
)


class TestPermissionMatrix:
    """The resolved role -> permission tables."""

    def test_every_role_resolves(self) -> None:
        assert set(ROLE_PERMISSIONS) == set(Role)

    def test_admin_holds_every_permission(self) -> None:
        assert ROLE_PERMISSIONS[Role.ADMIN] == frozenset(Permission)

    def test_developer_inherits_viewer_permissions(self) -> None:
        assert ROLE_PERMISSIONS[Role.VIEWER] <= ROLE_PERMISSIONS[Role.DEVELOPER]

    def test_admin_inherits_developer_permissions(self) -> None:
        assert ROLE_PERMISSIONS[Role.DEVELOPER] <= ROLE_PERMISSIONS[Role.ADMIN]

    def test_viewer_cannot_write(self) -> None:
        assert Permission.PROJECT_WRITE not in ROLE_PERMISSIONS[Role.VIEWER]

    def test_developer_cannot_manage_users_or_secrets(self) -> None:
        """Privilege boundaries between trusted roles still have to hold."""
        developer = ROLE_PERMISSIONS[Role.DEVELOPER]
        assert Permission.USERS_MANAGE not in developer
        assert Permission.SECRETS_MANAGE not in developer
        assert Permission.CONFIG_MANAGE not in developer

    def test_permissions_for_role_accepts_a_string(self) -> None:
        assert permissions_for_role("viewer") == ROLE_PERMISSIONS[Role.VIEWER]


class TestUntrustedRoleContainment:
    """`external` must never reach a privileged capability."""

    def test_external_holds_no_privileged_permission(self) -> None:
        assert not (ROLE_PERMISSIONS[Role.EXTERNAL] & PRIVILEGED_PERMISSIONS)

    def test_external_inherits_nothing(self) -> None:
        assert ROLE_INHERITS[Role.EXTERNAL] == frozenset()

    def test_external_is_marked_untrusted(self) -> None:
        assert Role.EXTERNAL in UNTRUSTED_ROLES

    def test_external_permissions_are_exactly_its_direct_grant(self) -> None:
        """No hierarchy expansion happens for an untrusted role."""
        assert ROLE_PERMISSIONS[Role.EXTERNAL] == DIRECT_PERMISSIONS[Role.EXTERNAL]

    @pytest.mark.parametrize("permission", sorted(PRIVILEGED_PERMISSIONS))
    def test_external_is_denied_every_privileged_permission(self, permission: Permission) -> None:
        assert has_permission(Role.EXTERNAL, permission) is False

    def test_holding_external_alongside_admin_does_not_subtract(self) -> None:
        """Permissions are a union across roles, so a weak role cannot dilute."""
        principal = Principal(principal_id="u1", roles=frozenset({Role.EXTERNAL, Role.ADMIN}))
        assert principal.has_role(Role.EXTERNAL)
        assert has_permission(principal, Permission.USERS_MANAGE) is True


class TestDenyByDefault:
    """Anything unrecognized or absent must deny, never allow."""

    def test_unknown_role_string_denies(self) -> None:
        decision = check_permission("wizard", Permission.PROJECT_READ)
        assert decision.allowed is False
        assert "wizard" in decision.reason

    def test_unknown_permission_denies_even_for_admin(self) -> None:
        """A typo'd permission must not resolve to 'allow everything'."""
        decision = check_permission(Role.ADMIN, "porject:read")
        assert decision.allowed is False

    def test_none_subject_denies(self) -> None:
        assert has_permission(None, Permission.DOCS_READ_PUBLIC) is False

    def test_principal_with_no_roles_denies(self) -> None:
        principal = Principal(principal_id="u1", roles=frozenset())
        assert has_permission(principal, Permission.DOCS_READ_PUBLIC) is False

    def test_decision_carries_an_actionable_reason(self) -> None:
        decision = check_permission(Role.VIEWER, Permission.PROJECT_DELETE)
        assert decision.allowed is False
        assert decision.reason
        assert decision.permission == str(Permission.PROJECT_DELETE)


class TestPrincipal:
    """Principal construction and effective permissions."""

    def test_permissions_are_the_union_across_roles(self) -> None:
        principal = Principal(principal_id="u1", roles=frozenset({Role.VIEWER, Role.DEVELOPER}))
        expected = ROLE_PERMISSIONS[Role.VIEWER] | ROLE_PERMISSIONS[Role.DEVELOPER]
        assert principal.permissions == expected

    def test_has_role_accepts_enum_and_string(self) -> None:
        principal = Principal(principal_id="u1", roles=frozenset({Role.DEVELOPER}))
        assert principal.has_role(Role.DEVELOPER) is True
        assert principal.has_role("developer") is True
        assert principal.has_role(Role.ADMIN) is False

    def test_assign_role_returns_a_new_principal(self) -> None:
        """Immutability matters: another thread may be authorizing mid-check."""
        original = Principal(principal_id="u1", roles=frozenset({Role.VIEWER}))
        promoted = assign_role(original, Role.ADMIN)

        assert promoted is not original
        assert original.roles == frozenset({Role.VIEWER})
        assert promoted.has_role(Role.ADMIN)

    def test_revoke_role_returns_a_new_principal(self) -> None:
        original = Principal(principal_id="u1", roles=frozenset({Role.VIEWER, Role.ADMIN}))
        demoted = revoke_role(original, Role.ADMIN)

        assert original.has_role(Role.ADMIN)
        assert demoted.has_role(Role.ADMIN) is False
        assert demoted.has_role(Role.VIEWER)


class TestRequirePermission:
    """require_permission raises rather than returning a value."""

    def test_authorized_subject_returns_none(self) -> None:
        assert require_permission(Role.ADMIN, Permission.USERS_MANAGE) is None

    def test_unauthorized_subject_raises(self) -> None:
        with pytest.raises(AuthorizationError):
            require_permission(Role.VIEWER, Permission.PROJECT_DELETE)

    def test_unknown_permission_raises(self) -> None:
        with pytest.raises(AuthorizationError):
            require_permission(Role.ADMIN, "not:a:permission")

    def test_error_carries_the_permission_that_was_denied(self) -> None:
        with pytest.raises(AuthorizationError) as excinfo:
            require_permission(Role.EXTERNAL, Permission.SECRETS_MANAGE)
        assert excinfo.value.permission == str(Permission.SECRETS_MANAGE)


class TestRequiresPermissionDecorator:
    """The decorator guarding a function."""

    def test_authorized_principal_passes_through(self) -> None:
        @requires_permission(Permission.PROJECT_DELETE)
        def delete_project(principal: Principal, name: str) -> str:
            return f"deleted {name}"

        admin = Principal(principal_id="root", roles=frozenset({Role.ADMIN}))
        assert delete_project(admin, "demo") == "deleted demo"

    def test_unauthorized_principal_is_blocked(self) -> None:
        @requires_permission(Permission.PROJECT_DELETE)
        def delete_project(principal: Principal, name: str) -> str:  # pragma: no cover
            return f"deleted {name}"

        viewer = Principal(principal_id="ro", roles=frozenset({Role.VIEWER}))
        with pytest.raises(AuthorizationError):
            delete_project(viewer, "demo")

    def test_principal_may_be_passed_by_keyword(self) -> None:
        @requires_permission(Permission.PROJECT_READ)
        def read_project(*, principal: Principal) -> str:
            return "ok"

        viewer = Principal(principal_id="ro", roles=frozenset({Role.VIEWER}))
        assert read_project(principal=viewer) == "ok"

    def test_call_with_no_principal_is_denied_not_waved_through(self) -> None:
        """A guard that disables itself when its input is missing is not a guard."""

        @requires_permission(Permission.PROJECT_READ)
        def read_project(name: str) -> str:  # pragma: no cover
            return name

        with pytest.raises(AuthorizationError):
            read_project("demo")

    def test_decorator_preserves_function_metadata(self) -> None:
        @requires_permission(Permission.PROJECT_READ)
        def read_project(principal: Principal) -> str:  # pragma: no cover
            """Read a project."""
            return "ok"

        assert read_project.__name__ == "read_project"
        assert read_project.__doc__ == "Read a project."
