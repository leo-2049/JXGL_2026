"""通用服务：配置、人员解析、待办、消息、操作日志、签名。"""
import datetime as dt
import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.model import now
from ..integrations.oa import get_oa
from .models import Config, Dept, MsgLog, MsgTpl, OperLog, Signature, Todo, User, UserRole

log = logging.getLogger("qms.sys")


# ---------------- 配置 ----------------
def cfg(db: Session, key: str, default=None, cast=float):
    row = db.scalar(select(Config).where(Config.cfg_key == key))
    if row is None or row.cfg_value in (None, ""):
        return default
    try:
        return cast(row.cfg_value)
    except (TypeError, ValueError):
        return default


# ---------------- 人员解析（岗位 / 角色 → 当前任职人） ----------------
def user_name(db: Session, user_id: str | None) -> str | None:
    if not user_id:
        return None
    return db.scalar(select(User.user_name).where(User.user_id == user_id)) or user_id


def users_by_role(db: Session, *roles: str) -> list[str]:
    return sorted(set(db.scalars(select(UserRole.user_id).where(UserRole.role_code.in_(roles)))))


def users_by_post(db: Session, *posts: str) -> list[str]:
    out = []
    for u in db.scalars(select(User).where(User.enabled.is_(True))):
        mine = set((u.posts or "").split(","))
        if mine & set(posts):
            out.append(u.user_id)
    return out


def dept_manager(db: Session, dept_id: int | None) -> str | None:
    if not dept_id:
        return None
    return db.scalar(select(Dept.manager_user_id).where(Dept.dept_id == dept_id))


def dept_name(db: Session, dept_id) -> str | None:
    if not dept_id:
        return None
    return db.scalar(select(Dept.dept_name).where(Dept.dept_id == int(dept_id)))


def user_dept(db: Session, user_id: str) -> int | None:
    return db.scalar(select(User.dept_id).where(User.user_id == user_id))


# ---------------- 操作日志 ----------------
def oper_log(db: Session, biz_type: str, biz_id: int, biz_no: str | None, action: str, action_name: str,
             from_status: str | None, to_status: str | None, user, opinion: str | None = None,
             field_changes: list | None = None):
    db.add(OperLog(
        biz_type=biz_type, biz_id=biz_id, biz_no=biz_no, action=action, action_name=action_name,
        from_status=from_status, to_status=to_status, opinion=opinion, field_changes=field_changes,
        user_id=getattr(user, "user_id", "system"), user_name=getattr(user, "user_name", "系统"),
        ip=getattr(user, "ip", None),
    ))


# ---------------- 消息 ----------------
def render(tpl: str, vars_: dict) -> str:
    return re.sub(r"\{([^{}]+)\}", lambda m: str(vars_.get(m.group(1), m.group(0))), tpl or "")


def doc_link(biz_type: str, biz_id: int | None) -> str:
    return f"{get_settings().frontend_base_url}/#/open/{biz_type}/{biz_id or ''}"


def send_msg(db: Session, event_code: str, receivers: list[str], vars_: dict, biz_type: str | None = None,
             biz_id: int | None = None, title: str | None = None, body: str | None = None, force: bool = False):
    """按模板发消息。同一接收人同一单据同一事件 30 分钟内只发一条；22:00～7:00 不推 APP（站内保留）。"""
    tpl = db.scalar(select(MsgTpl).where(MsgTpl.event_code == event_code))
    if tpl is not None and not tpl.enabled:
        return []
    title = title or render(tpl.title_tpl if tpl else event_code, vars_)
    body = body or render(tpl.body_tpl if tpl else "", vars_)
    channels = (tpl.channels if tpl else "SITE").split(",")
    link = doc_link(biz_type, biz_id) if biz_type else ""
    t = now()
    sent = []
    receivers = [r for r in dict.fromkeys(receivers) if r]
    to_send = []
    for r in receivers:
        if not force and biz_id is not None:
            recent = db.scalar(select(MsgLog.id).where(
                MsgLog.event_code == event_code, MsgLog.receiver == r, MsgLog.biz_type == biz_type,
                MsgLog.biz_id == biz_id, MsgLog.sent_at >= t - dt.timedelta(minutes=30)).limit(1))
            if recent:
                continue
        to_send.append(r)
    if not to_send:
        return []
    quiet = t.hour >= 22 or t.hour < 7
    urgent = event_code in ("spc.alarm", "insp.fail")
    push_app = "APP" in channels and (not quiet or urgent)
    status, err = "SENT", None
    try:
        get_oa().send_message(to_send, title, body, link, push_app)
    except Exception as e:  # noqa: BLE001 - 消息失败不影响业务，记日志
        status, err = "FAILED", str(e)[:500]
        log.warning("OA 消息发送失败 %s: %s", event_code, e)
    for r in to_send:
        for ch in channels:
            if ch == "MAIL":
                continue
            db.add(MsgLog(event_code=event_code, biz_type=biz_type, biz_id=biz_id, receiver=r, channel=ch,
                          title=title, body=body, link=link,
                          status=status if (ch != "APP" or push_app) else "SKIPPED", error=err))
        sent.append(r)
    return sent


def send_mail(db: Session, event_code: str, to: list[str], subject: str, body: str, biz_type=None, biz_id=None,
              attachments: list | None = None):
    """对外邮件（客户、供应商）。统一从公司邮箱发出；此处落记录，由邮件网关异步投递。"""
    for addr in to:
        db.add(MsgLog(event_code=event_code, biz_type=biz_type, biz_id=biz_id, receiver=addr, channel="MAIL",
                      title=subject, body=body + (f"\n附件：{', '.join(attachments)}" if attachments else ""),
                      status="SENT"))


def notify_admins(db: Session, event_code: str, vars_: dict):
    send_msg(db, event_code, users_by_role(db, "ADMIN"), vars_, force=True)


# ---------------- 待办 ----------------
def open_todos(db: Session, biz_type: str, biz_id: int, node: str | None = None) -> list[Todo]:
    q = select(Todo).where(Todo.biz_type == biz_type, Todo.biz_id == biz_id, Todo.status == "OPEN")
    if node:
        q = q.where(Todo.node == node)
    return list(db.scalars(q))


def create_todo(db: Session, biz_type, biz_id, biz_no, node, node_name, title, assignee, due_at=None):
    exists = db.scalar(select(Todo).where(Todo.biz_type == biz_type, Todo.biz_id == biz_id, Todo.node == node,
                                          Todo.assignee == assignee, Todo.status == "OPEN"))
    if exists:
        return exists
    t = Todo(biz_type=biz_type, biz_id=biz_id, biz_no=biz_no, node=node, node_name=node_name, title=title,
             assignee=assignee, assignee_name=user_name(db, assignee), due_at=due_at,
             link=doc_link(biz_type, biz_id))
    db.add(t)
    return t


def close_todos(db: Session, biz_type, biz_id, node=None, assignee=None, status="DONE"):
    for t in open_todos(db, biz_type, biz_id, node):
        if assignee and t.assignee != assignee:
            continue
        t.status, t.done_at = status, now()


# ---------------- 签名 ----------------
def save_signature(db: Session, user, image: str | None, biz_type: str, biz_id: int, node: str,
                   required: bool = True) -> int | None:
    """手写签名必须是本人当场书写的图像（data:image/png;base64,...）；仅点按钮不算签名。"""
    from ..core.errors import BizError

    if not image:
        if required:
            raise BizError("【手写签名】请在签名板上手写签名后再提交")
        return None
    if not image.startswith("data:image/") or len(image) < 200:
        raise BizError("【手写签名】签名图像无效，请重新书写")
    sig = Signature(user_id=user.user_id, user_name=user.user_name, image=image, biz_type=biz_type, biz_id=biz_id,
                    node=node, ip=getattr(user, "ip", None), device=getattr(user, "device", None))
    db.add(sig)
    db.flush()
    return sig.id
