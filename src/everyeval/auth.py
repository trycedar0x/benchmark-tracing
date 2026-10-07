"""Users, workspaces, API keys, sessions and encrypted secrets.

Authentication is off unless EVERYEVAL_AUTH=1. Without it the server runs in
single-workspace local mode and should stay bound to localhost.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from everyeval.config import env
from everyeval.db import ApiKey, Membership, Secret, User, UserSession, Workspace, now, session_scope

ROLES = ("owner", "member", "viewer")
SESSION_DAYS = 14
SECRET_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,99}$")


class AuthError(PermissionError):
    pass


def auth_enabled() -> bool:
    return env("AUTH") == "1"


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise AuthError("Passwords must be at least 10 characters.")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, digest_b64 = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=2**14, r=8, p=1)
    return hmac.compare_digest(digest, base64.b64decode(digest_b64))


# ---------------------------------------------------------------------------- admin operations


def ensure_workspace(session: Session, name: str) -> Workspace:
    ws = session.scalars(select(Workspace).where(Workspace.name == name)).first()
    if ws is None:
        ws = Workspace(name=name)
        session.add(ws)
        session.flush()
    return ws


def create_user(email: str, password: str, workspace: str, role: str = "owner") -> User:
    if role not in ROLES:
        raise AuthError(f"Role must be one of {', '.join(ROLES)}.")
    email = email.strip().lower()
    with session_scope() as session:
        user = session.scalars(select(User).where(User.email == email)).first()
        if user is None:
            user = User(email=email, password_hash=hash_password(password))
            session.add(user)
            session.flush()
        ws = ensure_workspace(session, workspace)
        member = session.scalars(
            select(Membership).where(Membership.user_id == user.id, Membership.workspace_id == ws.id)
        ).first()
        if member is None:
            session.add(Membership(user_id=user.id, workspace_id=ws.id, role=role))
        else:
            member.role = role
    return user


def create_api_key(workspace_id: str, name: str, role: str = "member", created_by: str | None = None) -> str:
    """Create a key and return its plaintext once; only a hash is stored."""
    if role not in ROLES:
        raise AuthError(f"Role must be one of {', '.join(ROLES)}.")
    token = "ee_" + secrets.token_urlsafe(32)
    with session_scope() as session:
        session.add(
            ApiKey(
                workspace_id=workspace_id,
                name=name,
                prefix=token[:10],
                key_hash=_sha(token),
                role=role,
                created_by=created_by,
            )
        )
    return token


# ---------------------------------------------------------------------------- request authentication


@dataclass
class Principal:
    workspace_id: str | None
    user: str
    role: str
    via: str  # local | session | api_key


def login(email: str, password: str) -> str:
    with session_scope() as session:
        user = session.scalars(select(User).where(User.email == email.strip().lower())).first()
        if user is None or not verify_password(password, user.password_hash):
            raise AuthError("Email or password is incorrect.")
        token = secrets.token_urlsafe(32)
        session.add(
            UserSession(token_hash=_sha(token), user_id=user.id, expires_at=now() + timedelta(days=SESSION_DAYS))
        )
    return token


def logout(token: str) -> None:
    with session_scope() as session:
        found = session.get(UserSession, _sha(token))
        if found:
            session.delete(found)


def memberships(session: Session, user_id: str) -> list[tuple[Workspace, str]]:
    rows = session.execute(
        select(Workspace, Membership.role)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user_id)
        .order_by(Workspace.name)
    ).all()
    return [(ws, role) for ws, role in rows]


def authenticate(bearer: str | None, session_token: str | None, workspace_hint: str | None) -> Principal:
    if not auth_enabled():
        return Principal(workspace_id=None, user="local", role="owner", via="local")
    with session_scope() as session:
        if bearer and bearer.startswith(("ee_", "bt_")):  # bt_: keys made before the rename
            key = session.scalars(select(ApiKey).where(ApiKey.key_hash == _sha(bearer))).first()
            if key is None or key.revoked_at is not None:
                raise AuthError("Invalid or revoked API key.")
            key.last_used_at = now()
            return Principal(workspace_id=key.workspace_id, user=f"key:{key.name}", role=key.role, via="api_key")
        token = session_token or bearer
        if not token:
            raise AuthError("Sign in or send an API key.")
        found = session.get(UserSession, _sha(token))
        expires = found.expires_at if found else None
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=now().tzinfo)
        if found is None or expires < now():
            raise AuthError("Session expired; sign in again.")
        user = session.get(User, found.user_id)
        options = memberships(session, user.id)
        if not options:
            raise AuthError("You are not a member of any workspace.")
        chosen = next(((ws, role) for ws, role in options if ws.id == workspace_hint), options[0])
        return Principal(workspace_id=chosen[0].id, user=user.email, role=chosen[1], via="session")


# ---------------------------------------------------------------------------- secrets


def _fernet() -> Fernet:
    key = env("SECRET_KEY")
    if not key:
        raise AuthError("Set EVERYEVAL_SECRET_KEY on the server to store secrets.")
    # Accept any string: derive a Fernet key from it.
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def set_secret(workspace_id: str | None, name: str, value: str) -> None:
    if not SECRET_NAME.match(name):
        raise AuthError("Secret names are environment variable names, e.g. OPENAI_API_KEY.")
    token = _fernet().encrypt(value.encode()).decode()
    with session_scope() as session:
        found = session.scalars(select(Secret).where(Secret.workspace_id == workspace_id, Secret.name == name)).first()
        if found:
            found.ciphertext, found.updated_at = token, now()
        else:
            session.add(Secret(workspace_id=workspace_id, name=name, ciphertext=token))


def delete_secret(workspace_id: str | None, name: str) -> None:
    with session_scope() as session:
        found = session.scalars(select(Secret).where(Secret.workspace_id == workspace_id, Secret.name == name)).first()
        if found:
            session.delete(found)


def list_secrets(workspace_id: str | None) -> list[dict]:
    with session_scope() as session:
        rows = session.scalars(select(Secret).where(Secret.workspace_id == workspace_id).order_by(Secret.name)).all()
        return [{"name": s.name, "updated_at": s.updated_at.isoformat()} for s in rows]


def secrets_for(workspace_id: str | None) -> dict[str, str]:
    """Decrypted secrets for a workspace, for its run processes and imports only."""
    with session_scope() as session:
        rows = session.scalars(select(Secret).where(Secret.workspace_id == workspace_id)).all()
        if not rows:
            return {}
        fernet = _fernet()
        out = {}
        for s in rows:
            try:
                out[s.name] = fernet.decrypt(s.ciphertext.encode()).decode()
            except InvalidToken as ex:
                raise AuthError(f"Secret {s.name} cannot be decrypted; was EVERYEVAL_SECRET_KEY changed?") from ex
        return out
