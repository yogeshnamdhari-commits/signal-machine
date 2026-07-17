"""
Role-Based Access Control (RBAC) — Institutional-grade permission system.

Roles:
- Admin: Full access to all operations
- Trader: View, Trade, Modify, Close
- Analyst: View, Audit
- Viewer: View only

All actions are logged to the audit trail.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from loguru import logger


class Role(str, Enum):
    ADMIN = "admin"
    TRADER = "trader"
    ANALYST = "analyst"
    VIEWER = "viewer"


class Permission(str, Enum):
    VIEW = "view"
    TRADE = "trade"
    MODIFY = "modify"
    CLOSE = "close"
    CONFIGURE = "configure"
    AUDIT = "audit"


# Role → Permission mapping
ROLE_PERMISSIONS: Dict[Role, Set[Permission]] = {
    Role.ADMIN: {
        Permission.VIEW, Permission.TRADE, Permission.MODIFY,
        Permission.CLOSE, Permission.CONFIGURE, Permission.AUDIT,
    },
    Role.TRADER: {
        Permission.VIEW, Permission.TRADE, Permission.MODIFY, Permission.CLOSE,
    },
    Role.ANALYST: {Permission.VIEW, Permission.AUDIT},
    Role.VIEWER: {Permission.VIEW},
}


@dataclass
class User:
    """Authenticated user."""
    user_id: str = ""
    username: str = ""
    password_hash: str = ""
    role: Role = Role.VIEWER
    created_at: float = 0.0
    last_login: float = 0.0
    active: bool = True
    api_key: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "role": self.role.value,
            "created_at": self.created_at,
            "last_login": self.last_login,
            "active": self.active,
        }


@dataclass
class AuditEntry:
    """Audit log entry."""
    entry_id: str = ""
    user_id: str = ""
    username: str = ""
    action: str = ""
    resource: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    ip_address: str = ""
    timestamp: float = 0.0
    success: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "user_id": self.user_id,
            "username": self.username,
            "action": self.action,
            "resource": self.resource,
            "details": self.details,
            "timestamp": self.timestamp,
            "success": self.success,
        }


@dataclass
class Session:
    """Active user session."""
    session_id: str = ""
    user_id: str = ""
    username: str = ""
    role: Role = Role.VIEWER
    created_at: float = 0.0
    expires_at: float = 0.0
    ip_address: str = ""
    active: bool = True


class RBACManager:
    """
    Role-based access control manager.

    Handles:
    - User management (create, update, deactivate)
    - Authentication (password, API key)
    - Session management (create, validate, revoke)
    - Permission checking
    - Audit logging
    """

    SESSION_DURATION_SEC = 3600 * 8  # 8 hours
    AUDIT_PATH = Path("data/reports/rbac_audit.json")

    def __init__(self) -> None:
        self._users: Dict[str, User] = {}
        self._sessions: Dict[str, Session] = {}
        self._audit_log: List[AuditEntry] = []
        self._max_audit_entries = 10_000

        # Initialize audit file
        self.AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)

        # Create default admin user
        self._create_default_admin()

    def _create_default_admin(self) -> None:
        """Create default admin user if none exists."""
        admin_id = "admin-001"
        if admin_id not in self._users:
            self._users[admin_id] = User(
                user_id=admin_id,
                username="admin",
                password_hash=self._hash_password("admin"),
                role=Role.ADMIN,
                created_at=time.time(),
                active=True,
                api_key=self._generate_api_key(),
            )
            logger.info("[RBAC] Default admin user created")

    @staticmethod
    def _hash_password(password: str) -> str:
        """Hash password with SHA-256."""
        return hashlib.sha256(password.encode()).hexdigest()

    @staticmethod
    def _generate_api_key() -> str:
        """Generate a random API key."""
        return f"dk_{uuid.uuid4().hex}"

    @staticmethod
    def _generate_session_id() -> str:
        """Generate a random session ID."""
        return f"sess_{uuid.uuid4().hex}"

    # ── User Management ──────────────────────────────────────────

    def create_user(
        self,
        username: str,
        password: str,
        role: Role = Role.VIEWER,
        created_by: str = "",
    ) -> User:
        """Create a new user."""
        # Check for duplicate username
        for u in self._users.values():
            if u.username == username:
                raise ValueError(f"Username '{username}' already exists")

        user_id = f"user-{uuid.uuid4().hex[:8]}"
        user = User(
            user_id=user_id,
            username=username,
            password_hash=self._hash_password(password),
            role=role,
            created_at=time.time(),
            active=True,
            api_key=self._generate_api_key(),
        )
        self._users[user_id] = user

        self._log_audit(
            user_id=created_by,
            action="CREATE_USER",
            resource=f"user/{user_id}",
            details={"username": username, "role": role.value},
        )

        logger.info("[RBAC] User created: {} (role={})", username, role.value)
        return user

    def update_user_role(
        self, user_id: str, new_role: Role, updated_by: str
    ) -> bool:
        """Update a user's role."""
        user = self._users.get(user_id)
        if not user:
            return False

        old_role = user.role
        user.role = new_role

        self._log_audit(
            user_id=updated_by,
            action="UPDATE_ROLE",
            resource=f"user/{user_id}",
            details={"old_role": old_role.value, "new_role": new_role.value},
        )

        logger.info("[RBAC] Role updated: {} → {}", old_role.value, new_role.value)
        return True

    def deactivate_user(self, user_id: str, deactivated_by: str) -> bool:
        """Deactivate a user."""
        user = self._users.get(user_id)
        if not user:
            return False

        user.active = False

        # Revoke all sessions
        for session in self._sessions.values():
            if session.user_id == user_id:
                session.active = False

        self._log_audit(
            user_id=deactivated_by,
            action="DEACTIVATE_USER",
            resource=f"user/{user_id}",
            details={"username": user.username},
        )

        logger.info("[RBAC] User deactivated: {}", user.username)
        return True

    # ── Authentication ───────────────────────────────────────────

    def authenticate(self, username: str, password: str, ip: str = "") -> Optional[Session]:
        """Authenticate with username/password, returns session."""
        for user in self._users.values():
            if user.username == username and user.active:
                if user.password_hash == self._hash_password(password):
                    session = self._create_session(user, ip)
                    user.last_login = time.time()

                    self._log_audit(
                        user_id=user.user_id,
                        action="LOGIN",
                        resource="session",
                        details={"method": "password", "ip": ip},
                    )

                    return session

        self._log_audit(
            user_id="",
            action="LOGIN_FAILED",
            resource="session",
            details={"username": username, "ip": ip},
            success=False,
        )

        return None

    def authenticate_api_key(self, api_key: str, ip: str = "") -> Optional[Session]:
        """Authenticate with API key, returns session."""
        for user in self._users.values():
            if user.api_key == api_key and user.active:
                session = self._create_session(user, ip)
                user.last_login = time.time()

                self._log_audit(
                    user_id=user.user_id,
                    action="LOGIN",
                    resource="session",
                    details={"method": "api_key", "ip": ip},
                )

                return session

        self._log_audit(
            user_id="",
            action="LOGIN_FAILED",
            resource="session",
            details={"method": "api_key", "ip": ip},
            success=False,
        )

        return None

    def validate_session(self, session_id: str) -> Optional[Session]:
        """Validate a session, returns session if valid."""
        session = self._sessions.get(session_id)
        if session and session.active and time.time() < session.expires_at:
            return session
        return None

    def revoke_session(self, session_id: str) -> bool:
        """Revoke a session."""
        session = self._sessions.get(session_id)
        if session:
            session.active = False

            self._log_audit(
                user_id=session.user_id,
                action="LOGOUT",
                resource="session",
                details={"session_id": session_id},
            )

            return True
        return False

    def _create_session(self, user: User, ip: str = "") -> Session:
        """Create a new session for a user."""
        now = time.time()
        session = Session(
            session_id=self._generate_session_id(),
            user_id=user.user_id,
            username=user.username,
            role=user.role,
            created_at=now,
            expires_at=now + self.SESSION_DURATION_SEC,
            ip_address=ip,
            active=True,
        )
        self._sessions[session.session_id] = session
        return session

    # ── Permission Checking ──────────────────────────────────────

    def check_permission(
        self, session_id: str, permission: Permission
    ) -> bool:
        """Check if a session has a specific permission."""
        session = self.validate_session(session_id)
        if not session:
            return False

        role_perms = ROLE_PERMISSIONS.get(session.role, set())
        return permission in role_perms

    def require_permission(
        self, session_id: str, permission: Permission
    ) -> Session:
        """Require a specific permission, raises if not authorized."""
        session = self.validate_session(session_id)
        if not session:
            raise PermissionError("Invalid or expired session")

        role_perms = ROLE_PERMISSIONS.get(session.role, set())
        if permission not in role_perms:
            self._log_audit(
                user_id=session.user_id,
                action="ACCESS_DENIED",
                resource=permission.value,
                details={"required": permission.value, "role": session.role.value},
                success=False,
            )
            raise PermissionError(
                f"Role '{session.role.value}' does not have '{permission.value}' permission"
            )

        return session

    def get_user_permissions(self, session_id: str) -> List[str]:
        """Get all permissions for a session."""
        session = self.validate_session(session_id)
        if not session:
            return []

        role_perms = ROLE_PERMISSIONS.get(session.role, set())
        return [p.value for p in role_perms]

    # ── Audit ────────────────────────────────────────────────────

    def _log_audit(
        self,
        user_id: str,
        action: str,
        resource: str,
        details: Optional[Dict[str, Any]] = None,
        success: bool = True,
    ) -> None:
        """Log an audit entry."""
        entry = AuditEntry(
            entry_id=f"audit-{uuid.uuid4().hex[:12]}",
            user_id=user_id,
            username=self._get_username(user_id),
            action=action,
            resource=resource,
            details=details or {},
            timestamp=time.time(),
            success=success,
        )
        self._audit_log.append(entry)

        # Trim audit log
        if len(self._audit_log) > self._max_audit_entries:
            self._audit_log = self._audit_log[-self._max_audit_entries // 2:]

    def _get_username(self, user_id: str) -> str:
        """Get username by user_id."""
        user = self._users.get(user_id)
        return user.username if user else "unknown"

    def get_audit_log(
        self,
        limit: int = 100,
        action_filter: Optional[str] = None,
        user_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get audit log entries with optional filtering."""
        entries = self._audit_log

        if action_filter:
            entries = [e for e in entries if e.action == action_filter]
        if user_filter:
            entries = [e for e in entries if e.user_id == user_filter]

        return [e.to_dict() for e in entries[-limit:]]

    # ── State ────────────────────────────────────────────────────

    def get_users(self) -> List[Dict[str, Any]]:
        """Get all users."""
        return [u.to_dict() for u in self._users.values()]

    def get_active_sessions(self) -> List[Dict[str, Any]]:
        """Get all active sessions."""
        now = time.time()
        return [
            {
                "session_id": s.session_id,
                "user_id": s.user_id,
                "username": s.username,
                "role": s.role.value,
                "created_at": s.created_at,
                "expires_at": s.expires_at,
                "active": s.active,
            }
            for s in self._sessions.values()
            if s.active and now < s.expires_at
        ]

    def get_stats(self) -> Dict[str, Any]:
        """Get RBAC statistics."""
        now = time.time()
        active_sessions = sum(
            1 for s in self._sessions.values()
            if s.active and now < s.expires_at
        )
        return {
            "total_users": len(self._users),
            "active_users": sum(1 for u in self._users.values() if u.active),
            "active_sessions": active_sessions,
            "total_audit_entries": len(self._audit_log),
            "roles": {
                role.value: sum(1 for u in self._users.values() if u.role == role)
                for role in Role
            },
        }

    def save_audit(self) -> None:
        """Persist audit log to disk."""
        try:
            data = [e.to_dict() for e in self._audit_log[-1000:]]
            tmp = self.AUDIT_PATH.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            tmp.rename(self.AUDIT_PATH)
        except Exception as e:
            logger.error("[RBAC] Failed to save audit: {}", e)
