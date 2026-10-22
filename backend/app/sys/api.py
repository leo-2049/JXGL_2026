"""系统接口：登录（通达 OA SSO）、组织、字典、附件、签名、待办、消息、后台配置、接口监控、日志。"""
import base64
import mimetypes
import os
import secrets
import uuid
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.errors import BizError, NotFound
from ..core.model import now, to_dict
from ..core.router import crud_router, list_query
from ..core.security import CurrentUser, create_token, current_user, load_user, require
from ..db import get_db
from ..integrations.oa import get_oa
from .models import (Attachment, Config, Dept, Dict, IfLog, MsgLog, MsgTpl, NumberRule, OperLog, Role, Signature,
                     Stamp, TimerCfg, Todo, User, UserRole)

router = APIRouter(tags=["系统"])
_states: dict[str, float] = {}


# ======================================================================== 登录
@router.get("/api/auth/config")
def auth_config():
    st = get_settings()
    return {"oa_mode": st.oa_mode, "app_name": st.app_name, "oa_base_url": st.oa_base_url}


@router.get("/api/auth/oa/login")
def oa_login(next: str = "/"):
    """从 OA 菜单进入：跳转 OA OAuth2 授权页（用户已登录 OA 时免登直接回调）。"""
    st = get_settings()
    state = secrets.token_urlsafe(16) + "|" + next
    _states[state] = now().timestamp()
    redirect_uri = f"{st.public_base_url}/api/auth/oa/callback"
    return RedirectResponse(get_oa().authorize_url(state, redirect_uri))


@router.get("/api/auth/oa/callback")
def oa_callback(code: str, state: str, db: Session = Depends(get_db)):
    st = get_settings()
    if state not in _states:
        raise BizError("登录状态校验失败，请从 OA 重新进入", status_code=401)
    _states.pop(state, None)
    tok = get_oa().exchange_code(code, f"{st.public_base_url}/api/auth/oa/callback")
    info = get_oa().userinfo(tok["access_token"])
    u = db.scalar(select(User).where(User.user_id == info.user_id))
    if u is None:
        u = User(user_id=info.user_id, user_name=info.user_name, oa_uid=info.uid, dept_id=info.dept_id,
                 byname=info.byname, enabled=True)
        db.add(u)
        db.add(UserRole(user_id=info.user_id, role_code="STAFF"))
    else:
        u.user_name, u.oa_uid, u.dept_id = info.user_name or u.user_name, info.uid or u.oa_uid, info.dept_id or u.dept_id
    u.synced_at = now()
    db.commit()
    nxt = state.split("|", 1)[1] if "|" in state else "/"
    return RedirectResponse(f"{st.frontend_base_url}/#/sso?token={create_token(info.user_id)}&next={nxt}")


@router.post("/api/auth/dev-login")
def dev_login(data: dict = Body(...), db: Session = Depends(get_db)):
    """开发 / 演示模式（QMS_OA_MODE=mock）下选人登录；对接真实 OA 后此接口关闭。"""
    if get_settings().oa_mode != "mock":
        raise BizError("已对接通达 OA，请从 OA 进入", status_code=403)
    u = load_user(db, data["user_id"])
    return {"token": create_token(u.user_id), "user": u.to_dict()}


@router.get("/api/auth/dev-users")
def dev_users(db: Session = Depends(get_db)):
    if get_settings().oa_mode != "mock":
        return []
    rows = db.execute(select(User.user_id, User.user_name, User.posts, Dept.dept_name)
                      .join(Dept, Dept.dept_id == User.dept_id, isouter=True).where(User.enabled.is_(True))
                      .order_by(User.dept_id, User.id))
    return [{"user_id": a, "user_name": b, "posts": c, "dept_name": d} for a, b, c, d in rows]


@router.get("/api/me")
def me(user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    out = user.to_dict()
    out["todo_count"] = db.scalar(select(func.count()).select_from(Todo).where(
        Todo.assignee == user.user_id, Todo.status == "OPEN"))
    out["unread"] = db.scalar(select(func.count()).select_from(MsgLog).where(
        MsgLog.receiver == user.user_id, MsgLog.channel == "SITE", MsgLog.read_at.is_(None)))
    return out


# ======================================================================== 组织（人员 / 部门选择器）
@router.get("/api/org/users")
def org_users(keyword: str = "", dept_id: int | None = None, post: str = "", db: Session = Depends(get_db),
              _: CurrentUser = Depends(current_user)):
    q = select(User).where(User.enabled.is_(True))
    if keyword:
        q = q.where((User.user_name.like(f"%{keyword}%")) | (User.user_id.like(f"%{keyword}%")))
    if dept_id:
        q = q.where(User.dept_id == dept_id)
    rows = [u for u in db.scalars(q.order_by(User.dept_id, User.id)) if not post or post in (u.posts or "")]
    depts = {d.dept_id: d.dept_name for d in db.scalars(select(Dept))}
    return [{"user_id": u.user_id, "user_name": u.user_name, "dept_id": u.dept_id, "dept_name": depts.get(u.dept_id),
             "posts": u.posts, "phone": u.phone} for u in rows]


@router.get("/api/org/depts")
def org_depts(db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    return [to_dict(d) for d in db.scalars(select(Dept).order_by(Dept.sort, Dept.dept_id))]


@router.post("/api/org/sync")
def org_sync(db: Session = Depends(get_db), user: CurrentUser = Depends(require("sys.role"))):
    """从通达 OA 同步部门与人员（新增 / 更新 / 离职禁用）。"""
    org = get_oa().fetch_org()
    if not org.depts and not org.users:
        return {"ok": True, "message": "当前为模拟 OA 模式，组织取自本地初始化数据", "depts": 0, "users": 0}
    for dp in org.depts:
        row = db.scalar(select(Dept).where(Dept.dept_id == dp["dept_id"])) or Dept(dept_id=dp["dept_id"])
        row.dept_name, row.parent_id = dp["dept_name"], dp["parent_id"]
        db.add(row)
    seen = set()
    for u in org.users:
        seen.add(u["user_id"])
        row = db.scalar(select(User).where(User.user_id == u["user_id"]))
        if row is None:
            row = User(user_id=u["user_id"])
            db.add(row)
            db.add(UserRole(user_id=u["user_id"], role_code="STAFF"))
        row.user_name, row.dept_id, row.enabled, row.synced_at = u["user_name"], u["dept_id"], True, now()
    for row in db.scalars(select(User).where(User.enabled.is_(True))):
        if row.user_id not in seen and row.synced_at is not None:
            row.enabled = False
            _transfer_todos(db, row.user_id)
    db.commit()
    return {"ok": True, "depts": len(org.depts), "users": len(org.users)}


def _transfer_todos(db: Session, user_id: str):
    """人员离职：待办转给同岗位新任职人（5.8.8 通用消息规则 2）。"""
    from .services import oper_log, users_by_post

    old = db.scalar(select(User).where(User.user_id == user_id))
    posts = [p for p in (old.posts or "").split(",") if p] if old else []
    heirs = [u for u in users_by_post(db, *posts) if u != user_id] if posts else []
    for t in db.scalars(select(Todo).where(Todo.assignee == user_id, Todo.status == "OPEN")):
        if heirs:
            oper_log(db, t.biz_type, t.biz_id, t.biz_no, "transfer", "待办转移", t.node, t.node, None,
                     f"{user_id} 离职，待办转给 {heirs[0]}")
            t.assignee = heirs[0]


@router.put("/api/me/signature")
def save_my_sig(data: dict = Body(...), db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
    u = db.scalar(select(User).where(User.user_id == user.user_id))
    u.sig_image = data.get("image")
    db.commit()
    return {"ok": True}


# ======================================================================== 待办与消息
@router.get("/api/todos")
def my_todos(request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
    params = dict(request.query_params)
    params.setdefault("status", "OPEN")
    base = select(Todo).where(Todo.assignee == user.user_id)
    rows, total = list_query(db, Todo, params, ("title", "biz_no"), base)
    t = now()
    return {"items": [to_dict(x) | {"overdue": bool(x.due_at and x.due_at < t and x.status == "OPEN")} for x in rows],
            "total": total}


@router.get("/api/messages")
def my_msgs(request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
    base = select(MsgLog).where(MsgLog.receiver == user.user_id, MsgLog.channel == "SITE")
    rows, total = list_query(db, MsgLog, dict(request.query_params), ("title",), base)
    return {"items": [to_dict(x) for x in rows], "total": total}


@router.post("/api/messages/read")
def read_msgs(data: dict = Body(default={}), db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
    q = select(MsgLog).where(MsgLog.receiver == user.user_id, MsgLog.read_at.is_(None))
    if data.get("ids"):
        q = q.where(MsgLog.id.in_(data["ids"]))
    for m in db.scalars(q):
        m.read_at = now()
    db.commit()
    return {"ok": True}


# ======================================================================== 字典
@router.get("/api/dicts")
def dicts(types: str = "", db: Session = Depends(get_db)):
    q = select(Dict).where(Dict.enabled.is_(True)).order_by(Dict.dict_type, Dict.sort, Dict.id)
    if types:
        q = q.where(Dict.dict_type.in_(types.split(",")))
    out: dict[str, list] = {}
    for d in db.scalars(q):
        out.setdefault(d.dict_type, []).append({"value": d.code, "label": d.name, "extra": d.extra})
    return out


# ======================================================================== 附件
def _upload_root() -> Path:
    p = Path(get_settings().upload_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


ALLOWED_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".doc", ".docx", ".xls", ".xlsx", ".csv",
               ".txt", ".zip"}


@router.post("/api/attachments")
async def upload(file: UploadFile = File(...), biz_type: str = Form(""), biz_id: int | None = Form(None),
                 field_code: str = Form(""), db: Session = Depends(get_db),
                 user: CurrentUser = Depends(current_user)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise BizError(f"【附件】不支持 {ext} 格式，仅支持 PDF / Excel / Word / 图片")
    content = await file.read()
    if len(content) > get_settings().max_upload_mb * 1024 * 1024:
        raise BizError(f"【附件】单个文件不能超过 {get_settings().max_upload_mb} MB")
    sub = now().strftime("%Y%m")
    (_upload_root() / sub).mkdir(exist_ok=True)
    rel = f"{sub}/{uuid.uuid4().hex}{ext}"
    (_upload_root() / rel).write_bytes(content)
    att = Attachment(biz_type=biz_type or None, biz_id=biz_id, field_code=field_code or None,
                     file_name=file.filename, file_size=len(content),
                     mime=file.content_type or mimetypes.guess_type(file.filename or "")[0],
                     storage_path=rel, uploaded_by=user.user_id)
    db.add(att)
    db.commit()
    return to_dict(att)


@router.post("/api/attachments/bind")
def bind(data: dict = Body(...), db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    for aid in data.get("ids", []):
        a = db.get(Attachment, aid)
        if a:
            a.biz_type, a.biz_id = data["biz_type"], data["biz_id"]
            a.field_code = data.get("field_code") or a.field_code
    db.commit()
    return {"ok": True}


@router.get("/api/attachments")
def list_atts(biz_type: str, biz_id: int, field_code: str = "", db: Session = Depends(get_db),
              _: CurrentUser = Depends(current_user)):
    q = select(Attachment).where(Attachment.biz_type == biz_type, Attachment.biz_id == biz_id)
    if field_code:
        q = q.where(Attachment.field_code == field_code)
    return [to_dict(a) for a in db.scalars(q.order_by(Attachment.id))]


@router.get("/api/attachments/{att_id}/file")
def download(att_id: int, inline: int = 1, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    a = db.get(Attachment, att_id)
    if not a:
        raise NotFound("附件不存在")
    path = _upload_root() / a.storage_path
    if not path.exists():
        raise NotFound("附件文件已丢失")
    return FileResponse(path, media_type=a.mime or "application/octet-stream", filename=a.file_name,
                        content_disposition_type="inline" if inline else "attachment")


@router.delete("/api/attachments/{att_id}")
def del_att(att_id: int, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
    a = db.get(Attachment, att_id)
    if a and (a.uploaded_by == user.user_id or user.is_admin):
        db.delete(a)
        db.commit()
    return {"ok": True}


def save_bytes(db: Session, content: bytes, file_name: str, mime: str, biz_type: str, biz_id: int, field_code: str,
               user_id: str = "system") -> Attachment:
    sub = now().strftime("%Y%m")
    (_upload_root() / sub).mkdir(exist_ok=True)
    rel = f"{sub}/{uuid.uuid4().hex}{os.path.splitext(file_name)[1]}"
    (_upload_root() / rel).write_bytes(content)
    att = Attachment(biz_type=biz_type, biz_id=biz_id, field_code=field_code, file_name=file_name,
                     file_size=len(content), mime=mime, storage_path=rel, uploaded_by=user_id)
    db.add(att)
    db.flush()
    return att


# ======================================================================== 签名
@router.get("/api/signatures/{sig_id}")
def get_sig(sig_id: int, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    s = db.get(Signature, sig_id)
    if not s:
        raise NotFound()
    return to_dict(s)


@router.get("/api/signatures/{sig_id}/image")
def sig_image(sig_id: int, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    from fastapi import Response

    s = db.get(Signature, sig_id)
    if not s or not s.image:
        raise NotFound()
    raw = base64.b64decode(s.image.split(",", 1)[1])
    return Response(raw, media_type="image/png")


# ======================================================================== 后台配置
router.include_router(crud_router(Dict, "/api/sys/dict", "字典", "sys.dict", ("dict_type", "code", "name")))
router.include_router(crud_router(NumberRule, "/api/sys/number", "编号规则", "sys.number", ("biz_type", "name")))
router.include_router(crud_router(TimerCfg, "/api/sys/timer", "时限与提醒", "sys.timer", ("biz_type", "node")))
router.include_router(crud_router(MsgTpl, "/api/sys/msgtpl", "消息模板", "sys.msg", ("event_code", "event_name")))
router.include_router(crud_router(Role, "/api/sys/role", "角色", "sys.role", ("role_code", "role_name"),
                                  unique=("role_code",)))
router.include_router(crud_router(Config, "/api/sys/config", "参数配置", "sys.config", ("cfg_key", "name")))
router.include_router(crud_router(Stamp, "/api/sys/stamp", "电子章", "sys.config", ("stamp_code", "name")))
router.include_router(crud_router(User, "/api/sys/user", "人员", "sys.role", ("user_id", "user_name")))
router.include_router(crud_router(Dept, "/api/sys/dept", "部门", "sys.role", ("dept_name",)))


@router.get("/api/sys/user-roles")
def user_roles(user_id: str = "", db: Session = Depends(get_db), _: CurrentUser = Depends(require("sys.role"))):
    q = select(UserRole)
    if user_id:
        q = q.where(UserRole.user_id == user_id)
    return [to_dict(x) for x in db.scalars(q)]


@router.put("/api/sys/user-roles/{user_id}")
def set_user_roles(user_id: str, data: dict = Body(...), db: Session = Depends(get_db),
                   user: CurrentUser = Depends(require("sys.role"))):
    from .services import oper_log

    old = [x.role_code for x in db.scalars(select(UserRole).where(UserRole.user_id == user_id))]
    for x in db.scalars(select(UserRole).where(UserRole.user_id == user_id)):
        db.delete(x)
    db.flush()
    for rc in data.get("roles", []):
        db.add(UserRole(user_id=user_id, role_code=rc))
    oper_log(db, "qms_sys_user_role", 0, user_id, "edit", "角色调整", None, None, user,
             field_changes=[{"field": "roles", "old": old, "new": data.get("roles", [])}])
    db.commit()
    return {"ok": True}


# ======================================================================== 接口监控 / 日志
@router.get("/api/sys/iflog")
def iflog(request: Request, db: Session = Depends(get_db), _: CurrentUser = Depends(require("sys.if"))):
    rows, total = list_query(db, IfLog, dict(request.query_params), ("biz_no", "if_code"))
    summary = db.execute(select(IfLog.if_code, IfLog.status, func.count(), func.max(IfLog.at))
                         .group_by(IfLog.if_code, IfLog.status)).all()
    return {"items": [to_dict(x) for x in rows], "total": total,
            "summary": [{"if_code": a, "status": b, "count": c, "last_at": str(d)} for a, b, c, d in summary]}


@router.post("/api/sys/iflog/{log_id}/resend")
def iflog_resend(log_id: int, db: Session = Depends(get_db), _: CurrentUser = Depends(require("sys.if"))):
    from ..integrations import erp

    rec = db.get(IfLog, log_id)
    if not rec:
        raise NotFound()
    erp.resend(db, rec)
    db.commit()
    return to_dict(rec)


@router.get("/api/sys/operlog")
def operlog(request: Request, db: Session = Depends(get_db), _: CurrentUser = Depends(require("sys.log"))):
    rows, total = list_query(db, OperLog, dict(request.query_params), ("biz_no", "user_name", "action_name"))
    return {"items": [to_dict(x) for x in rows], "total": total}


@router.post("/api/sys/scheduler/run")
def run_scheduler(db: Session = Depends(get_db), _: CurrentUser = Depends(require("sys.timer"))):
    from ..scheduler import tick

    return tick(db)
