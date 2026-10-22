"""文档管理接口（5.1.8 页面与操作清单）。"""
import datetime as dt

from fastapi import APIRouter, Body, Depends, File, Request, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.errors import BizError, Checker, Forbidden, NotFound
from ..core.excel import export, read_rows
from ..core.model import now, to_dict
from ..core.router import flow_router, list_query
from ..core.security import CurrentUser, current_user, require
from ..db import get_db
from ..sys import services as svc
from ..sys.models import Attachment, OperLog
from . import flows
from .flows import BORROW_FLOW, DCR_FLOW, REISSUE_FLOW, LEVEL_NAMES, make_dist, next_rev, _create_recall_for_all
from .models import (Borrow, BorrowItem, Dcr, Dist, DistItem, DocFile, DocSign, DocSignDept, DocVersion, Drawing,
                     DrawingAck, Recall, Reissue)

router = APIRouter(tags=["文档管理"])

FILE_STATUS = {"NEW": "新建中", "CURRENT": "现行", "REVISING": "修订中", "OBSOLETE": "已作废"}
DWG_STATUS = {"TRIAL": "试产版", "RELEASED": "受控正式版", "PENDING": "待发行", "OBSOLETE": "已作废"}


def _dcr_create(db, _, data, user):
    data.setdefault("apply_dept_id", user.dept_id)
    data.setdefault("applicant", user.user_id)
    if data.get("file_id"):
        f = db.get(DocFile, int(data["file_id"]))
        if f:
            data.update(file_no=f.file_no, file_name=f.file_name, file_level=f.file_level, cur_rev=f.rev,
                        owner_dept_id=f.owner_dept_id, new_rev=next_rev(f.rev))


def _dcr_detail(db, doc, user):
    sign = db.scalar(select(DocSign).where(DocSign.dcr_id == doc.id))
    rows = db.scalars(select(DocSignDept).where(DocSignDept.dcr_id == doc.id).order_by(DocSignDept.round,
                                                                                         DocSignDept.id))
    return {"sign": to_dict(sign), "sign_depts": [to_dict(r) | {"dept_name": svc.dept_name(db, r.dept_id)} for r in rows],
            "file": to_dict(db.get(DocFile, doc.file_id)) if doc.file_id else None}


router.include_router(flow_router(DCR_FLOW, ("dcr_no", "file_no", "file_name"), on_create=_dcr_create,
                                  extra_detail=_dcr_detail, add_perm="doc.dcr.add", prefix="/api/doc/dcr"))


def _borrow_create(db, _, data, user):
    data.setdefault("dept_id", user.dept_id)
    data.setdefault("borrower", user.user_id)
    data.setdefault("borrow_date", str(dt.date.today()))
    days = int(svc.cfg(db, "doc.borrow_days", 7))
    data.setdefault("plan_return_date", str(dt.date.today() + dt.timedelta(days=days)))
    for it in data.get("items", []):
        f = db.get(DocFile, int(it["file_id"]))
        if not f or f.status not in ("CURRENT", "REVISING"):
            raise BizError("【文件】只能借阅现行版文件")
        it.update(file_no=f.file_no, file_name=f.file_name, rev=f.rev)


router.include_router(flow_router(BORROW_FLOW, ("borrow_no",), on_create=_borrow_create, prefix="/api/doc/borrow"))


def _reissue_create(db, _, data, user):
    data.setdefault("dept_id", user.dept_id)
    if data.get("file_id"):
        f = db.get(DocFile, int(data["file_id"]))
        data.update(file_no=f.file_no, file_name=f.file_name, file_rev=f.rev)
    if data.get("drawing_id"):
        dw = db.get(Drawing, int(data["drawing_id"]))
        data.update(part_no=dw.part_no, model=dw.drawing_no)


router.include_router(flow_router(REISSUE_FLOW, ("reissue_no", "file_no", "part_no"), on_create=_reissue_create,
                                  prefix="/api/doc/reissue"))


# ======================================================================== 受控文件台账
def _visible(q, user):
    """作废版仅文控可查（5.1.6 规则 1）。"""
    if not (user.has_role("DC") or user.is_admin):
        q = q.where(DocFile.status != "OBSOLETE")
    return q


@router.get("/api/doc/files")
def files(request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
    params = dict(request.query_params)
    rows, total = list_query(db, DocFile, params, ("file_no", "file_name"), _visible(select(DocFile), user))
    parents = {f.id: f.file_no for f in db.scalars(select(DocFile).where(
        DocFile.id.in_([r.parent_file_id for r in rows if r.parent_file_id] or [0])))}
    return {"items": [to_dict(r) | {"status_name": FILE_STATUS.get(r.status), "level_name": LEVEL_NAMES.get(r.file_level),
                                    "parent_no": parents.get(r.parent_file_id),
                                    "owner_dept_name": svc.dept_name(db, r.owner_dept_id)} for r in rows],
            "total": total}


@router.get("/api/doc/files/export")
def files_export(request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require("doc.file.export"))):
    params = dict(request.query_params) | {"size": 5000}
    rows, _ = list_query(db, DocFile, params, ("file_no", "file_name"), _visible(select(DocFile), user))
    data = [to_dict(r) | {"status_name": FILE_STATUS.get(r.status), "level_name": LEVEL_NAMES.get(r.file_level),
                          "owner": svc.dept_name(db, r.owner_dept_id)} for r in rows]
    return export("受控文件一览表", [("file_name", "文件名称"), ("file_no", "文件编号"), ("rev", "版本"),
                                    ("owner", "保存单位"), ("level_name", "层级"), ("effective_date", "生效日期"),
                                    ("status_name", "状态"), ("remark", "备注")], data)


@router.get("/api/doc/files/{file_id}")
def file_detail(file_id: int, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
    f = db.get(DocFile, file_id)
    if not f or f.is_deleted or (f.status == "OBSOLETE" and not (user.has_role("DC") or user.is_admin)):
        raise NotFound("文件不存在或已作废（作废版仅文控可查）")
    versions = db.scalars(select(DocVersion).where(DocVersion.file_id == f.id).order_by(DocVersion.id.desc()))
    dists = db.scalars(select(DistItem).where(DistItem.file_id == f.id).order_by(DistItem.id.desc()))
    recalls = db.scalars(select(Recall).where(Recall.file_id == f.id).order_by(Recall.id.desc()))
    dcrs = db.scalars(select(Dcr).where(Dcr.file_id == f.id, Dcr.is_deleted.is_(False)).order_by(Dcr.id.desc()))
    children = db.scalars(select(DocFile).where(DocFile.parent_file_id == f.id, DocFile.is_deleted.is_(False)))
    can_src = user.has_role("DC") or user.dept_id == f.owner_dept_id or user.is_admin
    return {
        "data": to_dict(f) | {"status_name": FILE_STATUS.get(f.status), "level_name": LEVEL_NAMES.get(f.file_level),
                              "owner_dept_name": svc.dept_name(db, f.owner_dept_id),
                              "parent": to_dict(db.get(DocFile, f.parent_file_id)) if f.parent_file_id else None,
                              "src_att_id": f.src_att_id if can_src else None},
        "versions": [to_dict(v) for v in versions],
        "dists": [to_dict(x) | {"dept_name": svc.dept_name(db, x.dept_id)} for x in dists],
        "recalls": [to_dict(x) | {"dept_name": svc.dept_name(db, x.dept_id)} for x in recalls],
        "dcrs": [to_dict(x) | {"status_name": DCR_FLOW.states.get(x.status)} for x in dcrs],
        "children": [to_dict(x) for x in children],
    }


@router.post("/api/doc/files/import")
async def files_import(file: UploadFile = File(...), db: Session = Depends(get_db),
                       user: CurrentUser = Depends(require("doc.file.import"))):
    """上线初始化：按 SY/FM0301 一览表导入台账（历史版次不补流程，以制修订记录为准）。"""
    rows = read_rows(await file.read(), {"文件名称": "file_name", "文件编号": "file_no", "版本": "rev",
                                         "保存单位": "owner", "层级": "level", "上级文件": "parent_no",
                                         "生效日期": "effective_date", "备注": "remark"})
    depts = {d.dept_name: d.dept_id for d in db.scalars(select(flows.Dept))}
    n = 0
    for r in rows:
        if not r.get("file_no"):
            continue
        f = db.scalar(select(DocFile).where(DocFile.file_no == str(r["file_no"]).strip()))
        if f is None:
            f = DocFile(file_no=str(r["file_no"]).strip(), status="CURRENT", created_by=user.user_id)
            db.add(f)
        f.file_name, f.rev = r.get("file_name"), str(r.get("rev") or "A0")
        f.file_level = flows.infer_level(f.file_no) or {"一级": "QM", "二级": "QP", "三级": "WI", "四级": "FM"}.get(
            str(r.get("level") or "")[:2], "EXT")
        f.owner_dept_id = depts.get(str(r.get("owner") or "").strip())
        eff = r.get("effective_date")
        f.effective_date = eff.date() if hasattr(eff, "date") else (dt.date.fromisoformat(str(eff)[:10]) if eff else None)
        f.remark = r.get("remark")
        db.flush()
        if not db.scalar(select(DocVersion.id).where(DocVersion.file_id == f.id, DocVersion.rev == f.rev)):
            db.add(DocVersion(file_id=f.id, rev=f.rev, change_content="上线初始化导入", is_history=True))
        n += 1
    for r in rows:
        if r.get("parent_no"):
            f = db.scalar(select(DocFile).where(DocFile.file_no == str(r["file_no"]).strip()))
            p = db.scalar(select(DocFile).where(DocFile.file_no == str(r["parent_no"]).strip()))
            if f and p:
                f.parent_file_id = p.id
    db.commit()
    return {"ok": True, "count": n}


# ======================================================================== 会签汇总
@router.get("/api/doc/sign-summary")
def sign_summary(request: Request, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    params = dict(request.query_params)
    rows, total = list_query(db, DocSign, params, ("sign_no", "name"))
    out = []
    t = now()
    min_wait = int(params.get("wait_days") or 0)
    for s in rows:
        d = db.get(Dcr, s.dcr_id)
        lines = list(db.scalars(select(DocSignDept).where(DocSignDept.dcr_id == s.dcr_id,
                                                          DocSignDept.round == (d.sign_round if d else 1))))
        unsigned = [x for x in lines if not x.sign_at]
        wait = max([(t - x.receive_at).days for x in unsigned if x.receive_at] or [0])
        if wait < min_wait:
            continue
        out.append(to_dict(s) | {
            "file_no": d.file_no if d else None, "rev": d.new_rev if d else None, "dcr_id": s.dcr_id,
            "initiator": svc.user_name(db, d.created_by) if d else None, "start": str(d.created_at)[:10] if d else None,
            "dept_count": len(lines), "signed": len(lines) - len(unsigned),
            "unsigned_depts": "、".join(svc.dept_name(db, x.dept_id) or "" for x in unsigned), "max_wait_days": wait,
        })
    return {"items": out, "total": total}


@router.post("/api/doc/sign-summary/{sign_id}/urge")
def sign_urge(sign_id: int, db: Session = Depends(get_db), user: CurrentUser = Depends(require("doc.sign.urge"))):
    s = db.get(DocSign, sign_id)
    d = db.get(Dcr, s.dcr_id)
    rows = db.scalars(select(DocSignDept).where(DocSignDept.dcr_id == d.id, DocSignDept.round == d.sign_round,
                                                DocSignDept.sign_at.is_(None)))
    n = 0
    for r in rows:
        days = (now() - r.receive_at).days if r.receive_at else 0
        svc.send_msg(db, "doc.sign.overdue", [r.signer], {"文件编号": d.file_no, "N": days}, "dcr", d.id, force=True)
        n += 1
    db.commit()
    return {"ok": True, "count": n}


# ======================================================================== 发放 / 签收 / 回收
@router.get("/api/doc/dist-items")
def dist_items(request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
    params = dict(request.query_params)
    base = None
    if params.pop("mine", None):
        base = select(DistItem).where(DistItem.receiver == user.user_id)
    rows, total = list_query(db, DistItem, params, ("doc_no", "doc_name"), base)
    dists = {x.id: x for x in db.scalars(select(Dist).where(Dist.id.in_([r.dist_id for r in rows] or [0])))}
    return {"items": [to_dict(r) | {"dept_name": svc.dept_name(db, r.dept_id),
                                    "release_date": str(dists[r.dist_id].release_date) if r.dist_id in dists else None,
                                    "first_issue": dists[r.dist_id].first_issue if r.dist_id in dists else None}
                      for r in rows], "total": total}


@router.post("/api/doc/dist-items/ack")
def dist_ack(data: dict = Body(...), db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
    """已阅签收（支持批量）。纸质领取须手写签名。"""
    n = 0
    for iid in data.get("ids", []):
        it = db.get(DistItem, int(iid))
        if not it or it.ack_status != "PENDING":
            continue
        if it.receiver != user.user_id and not user.has_role("DC"):
            raise Forbidden(f"{it.doc_no} 不是发给您的，不能签收")
        if it.dist_mode == "PAPER":
            it.sig_id = svc.save_signature(db, user, data.get("signature"), "dist_item", it.id, "ACK")
        it.ack_status, it.ack_at, it.status = "SIGNED", now(), "SIGNED"
        svc.close_todos(db, "dist_item", it.id)
        n += 1
    db.commit()
    return {"ok": True, "count": n}


@router.get("/api/doc/recalls")
def recalls(request: Request, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    rows, total = list_query(db, Recall, dict(request.query_params), ("doc_no",))
    return {"items": [to_dict(r) | {"dept_name": svc.dept_name(db, r.dept_id)} for r in rows], "total": total}


@router.post("/api/doc/recalls/{rid}/done")
def recall_done(rid: int, data: dict = Body(...), db: Session = Depends(get_db),
                user: CurrentUser = Depends(require("doc.dist.recall"))):
    rc = db.get(Recall, rid)
    if not rc or rc.recall_status != "PENDING":
        raise BizError("该回收记录不是待回收状态")
    n = int(data.get("recall_copies", rc.old_copies or 0))
    if n < 0 or n > (rc.old_copies or 0):
        raise BizError("【回收份数】须在 0 ～ 旧版发放份数之间")
    if n < (rc.old_copies or 0) and not (data.get("diff_reason") or "").strip():
        raise BizError("【回收份数】少于发放份数，请填写差异原因")
    rc.recall_copies, rc.diff_reason = n, data.get("diff_reason")
    rc.recall_by, rc.recall_at = user.user_id, now()
    rc.recall_status = "DONE" if n == rc.old_copies else "DIFF"
    old = db.get(DistItem, rc.old_item_id)
    if old:
        old.ack_status = "RECALLED"
    svc.oper_log(db, "doc_recall", rc.id, rc.doc_no, "recall", "登记回收", "PENDING", rc.recall_status, user,
                 rc.diff_reason)
    db.flush()
    target_model, key = (DocFile, Recall.file_id) if rc.file_id else (Drawing, Recall.drawing_id)
    tid = rc.file_id or rc.drawing_id
    left = db.scalar(select(func.count()).select_from(Recall).where(key == tid, Recall.recall_status == "PENDING"))
    t = db.get(target_model, tid)
    if t is not None and hasattr(t, "recall_pending"):
        t.recall_pending = bool(left)
    db.commit()
    return to_dict(rc)


# ======================================================================== 图纸库
@router.get("/api/doc/drawings")
def drawings(request: Request, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    rows, total = list_query(db, Drawing, dict(request.query_params), ("drawing_no", "part_no", "cust_part_no"))
    out = []
    for r in rows:
        acks = list(db.scalars(select(DrawingAck).where(DrawingAck.drawing_id == r.id)))
        out.append(to_dict(r) | {"status_name": DWG_STATUS.get(r.status),
                                 "ack_progress": f"{sum(1 for a in acks if a.ack_at)}/{len(acks)}"})
    return {"items": out, "total": total}


@router.get("/api/doc/drawings/{did}")
def drawing_detail(did: int, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    dw = db.get(Drawing, did)
    if not dw:
        raise NotFound()
    acks = db.scalars(select(DrawingAck).where(DrawingAck.drawing_id == did))
    hist = db.scalars(select(Drawing).where(Drawing.part_no == dw.part_no, Drawing.drawing_type == dw.drawing_type)
                      .order_by(Drawing.id.desc()))
    return {"data": to_dict(dw) | {"status_name": DWG_STATUS.get(dw.status)},
            "acks": [to_dict(a) | {"dept_name": svc.dept_name(db, a.dept_id)} for a in acks],
            "history": [to_dict(h) | {"status_name": DWG_STATUS.get(h.status)} for h in hist]}


@router.post("/api/doc/drawings")
def drawing_upload(data: dict = Body(...), db: Session = Depends(get_db),
                   user: CurrentUser = Depends(require("doc.dwg.upload"))):
    from ..base.models import Part

    ck = Checker()
    part = db.scalar(select(Part).where(Part.part_no == data.get("part_no")))
    ck.check(part is not None, "part_no", "【顺翼料号】必须是 ERP 物料主数据中存在的料号")
    ck.check(bool(data.get("file_att_id")), "file_att_id", "【图纸文件】请上传 PDF 图纸")
    if data.get("file_att_id"):
        att = db.get(Attachment, int(data["file_att_id"]))
        ck.check(att is not None and (att.file_name or "").lower().endswith(".pdf"), "file_att_id",
                 "【图纸文件】仅支持 PDF")
    dtype = data.get("drawing_type") or "FINISHED"
    sy_rev = (data.get("sy_rev") or "").strip()
    ck.check(bool(sy_rev), "sy_rev", "【顺翼版本】必须填写")
    prev = db.scalar(select(Drawing).where(Drawing.part_no == data.get("part_no"), Drawing.drawing_type == dtype,
                                           Drawing.is_deleted.is_(False)).order_by(Drawing.id.desc()))
    if sy_rev and db.scalar(select(Drawing.id).where(Drawing.part_no == data.get("part_no"),
                                                     Drawing.drawing_type == dtype, Drawing.sy_rev == sy_rev,
                                                     Drawing.is_deleted.is_(False))):
        ck.add("sy_rev", f"【顺翼版本】{sy_rev} 已存在，同图号顺翼版本不可重复")
    if data.get("ecn_id") is None and data.get("from_ecn"):
        ck.add("ecn_id", "【关联变更单】由变更升版时必填")
    ck.raise_if_any()
    if prev and prev.cust_rev != data.get("cust_rev") and prev.sy_rev == sy_rev and not data.get("confirm_rev"):
        raise BizError("客户版本已变化而顺翼版本未变，请确认后勾选「确认版本」")
    dw = Drawing(drawing_no=data.get("drawing_no") or f"DWG-{data['part_no']}", part_no=data["part_no"],
                 cust_part_no=data.get("cust_part_no") or (part.cust_part_no if part else None),
                 cust_code=part.cust_code if part else None, drawing_type=dtype, cust_rev=data.get("cust_rev"),
                 sy_rev=sy_rev, file_att_id=int(data["file_att_id"]), ecn_id=data.get("ecn_id"),
                 status="TRIAL" if data.get("ecn_id") else data.get("status", "RELEASED"),
                 remark=data.get("remark"), created_by=user.user_id, created_name=user.user_name)
    db.add(dw)
    db.flush()
    if dw.status == "RELEASED":
        _obsolete_prev(db, dw, user)
    svc.oper_log(db, "doc_drawing", dw.id, dw.drawing_no, "upload", "上传图纸", None, dw.status, user)
    if data.get("push_dept_ids"):
        _push(db, dw, data["push_dept_ids"], user)
    db.commit()
    return to_dict(dw)


def _obsolete_prev(db, dw: Drawing, user):
    for old in db.scalars(select(Drawing).where(Drawing.part_no == dw.part_no, Drawing.drawing_type == dw.drawing_type,
                                                Drawing.id != dw.id, Drawing.status == "RELEASED")):
        old.status = "OBSOLETE"
        svc.oper_log(db, "doc_drawing", old.id, old.drawing_no, "obsolete", "新版转正式，旧版作废", "RELEASED",
                     "OBSOLETE", user)
        _create_recall_for_all(db, None, None, drawing=old)


def _push(db, dw, dept_ids, user):
    from ..sys.models import User

    dw.push_dept_ids = sorted(set((dw.push_dept_ids or []) + [int(x) for x in dept_ids]))
    receivers = []
    for dept in dept_ids:
        for u in db.scalars(select(User).where(User.dept_id == int(dept), User.enabled.is_(True))):
            if not db.scalar(select(DrawingAck.id).where(DrawingAck.drawing_id == dw.id,
                                                         DrawingAck.receiver == u.user_id)):
                db.add(DrawingAck(drawing_id=dw.id, dept_id=int(dept), receiver=u.user_id, receiver_name=u.user_name))
                receivers.append(u.user_id)
    svc.send_msg(db, "doc.dwg.push", receivers, {"料号": dw.part_no, "图号": dw.drawing_no, "版本": dw.sy_rev,
                                                  "状态": DWG_STATUS.get(dw.status)}, "doc_drawing", dw.id, force=True)


@router.post("/api/doc/drawings/{did}/push")
def drawing_push(did: int, data: dict = Body(...), db: Session = Depends(get_db),
                 user: CurrentUser = Depends(require("doc.dwg.upload"))):
    dw = db.get(Drawing, did)
    if not data.get("dept_ids"):
        raise BizError("【推送部门】请至少选择 1 个部门")
    _push(db, dw, data["dept_ids"], user)
    db.commit()
    return {"ok": True}


@router.post("/api/doc/drawings/{did}/ack")
def drawing_ack(did: int, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
    a = db.scalar(select(DrawingAck).where(DrawingAck.drawing_id == did, DrawingAck.receiver == user.user_id))
    if not a:
        raise BizError("这份图纸没有推送给您")
    a.ack_at = a.ack_at or now()
    db.commit()
    return {"ok": True}


@router.post("/api/doc/drawings/{did}/release")
def drawing_release(did: int, db: Session = Depends(get_db), user: CurrentUser = Depends(require("doc.dwg.release"))):
    """试产版 → 受控正式版（ECN 验证通过后）；同料号同类型原正式版作废并回收。"""
    dw = db.get(Drawing, did)
    if dw.status != "TRIAL":
        raise BizError("只有试产版可以转正式版")
    dw.status = "RELEASED"
    _obsolete_prev(db, dw, user)
    svc.oper_log(db, "doc_drawing", dw.id, dw.drawing_no, "release", "转正式版", "TRIAL", "RELEASED", user)
    from ..core import events

    events.emit("doc.drawing.released", db, drawing=dw)
    db.commit()
    return to_dict(dw)


@router.post("/api/doc/drawings/{did}/obsolete")
def drawing_obsolete(did: int, data: dict = Body(default={}), db: Session = Depends(get_db),
                     user: CurrentUser = Depends(require("doc.dwg.release"))):
    dw = db.get(Drawing, did)
    old = dw.status
    dw.status = "OBSOLETE"
    _create_recall_for_all(db, None, None, drawing=dw)
    svc.oper_log(db, "doc_drawing", dw.id, dw.drawing_no, "obsolete", "作废", old, "OBSOLETE", user,
                 data.get("opinion"))
    db.commit()
    return to_dict(dw)


@router.put("/api/doc/drawings/{did}")
def drawing_update(did: int, data: dict = Body(...), db: Session = Depends(get_db),
                   user: CurrentUser = Depends(require("doc.dwg.release"))):
    dw = db.get(Drawing, did)
    if "download_allowed" in data:
        dw.download_allowed = bool(data["download_allowed"])
    if "remark" in data:
        dw.remark = data["remark"]
    svc.oper_log(db, "doc_drawing", dw.id, dw.drawing_no, "edit", "修改", dw.status, dw.status, user)
    db.commit()
    return to_dict(dw)


# ======================================================================== 借阅 / 补发统计
@router.get("/api/doc/borrow-stats")
def borrow_stats(month: str = "", db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    q = select(Borrow).where(Borrow.is_deleted.is_(False), Borrow.status.in_(["LENT", "RETURNED"]))
    rq = select(Reissue).where(Reissue.is_deleted.is_(False), Reissue.status == "DONE")
    rows, rrows = list(db.scalars(q)), list(db.scalars(rq))
    if month:
        rows = [b for b in rows if b.borrow_date and b.borrow_date.strftime("%Y-%m") == month]
        rrows = [r for r in rrows if r.created_at and r.created_at.strftime("%Y-%m") == month]
    by_dept: dict = {}
    for b in rows:
        k = svc.dept_name(db, b.dept_id) or "—"
        by_dept.setdefault(k, {"dept": k, "borrow": 0, "reissue": 0})["borrow"] += 1
    for r in rrows:
        k = svc.dept_name(db, r.dept_id) or "—"
        by_dept.setdefault(k, {"dept": k, "borrow": 0, "reissue": 0})["reissue"] += r.copies or 0
    today = dt.date.today()
    overdue = [to_dict(b) | {"borrower_name": svc.user_name(db, b.borrower), "dept_name": svc.dept_name(db, b.dept_id),
                             "overdue_days": (today - b.plan_return_date).days,
                             "files": "、".join(i.file_no for i in db.scalars(
                                 select(BorrowItem).where(BorrowItem.borrow_id == b.id)))}
               for b in rows if b.status == "LENT" and b.plan_return_date and b.plan_return_date < today]
    return {"by_dept": list(by_dept.values()), "overdue": overdue}


# ======================================================================== 检索
@router.get("/api/doc/search")
def search(q: str, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
    """按编号、名称、制修订内容检索现行文件。AI 条款问答（D-05-1，P2）接入后返回段落与出处。"""
    like = f"%{q}%"
    files_ = db.scalars(_visible(select(DocFile), user).where(
        DocFile.is_deleted.is_(False), or_(DocFile.file_no.like(like), DocFile.file_name.like(like))).limit(50))
    vers = db.execute(select(DocVersion, DocFile).join(DocFile, DocFile.id == DocVersion.file_id)
                      .where(DocVersion.change_content.like(like)).limit(50)).all()
    return {"files": [to_dict(f) | {"level_name": LEVEL_NAMES.get(f.file_level)} for f in files_],
            "versions": [to_dict(v) | {"file_no": f.file_no, "file_name": f.file_name} for v, f in vers]}


_ = (OperLog, make_dist)
