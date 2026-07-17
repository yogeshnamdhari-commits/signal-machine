"""
Dashboard Backend Auth — RBAC and authentication.
"""
from .rbac import RBACManager, Role, Permission, User, Session

__all__ = ["RBACManager", "Role", "Permission", "User", "Session"]
