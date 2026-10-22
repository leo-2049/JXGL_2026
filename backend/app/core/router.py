"""通用路由：流程单据（列表 / 详情 / 新建 / 编辑 / 动作）与台账 CRUD。"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..sys import services as svc
from ..sys.models import Todo
from . import flow as fl
from .errors import BizError, Forbidden, NotFound
from .model import coerce, columns_of, to_dict
from .security import CurrentUser, current_user


def list_query(db: Session, model, params: dict, like_fields: tuple = (), base=None):
    """通用查询：?page=&size=&keyword=&status=&<字段>=&<字段>_from=&<字段>_to=&order=-id"""
    cols = columns_of(model)
    q = base if base is not None else select(model)
    if "is_deleted" in cols:
        q = q.where(model.is_deleted.is_(False))
    kw = (params.get("keyword") or "").strip()
    if kw and like_fields:
        q = q.where(or_(*[getattr(model, f).like(f"%{kw}%") for f in like_fields]))
    for k, v in params.items():
        if v in (None, "") or k in ("page", "size", "keyword", "order", "mine"):
            continue
        if k.endswith("_from") and k[:-5] in cols:
            q = q.where(getattr(model, k[:-5]) >= coerce(model, k[:-5], v))
        elif k.endswith("_to") and k[:-3] in cols:
            val = coerce(model, k[:-3], v)
            if isinstance(v, str) and len(v) == 10 and hasattr(val, "hour"):
                val = val.replace(hour=23, minute=59, second=59)
            q = q.where(getattr(model, k[:-3]) <= val)
        elif k.endswith("_like") and k[:-5] in cols:
            q = q.where(getattr(model, k[:-5]).like(f"%{v}%"))
        elif k in cols:
            if isinstance(v, str) and "," in v:
                q = q.where(getattr(model, k).in_(v.split(",")))
            else:
                q = q.where(getattr(model, k) == coerce(model, k, v))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    order = params.get("order") or "-id"
    oc = getattr(model, order.lstrip("-"), model.id)
    q = q.order_by(oc.desc() if order.startswith("-") else oc.asc())
    page, size = int(params.get("page") or 1), min(int(params.get("size") or 20), 2000)
    rows = list(db.scalars(q.offset((page - 1) * size).limit(size)))
    return rows, total


def flow_router(
    flow: fl.FlowDef,
    like_fields: tuple = (),
    on_create: Callable[[Session, Any, dict, CurrentUser], None] | None = None,
    after_create: Callable[[Session, Any, dict, CurrentUser], None] | None = None,
    extra_detail: Callable[[Session, Any, CurrentUser], dict] | None = None,
    row_extra: Callable[[Session, list], list[dict]] | None = None,
    add_perm: str | None = None,
    prefix: str | None = None,
) -> APIRouter:
    r = APIRouter(prefix=prefix or f"/api/{flow.biz_type}", tags=[flow.name])
    model = flow.model

    @r.get("")
    def list_(request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
        params = dict(request.query_params)
        base = None
        if params.get("mine") == "todo":
            ids = select(Todo.biz_id).where(Todo.biz_type == flow.biz_type, Todo.assignee == user.user_id,
                                            Todo.status == "OPEN")
            base = select(model).where(model.id.in_(ids))
        elif params.get("mine") == "created":
            base = select(model).where(model.created_by == user.user_id)
        rows, total = list_query(db, model, params, like_fields or (flow.no_field,), base)
        items = [to_dict(x) | {"status_name": flow.states.get(x.status, x.status)} for x in rows]
        if row_extra:
            items = row_extra(db, items)
        return {"items": items, "total": total}

    @r.get("/{doc_id}")
    def get_(doc_id: int, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
        doc = _load(db, model, doc_id)
        out = fl.detail(db, flow, doc, user)
        if extra_detail:
            out.update(extra_detail(db, doc, user))
        return out

    @r.post("")
    def create(data: dict = Body(...), db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
        if add_perm and not user.has(add_perm):
            raise Forbidden(f"您没有新建权限（{add_perm}）")
        if on_create:
            on_create(db, None, data, user)
        doc = fl.new_doc(db, flow, user, data, no=data.pop("_no", None))
        if after_create:
            after_create(db, doc, data, user)
        db.commit()
        return fl.detail(db, flow, doc, user)

    @r.put("/{doc_id}")
    def update(doc_id: int, data: dict = Body(...), db: Session = Depends(get_db),
               user: CurrentUser = Depends(current_user)):
        doc = _load(db, model, doc_id)
        fl.apply_update(db, flow, doc, data, user, data.get("row_ver"))
        db.commit()
        return fl.detail(db, flow, doc, user)

    @r.post("/{doc_id}/act/{code}")
    def act(doc_id: int, code: str, payload: dict = Body(default={}), db: Session = Depends(get_db),
            user: CurrentUser = Depends(current_user)):
        doc = _load(db, model, doc_id)
        try:
            ctx = fl.execute(db, flow, doc, code, user, payload)
            db.commit()
        except Exception:
            db.rollback()
            raise
        out = fl.detail(db, flow, doc, user)
        out["result"] = ctx.extra.get("result")
        return out

    @r.delete("/{doc_id}")
    def delete(doc_id: int, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
        doc = _load(db, model, doc_id)
        if doc.status != flow.initial or len(fl.visited(db, flow, doc)) > 0:
            raise BizError("已提交过的单据不可删除，只能作废")
        if doc.created_by != user.user_id and not user.is_admin:
            raise Forbidden("只能删除本人起草的单据")
        doc.is_deleted = True
        svc.close_todos(db, flow.biz_type, doc.id, status="CANCELLED")
        db.commit()
        return {"ok": True}

    return r


def _load(db: Session, model, doc_id: int):
    doc = db.get(model, doc_id)
    if doc is None or getattr(doc, "is_deleted", False):
        raise NotFound()
    return doc


def crud_router(model, prefix: str, tag: str, perm: str | None = None, like_fields: tuple = (),
                unique: tuple = (), before_save: Callable | None = None, read_perm: str | None = None) -> APIRouter:
    """台账类（无流程）通用 CRUD；写操作需 perm 权限，修改保留留痕。"""
    r = APIRouter(prefix=prefix, tags=[tag])
    biz = model.__tablename__

    def _check(user: CurrentUser):
        if perm and not user.has(perm):
            raise Forbidden(f"您没有维护权限（{perm}）")

    @r.get("")
    def list_(request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
        if read_perm and not user.has(read_perm):
            raise Forbidden()
        rows, total = list_query(db, model, dict(request.query_params), like_fields)
        return {"items": [to_dict(x) for x in rows], "total": total}

    @r.get("/{obj_id}")
    def get_(obj_id: int, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
        return to_dict(_load(db, model, obj_id))

    @r.post("")
    def create(data: dict = Body(...), db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
        _check(user)
        obj = model()
        _assign(db, model, obj, data, unique)
        if before_save:
            before_save(db, obj, data, user)
        if hasattr(obj, "created_by"):
            obj.created_by, obj.created_name = user.user_id, user.user_name
        db.add(obj)
        db.flush()
        svc.oper_log(db, biz, obj.id, None, "create", "新增", None, None, user)
        db.commit()
        return to_dict(obj)

    @r.put("/{obj_id}")
    def update(obj_id: int, data: dict = Body(...), db: Session = Depends(get_db),
               user: CurrentUser = Depends(current_user)):
        _check(user)
        obj = _load(db, model, obj_id)
        if data.get("row_ver") is not None and hasattr(obj, "row_ver") and int(data["row_ver"]) != obj.row_ver:
            from .errors import Conflict

            raise Conflict()
        changes = _assign(db, model, obj, data, unique)
        if before_save:
            before_save(db, obj, data, user)
        if hasattr(obj, "updated_by"):
            obj.updated_by = user.user_id
        svc.oper_log(db, biz, obj.id, None, "edit", "修改", None, None, user, field_changes=changes)
        db.commit()
        return to_dict(obj)

    @r.delete("/{obj_id}")
    def delete(obj_id: int, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
        _check(user)
        obj = _load(db, model, obj_id)
        if hasattr(obj, "is_deleted"):
            obj.is_deleted = True
        else:
            db.delete(obj)
        svc.oper_log(db, biz, obj.id, None, "delete", "删除/停用", None, None, user)
        db.commit()
        return {"ok": True}

    return r


def _assign(db, model, obj, data, unique):
    cols = columns_of(model)
    changes = []
    for k, v in data.items():
        if k in ("id", "row_ver", "created_at", "updated_at", "created_by", "is_deleted") or k not in cols:
            continue
        new = coerce(model, k, v)
        old = getattr(obj, k)
        if not fl._same(old, new):
            setattr(obj, k, new)
            changes.append({"field": k, "old": fl._js(old), "new": fl._js(new)})
    for u in unique:
        val = getattr(obj, u)
        if val is None:
            continue
        q = select(model.id).where(getattr(model, u) == val)
        if "is_deleted" in cols:
            q = q.where(model.is_deleted.is_(False))
        if obj.id:
            q = q.where(model.id != obj.id)
        if db.scalar(q):
            raise BizError(f"【{u}】{val} 已存在，请检查")
    return changes
