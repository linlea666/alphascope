"""Roles and per-account permissions (docs/08-editorial-workflow.md).

A user acts on an account's articles when their role allows the action AND they hold that permission on the account.
admin and editor_in_chief act on every account; analyst only reads.
"""

ROLES = ("admin", "editor_in_chief", "editor", "reporter", "reviewer", "account_operator", "analyst")
ACCOUNT_PERMISSIONS = ("view", "draft", "review", "publish", "manage")
GLOBAL_ROLES = {"admin", "editor_in_chief"}
# which account actions each role may ever perform
ROLE_ACTIONS: dict[str, set[str]] = {
    "admin": set(ACCOUNT_PERMISSIONS),
    "editor_in_chief": set(ACCOUNT_PERMISSIONS),
    "editor": {"view", "draft", "review"},
    "reporter": {"view", "draft"},
    "reviewer": {"view", "review"},
    "account_operator": {"view", "publish"},
    "analyst": {"view"},
}
READ_ALL_ROLES = {"admin", "editor_in_chief", "analyst"}
# desk-wide actions not tied to an account
EVENT_DESK_ROLES = {"admin", "editor_in_chief", "editor"}  # evaluate, assign, decline events
SETTINGS_ROLES = {"admin"}
EMERGENCY_ROLES = {"admin", "editor_in_chief"}


def can(roles: set[str], account_perms: set[str], action: str) -> bool:
    """account_perms: the user's account_permission rows for that account."""
    if roles & GLOBAL_ROLES:
        return True
    if action == "view" and roles & READ_ALL_ROLES:
        return True
    allowed = set().union(*(ROLE_ACTIONS.get(r, set()) for r in roles)) if roles else set()
    held = set(ACCOUNT_PERMISSIONS) if "manage" in account_perms else account_perms
    return action in allowed and action in held
