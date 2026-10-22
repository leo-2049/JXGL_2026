"""登录态与权限。身份来源唯一：通达 OA OAuth2 授权码登录（或开发模式下的模拟登录）。"""
import datetime as dt
from dataclasses import dataclass, field

import jwt
from fastapi import Depends, Header, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from .errors import BizError, Forbidden

ALGO = "HS256"


@dataclass
class CurrentUser:
    user_id: str
    user_name: str
    dept_id: int | None = None
    dept_name: str | None = None
    posts: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    perms: set[str] = field(default_factory=set)
    ip: str | None = None
    device: str | None = None

    @property
    def is_admin(self) -> bool:
        return "*" in self.perms or "ADMIN" in self.roles

    def has(self, perm: str) -> bool:
        if self.is_admin or perm in self.perms:
            return True
        # 模块通配：nc.* 覆盖 nc.add
        mod = perm.split(".")[0]
        return f"{mod}.*" in self.perms

    def has_role(self, *roles: str) -> bool:
        return self.is_admin or any(r in self.roles for r in roles)

    def to_dict(self):
        return {
            "user_id": self.user_id, "user_name": self.user_name, "dept_id": self.dept_id,
            "dept_name": self.dept_name, "posts": self.posts, "roles": self.roles, "perms": sorted(self.perms),
            "is_admin": self.is_admin,
        }


def create_token(user_id: str, extra: dict | None = None) -> str:
    st = get_settings()
    payload = {"sub": user_id, "exp": dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=st.token_expire_minutes)}
    payload.update(extra or {})
    return jwt.encode(payload, st.secret_key, algorithm=ALGO)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, get_settings().secret_key, algorithms=[ALGO])
    except jwt.PyJWTError as e:
        raise BizError("登录已过期，请从 OA 重新进入", status_code=401) from e


def load_user(db: Session, user_id: str) -> CurrentUser:
    from ..sys.models import Dept, Role, User, UserRole

    u = db.scalar(select(User).where(User.user_id == user_id, User.enabled.is_(True)))
    if not u:
        raise BizError("账号不存在或已禁用，请联系系统管理员同步 OA 组织架构", status_code=401)
    role_codes = list(db.scalars(select(UserRole.role_code).where(UserRole.user_id == user_id)))
    perms: set[str] = set()
    for r in db.scalars(select(Role).where(Role.role_code.in_(role_codes or ["__none__"]))):
        perms.update(r.perms or [])
    dept = db.scalar(select(Dept).where(Dept.dept_id == u.dept_id)) if u.dept_id else None
    return CurrentUser(
        user_id=u.user_id, user_name=u.user_name, dept_id=u.dept_id, dept_name=dept.dept_name if dept else None,
        posts=[p for p in (u.posts or "").split(",") if p], roles=role_codes, perms=perms,
    )


def current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None, alias="access_token"),
    db: Session = Depends(get_db),
) -> CurrentUser:
    raw = None
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:]
    elif token:
        raw = token
    if not raw:
        raise BizError("未登录，请从通达 OA 进入质量平台", status_code=401)
    data = decode_token(raw)
    u = load_user(db, data["sub"])
    u.ip = request.client.host if request.client else None
    u.device = request.headers.get("user-agent", "")[:200]
    return u


def require(perm: str):
    def dep(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if not user.has(perm):
            raise Forbidden(f"您没有【{perm}】权限，请联系系统管理员在「角色与按钮权限」中配置")
        return user

    return dep
