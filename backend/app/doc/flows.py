"""文档管理流程：更改申请（含会签）、借阅、补发（5.1.6、5.1.9）。"""
import datetime as dt
import re

from sqlalchemy import func, select

from ..core import events
from ..core.errors import BizError, Checker
from ..core.flow import Act, ChildDef, FlowDef, register
from ..core.model import now
from ..core.numbering import next_no
from ..core.pdf import watermark
from ..sys import services as svc
from ..sys.models import Attachment, Dept, User
from .models import (Borrow, BorrowItem, Dcr, Dist, DistItem, DocFile, DocSign, DocSignDept, DocVersion, Drawing,
                     Recall, Reissue)

LEVEL_NAMES = {"QM": "一级手册", "QP": "二级程序", "WI": "三级作业指导书", "FM": "四级表单", "EXT": "外部文件",
               "DWG": "图纸"}
INTERNAL_NO = re.compile(r"^SY/(QM|QP|WI|FM)\d{2,4}$")


def next_rev(rev: str | None) -> str:
    if not rev:
        return "A0"
    m = re.match(r"^([A-Z])(\d{1,2})$", rev)
    if not m:
        return rev + "-1"
    return f"{m.group(1)}{int(m.group(2)) + 1}"


def infer_level(file_no: str) -> str | None:
    m = INTERNAL_NO.match(file_no or "")
    return m.group(1) if m else None


def _mgr(db, dept_id):
    return [svc.dept_manager(db, dept_id)] if dept_id else []


def approver_for(db, level: str, owner_dept_id) -> list[str]:
    rule = svc.cfg(db, f"doc.approver.{level}", None, str) or ("ROLE:GM" if level in ("QM", "QP") else "DEPT_MGR")
    if rule.startswith("ROLE:"):
        return svc.users_by_role(db, rule[5:])
    return _mgr(db, owner_dept_id)


def default_scope(db, level: str) -> list[int]:
    if level in ("QM", "QP"):
        return [d.dept_id for d in db.scalars(select(Dept).where(Dept.parent_id != 0))] or []
    return []


# ======================================================================== 更改申请 DCR
def _dcr_submit_guard(ctx):
    db, d = ctx.db, ctx.doc
    ck = Checker()
    ck.check(d.apply_type in ("NEW", "REVISE", "OBSOLETE"), "apply_type", "【申请类型】请选择新增 / 修订 / 作废")
    ck.check(len((d.reason or "").strip()) >= 10, "reason", "【变更原因】请说明为什么改，至少 10 个字")
    ck.check(bool((d.after_text or "").strip()), "after_text", "【更改后】必须填写")
    if d.apply_type in ("REVISE", "OBSOLETE"):
        ck.check(bool((d.before_text or "").strip()), "before_text", "【更改前】修订、作废必须填写原条款")
        f = db.get(DocFile, d.file_id) if d.file_id else None
        if not f:
            ck.add("file_id", "【文件】修订、作废须选择台账中的文件")
        elif f.status != "CURRENT" and f.cur_dcr_id != d.id:
            other = db.get(Dcr, f.cur_dcr_id) if f.cur_dcr_id else None
            ck.add("file_id", f"【文件】{f.file_no} 正在修订中（{other.dcr_no if other else '—'}），不能重复申请")
    else:
        if not d.file_no:
            ck.add("file_no", "【文件编号】新增文件须填写编号")
        else:
            lvl = d.file_level or infer_level(d.file_no)
            if lvl in ("QM", "QP", "WI", "FM") and not INTERNAL_NO.match(d.file_no):
                ck.add("file_no", "【文件编号】内部文件须为 SY/QMxx、SY/QPxx、SY/WIxxxx、SY/FMxxxx 格式")
            if db.scalar(select(DocFile.id).where(DocFile.file_no == d.file_no, DocFile.is_deleted.is_(False),
                                                  DocFile.cur_dcr_id.is_distinct_from(d.id))):
                ck.add("file_no", f"【文件编号】{d.file_no} 已存在，请检查是否应走修订")
        ck.check(bool(d.file_name), "file_name", "【文件名称】必须填写")
        if (d.file_level or infer_level(d.file_no or "")) in ("WI", "FM"):
            ck.check(bool(d.parent_file_id), "parent_file_id", "【上级文件】三级、四级文件须指定所属程序文件")
    if d.apply_type in ("NEW", "REVISE"):
        has = db.scalar(select(func.count()).select_from(Attachment).where(
            Attachment.biz_type == "dcr", Attachment.biz_id == d.id, Attachment.field_code == "draft_att"))
        ck.check(bool(has), "draft_att", "【修订稿】新增、修订须上传修订稿")
    ck.raise_if_any()


def _dcr_submit_effect(ctx):
    db, d = ctx.db, ctx.doc
    if d.apply_type == "NEW":
        lvl = d.file_level or infer_level(d.file_no) or "EXT"
        d.file_level = lvl
        f = db.scalar(select(DocFile).where(DocFile.file_no == d.file_no))
        if f is None:
            f = DocFile(file_no=d.file_no, file_name=d.file_name, file_level=lvl, parent_file_id=d.parent_file_id,
                        rev=None, owner_dept_id=d.owner_dept_id or d.apply_dept_id, status="NEW",
                        created_by=ctx.user.user_id, dist_scope=d.dist_scope or default_scope(db, lvl))
            db.add(f)
            db.flush()
        d.file_id, d.cur_rev, d.new_rev = f.id, None, "A0"
        f.cur_dcr_id = d.id
    else:
        f = db.get(DocFile, d.file_id)
        d.file_no, d.file_name, d.file_level, d.cur_rev = f.file_no, f.file_name, f.file_level, f.rev
        d.owner_dept_id = d.owner_dept_id or f.owner_dept_id
        d.new_rev = next_rev(f.rev) if d.apply_type == "REVISE" else None
        d.dist_scope = d.dist_scope or f.dist_scope
        f.status, f.cur_dcr_id = "REVISING", d.id
    d.applicant = d.applicant or ctx.user.user_id
    d.apply_dept_id = d.apply_dept_id or ctx.user.dept_id


def _dept_agree(ctx):
    ctx.doc.dept_opinion = ctx.opinion or "同意"
    ctx.doc.dept_sig_id = ctx.extra.get("sig_id")


def _dc_pass_guard(ctx):
    ids = ctx.p("sign_dept_ids") or ctx.doc.sign_dept_ids
    if not ids:
        raise BizError("【会签部门】请至少指定 1 个会签部门")


def _dc_pass_effect(ctx):
    db, d = ctx.db, ctx.doc
    if ctx.p("sign_dept_ids"):
        d.sign_dept_ids = [int(x) for x in ctx.p("sign_dept_ids")]
    if ctx.p("dist_scope"):
        d.dist_scope = [int(x) for x in ctx.p("dist_scope")]
    d.sign_round = (d.sign_round or 0) + 1
    sign = db.scalar(select(DocSign).where(DocSign.dcr_id == d.id))
    if sign is None:
        sign = DocSign(sign_no=next_no(db, "doc_sign"), dcr_id=d.id, category=d.file_level,
                       name=f"{d.file_name} {d.new_rev or ''}", status="SIGNING", created_by=ctx.user.user_id)
        db.add(sign)
        db.flush()
    sign.status = "SIGNING"
    prev = {r.dept_id: r for r in db.scalars(select(DocSignDept).where(
        DocSignDept.dcr_id == d.id, DocSignDept.round == d.sign_round - 1))}
    for dept_id in d.sign_dept_ids:
        old = prev.get(dept_id)
        signer = svc.dept_manager(db, dept_id)
        row = DocSignDept(sign_id=sign.id, dcr_id=d.id, round=d.sign_round, dept_id=dept_id, receive_at=now(),
                          signer=signer, signer_name=svc.user_name(db, signer))
        # 退回后重新会签：上一轮同意且内容未改的部门保留结论（5.4.5 规则 1 同口径）
        if old and old.opinion_result == "AGREE" and not ctx.p("resign_all"):
            row.opinion_result, row.opinion, row.sign_at, row.sig_id = "AGREE", old.opinion, old.sign_at, old.sig_id
            row.signer, row.signer_name = old.signer, old.signer_name
        db.add(row)


def _sign_rows(db, d, unsigned=False):
    q = select(DocSignDept).where(DocSignDept.dcr_id == d.id, DocSignDept.round == d.sign_round)
    if unsigned:
        q = q.where(DocSignDept.sign_at.is_(None))
    return list(db.scalars(q))


def _my_sign_row(ctx):
    rows = [r for r in _sign_rows(ctx.db, ctx.doc, True) if r.signer == ctx.user.user_id]
    if not rows and ctx.user.is_admin:
        rows = _sign_rows(ctx.db, ctx.doc, True)[:1]
    if not rows:
        raise BizError("您没有待会签的部门行")
    return rows[0]


def _sign_effect(ctx):
    row = _my_sign_row(ctx)
    res = ctx.p("opinion_result", "AGREE")
    if res not in ("AGREE", "AGREE_WITH", "DISAGREE"):
        raise BizError("【会签结论】请选择同意 / 有建议 / 不同意")
    op = ctx.opinion or ("同意" if res == "AGREE" else "")
    if res != "AGREE" and len(op.strip()) < 5:
        raise BizError("【会签意见】选择有建议或不同意时，意见至少 5 个字")
    row.opinion_result, row.opinion, row.sign_at, row.sig_id = res, op, now(), ctx.extra.get("sig_id")
    ctx.db.flush()
    remaining = _sign_rows(ctx.db, ctx.doc, True)
    if remaining:
        ctx.to_state = "SIGNING"
        return
    rows = _sign_rows(ctx.db, ctx.doc)
    sign = ctx.db.scalar(select(DocSign).where(DocSign.dcr_id == ctx.doc.id))
    if any(r.opinion_result == "DISAGREE" for r in rows):
        ctx.to_state = "DRAFT"
        sign.status = "RETURNED"
        svc.send_msg(ctx.db, "flow.returned", [ctx.doc.created_by], {
            "单据": "文件更改", "单号": ctx.doc.dcr_no, "处理人": "会签部门", "节点名": "草稿",
            "原因": "；".join(f"{svc.dept_name(ctx.db, r.dept_id)}：{r.opinion}" for r in rows
                             if r.opinion_result == "DISAGREE")}, "dcr", ctx.doc.id, force=True)
    else:
        ctx.to_state = "CONFIRMING"
        sign.status = "DONE"


def _sign_transfer(ctx):
    row = _my_sign_row(ctx)
    to = ctx.p("to_user")
    if not to:
        raise BizError("【转交给】请选择本部门的会签人")
    if svc.user_dept(ctx.db, to) != row.dept_id:
        raise BizError("【转交给】只能转交给本部门人员")
    row.transfer_note = f"{ctx.user.user_name} 转交 {svc.user_name(ctx.db, to)} {now():%m-%d %H:%M}"
    row.signer, row.signer_name = to, svc.user_name(ctx.db, to)
    ctx.to_state = "SIGNING"


def _confirm_guard(ctx):
    adopts = {int(a["id"]): a for a in (ctx.p("adopts") or [])}
    ck = Checker()
    for r in _sign_rows(ctx.db, ctx.doc):
        if r.opinion_result == "AGREE":
            continue
        a = adopts.get(r.id) or {"adopt": r.adopt, "adopt_note": r.adopt_note}
        dn = svc.dept_name(ctx.db, r.dept_id)
        if a.get("adopt") not in ("ADOPT", "REJECT"):
            ck.add(f"adopt_{r.id}", f"【采纳】请对 {dn} 的意见选择采纳 / 不采纳")
        elif a.get("adopt") == "REJECT" and not (a.get("adopt_note") or "").strip():
            ck.add(f"adopt_{r.id}", f"【不采纳说明】{dn} 的意见不采纳时须写明理由")
    ck.raise_if_any()


def _confirm_effect(ctx):
    adopts = {int(a["id"]): a for a in (ctx.p("adopts") or [])}
    for r in _sign_rows(ctx.db, ctx.doc):
        if r.id in adopts:
            r.adopt, r.adopt_note = adopts[r.id].get("adopt"), adopts[r.id].get("adopt_note")
        r.confirm_date = dt.date.today()


def _audit_agree(ctx):
    ctx.doc.audit_opinion, ctx.doc.audit_sig_id, ctx.doc.auditor = ctx.opinion or "同意", ctx.extra.get("sig_id"), \
        ctx.user.user_id


def _approve_agree(ctx):
    ctx.doc.approve_opinion, ctx.doc.approve_sig_id, ctx.doc.approver = ctx.opinion or "同意", \
        ctx.extra.get("sig_id"), ctx.user.user_id


def _release_guard(ctx):
    d = ctx.doc
    eff = ctx.p("effective_date") or d.effective_date
    imp = ctx.p("implement_date") or d.implement_date or eff
    ck = Checker()
    ck.check(bool(eff), "effective_date", "【生效日期】发行前必须填写")
    if eff and imp and str(imp) < str(eff):
        ck.add("implement_date", "【实施日期】不能早于生效日期，请重新选择")
    if d.apply_type != "OBSOLETE" and not (ctx.p("dist_scope") or d.dist_scope):
        ck.add("dist_scope", "【发放范围】至少选择 1 个部门")
    ck.raise_if_any()


def _att_bytes(db, att_id) -> bytes | None:
    from ..sys.api import _upload_root

    a = db.get(Attachment, att_id) if att_id else None
    if not a:
        return None
    p = _upload_root() / a.storage_path
    return p.read_bytes() if p.exists() else None


def release_dcr(ctx):
    """发行：台账升版、写制修订记录、生成受控 PDF、按发放范围生成发放单与旧版回收任务。"""
    from ..sys.api import save_bytes

    db, d, user = ctx.db, ctx.doc, ctx.user
    d.effective_date = d.effective_date or _date(ctx.p("effective_date"))
    d.implement_date = _date(ctx.p("implement_date")) or d.implement_date or d.effective_date
    if ctx.p("dist_scope"):
        d.dist_scope = [int(x) for x in ctx.p("dist_scope")]
    f = db.get(DocFile, d.file_id)
    old_rev = f.rev
    today = dt.date.today()
    if d.apply_type == "OBSOLETE":
        f.status, f.cur_dcr_id = "OBSOLETE", None
        if f.pdf_att_id:
            pdf = watermark(_att_bytes(db, f.pdf_att_id), f"作废 · {f.file_no} · {f.rev}", f.file_name, obsolete=True)
            f.pdf_att_id = save_bytes(db, pdf, f"{f.file_no.replace('/', '_')}-{f.rev}-作废.pdf", "application/pdf",
                                      "doc_file", f.id, "pdf_obsolete").id
        _create_recall_for_all(db, f, None)
        events.emit("doc.released", db, dcr=d, file=f)
        return
    new_rev = d.new_rev or next_rev(old_rev)
    draft = db.scalar(select(Attachment).where(Attachment.biz_type == "dcr", Attachment.biz_id == d.id,
                                               Attachment.field_code == "draft_att").order_by(Attachment.id.desc()))
    raw = _att_bytes(db, draft.id) if draft else None
    pdf = watermark(raw, f"受控文件 · {f.file_no} · {new_rev}", f"{f.file_name}（{f.file_no} {new_rev}）")
    pdf_att = save_bytes(db, pdf, f"{f.file_no.replace('/', '_')}-{new_rev}-受控.pdf", "application/pdf",
                         "doc_file", f.id, "pdf")
    if draft and not (draft.mime or "").endswith("pdf"):
        f.src_att_id = draft.id
    f.rev, f.status, f.cur_dcr_id, f.pdf_att_id = new_rev, "CURRENT", None, pdf_att.id
    f.effective_date, f.implement_date = d.effective_date, d.implement_date
    f.preparer, f.auditor, f.approver = d.applicant, d.auditor, d.approver
    f.dist_scope = d.dist_scope or f.dist_scope
    if d.parent_file_id:
        f.parent_file_id = d.parent_file_id
    db.add(DocVersion(file_id=f.id, rev=new_rev, change_content=f"{d.after_text}\n（原因：{d.reason}）"[:2000],
                      reviser=svc.user_name(db, d.applicant), revise_date=today,
                      auditor=svc.user_name(db, d.auditor), audit_date=today,
                      approver=svc.user_name(db, d.approver), approve_date=today, dcr_id=d.id, pdf_att_id=pdf_att.id))
    first = old_rev is None
    dist = make_dist(db, user, f.file_level, file=f, rev=new_rev, scope=f.dist_scope or [], first=first, dcr_id=d.id)
    if not first:
        _create_recall_for_all(db, f, dist, exclude_rev=new_rev)
    events.emit("doc.released", db, dcr=d, file=f)


def _date(v):
    if not v:
        return None
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v)[:10])


def make_dist(db, user, category, file=None, drawing=None, rev=None, scope=(), first=True, dcr_id=None,
              paper_depts=(), reissue_id=None, items=None):
    """生成发放单。默认：电子档发放；图面电子档发物控、品质、采购，纸质档发生产（5.1.6 规则 5）。"""
    no = file.file_no if file else drawing.drawing_no
    name = file.file_name if file else f"{drawing.part_no} 图纸"
    dist = Dist(dist_no=next_no(db, "dist"), dist_category=category, file_id=file.id if file else None,
                drawing_id=drawing.id if drawing else None, dcr_id=dcr_id, reissue_id=reissue_id, doc_no=no,
                doc_name=name, rev=rev, release_date=dt.date.today(), first_issue=first, status="ISSUED",
                created_by=user.user_id if user else "system")
    db.add(dist)
    db.flush()
    seq = db.scalar(select(func.count()).select_from(DistItem).where(
        DistItem.doc_no == no, DistItem.rev == rev, DistItem.dist_mode == "PAPER")) or 0
    rows = items or [{"dept_id": dpt, "mode": "PAPER" if dpt in paper_depts else "ELEC", "copies": 1} for dpt in scope]
    receivers = []
    for r in rows:
        mode = r.get("mode", "ELEC")
        receiver = r.get("receiver") or svc.dept_manager(db, r["dept_id"])
        item = DistItem(dist_id=dist.id, file_id=dist.file_id, drawing_id=dist.drawing_id, doc_no=no, doc_name=name,
                        rev=rev, dept_id=r["dept_id"], receiver=receiver, receiver_name=svc.user_name(db, receiver),
                        dist_mode=mode, copies=1 if mode == "ELEC" else int(r.get("copies") or 1),
                        ack_status="PENDING", status="PENDING")
        if mode == "PAPER":
            seq += 1
            tail = no.split("/")[-1]
            item.copy_no = f"{tail}-{rev}-{seq:02d}"
        db.add(item)
        receivers.append(receiver)
    svc.send_msg(db, "doc.released", receivers, {"文件编号": no, "文件名称": name, "版本": rev,
                                                  "生效日期": str(file.effective_date if file else dt.date.today())},
                 "doc_file" if file else "doc_drawing", file.id if file else drawing.id, force=True)
    return dist


def _create_recall_for_all(db, f: DocFile | None, dist: Dist | None, exclude_rev=None, drawing: Drawing | None = None):
    q = select(DistItem).where(DistItem.ack_status != "RECALLED")
    q = q.where(DistItem.file_id == f.id) if f else q.where(DistItem.drawing_id == drawing.id)
    if exclude_rev:
        q = q.where(DistItem.rev != exclude_rev)
    pending = False
    paper_depts = {}
    for it in db.scalars(q):
        rc = Recall(dist_id=dist.id if dist else it.dist_id, old_item_id=it.id, file_id=it.file_id,
                    drawing_id=it.drawing_id, doc_no=it.doc_no, old_rev=it.rev, dept_id=it.dept_id,
                    dist_mode=it.dist_mode, old_copies=it.copies, copy_no=it.copy_no)
        if it.dist_mode == "ELEC":
            # 电子档旧版由系统自动收回
            rc.recall_copies, rc.recall_by, rc.recall_at, rc.recall_status = it.copies, "system", now(), "DONE"
            it.ack_status = "RECALLED"
        else:
            rc.recall_status = "PENDING"
            pending = True
            paper_depts.setdefault(it.dept_id, []).append(it)
        db.add(rc)
    target = f or drawing
    if target is not None and hasattr(target, "recall_pending"):
        target.recall_pending = pending
    for dept_id, its in paper_depts.items():
        svc.send_msg(db, "doc.recall", [svc.dept_manager(db, dept_id)], {
            "文件编号": its[0].doc_no, "旧版本": its[0].rev, "份数": sum(x.copies or 1 for x in its),
            "分发号": "、".join(x.copy_no or "" for x in its)}, "doc_file" if f else "doc_drawing",
            target.id if target else None, force=True)


def _withdraw(ctx):
    f = ctx.db.get(DocFile, ctx.doc.file_id) if ctx.doc.file_id else None
    if f and f.cur_dcr_id == ctx.doc.id:
        f.cur_dcr_id = None
        if f.status == "REVISING":
            f.status = "CURRENT"
        elif f.status == "NEW":
            f.is_deleted = True


DCR_STATES = {
    "DRAFT": "草稿", "DEPT_REVIEW": "部门意见", "DC_REVIEW": "文控审核", "SIGNING": "会签中", "CONFIRMING": "意见签认",
    "AUDITING": "审核", "APPROVING": "核准", "RELEASING": "待发行", "RELEASED": "已发行", "CANCELLED": "已取消",
}


def _submit_guard(ctx):
    _dcr_submit_guard(ctx)


def _submit_effect(ctx):
    f = ctx.db.get(DocFile, ctx.doc.file_id) if ctx.doc.file_id else None
    if f is None or f.cur_dcr_id != ctx.doc.id:
        _dcr_submit_effect(ctx)


DCR_FLOW = register(FlowDef(
    biz_type="dcr", name="文件更改", model=Dcr, no_field="dcr_no", states=DCR_STATES, perm_prefix="doc.dcr",
    nodes=["DRAFT", "DEPT_REVIEW", "DC_REVIEW", "SIGNING", "CONFIRMING", "AUDITING", "APPROVING", "RELEASING",
           "RELEASED"],
    terminal={"RELEASED", "CANCELLED"},
    summary=lambda d: f"{d.file_no or ''} {d.file_name or ''}",
    handlers={
        "DRAFT": lambda db, d: [d.created_by],
        "DEPT_REVIEW": lambda db, d: _mgr(db, d.apply_dept_id),
        "DC_REVIEW": lambda db, d: svc.users_by_role(db, "DC"),
        "SIGNING": lambda db, d: [r.signer for r in _sign_rows(db, d, True)],
        "CONFIRMING": lambda db, d: [d.applicant or d.created_by],
        "AUDITING": lambda db, d: _mgr(db, d.owner_dept_id),
        "APPROVING": lambda db, d: approver_for(db, d.file_level, d.owner_dept_id),
        "RELEASING": lambda db, d: svc.users_by_role(db, "DC"),
    },
    due_hours={"DEPT_REVIEW": 24, "DC_REVIEW": 24, "SIGNING": 72, "CONFIRMING": 24, "AUDITING": 24,
               "APPROVING": 24},
    editable={
        "DRAFT": ["apply_type", "file_id", "file_no", "file_name", "file_level", "parent_file_id", "owner_dept_id",
                  "before_text", "after_text", "reason", "ecn_id", "nc_id"],
        "DC_REVIEW": ["sign_dept_ids", "file_level", "parent_file_id", "dist_scope"],
        "RELEASING": ["effective_date", "implement_date", "dist_scope"],
    },
    acts=[
        Act("submit", "提交", ("DRAFT",), "DEPT_REVIEW", by="creator", guard=_submit_guard, effect=_submit_effect),
        Act("dept_agree", "同意", ("DEPT_REVIEW",), "DC_REVIEW", sign=True, effect=_dept_agree),
        Act("dept_return", "退回申请人", ("DEPT_REVIEW",), "DRAFT", opinion="required", style="warning"),
        Act("dc_pass", "通过并发起会签", ("DC_REVIEW",), "SIGNING", roles=("DC",), guard=_dc_pass_guard,
            effect=_dc_pass_effect,
            fields=[{"name": "sign_dept_ids", "label": "会签部门", "type": "depts", "required": True},
                    {"name": "dist_scope", "label": "发放范围", "type": "depts"}]),
        Act("dc_return", "退回申请人", ("DC_REVIEW",), "DRAFT", roles=("DC",), opinion="required", style="warning"),
        Act("sign", "会签", ("SIGNING",), None, kind="partial", sign=True, effect=_sign_effect,
            fields=[{"name": "opinion_result", "label": "会签结论", "type": "select", "required": True,
                     "default": "AGREE", "options": [{"value": "AGREE", "label": "同意"},
                                                     {"value": "AGREE_WITH", "label": "有建议"},
                                                     {"value": "DISAGREE", "label": "不同意"}]}]),
        Act("sign_transfer", "转交本部门他人", ("SIGNING",), None, kind="partial", effect=_sign_transfer,
            style="default", fields=[{"name": "to_user", "label": "转交给", "type": "user", "required": True}]),
        Act("confirm", "提交签认", ("CONFIRMING",), "AUDITING", guard=_confirm_guard, effect=_confirm_effect),
        Act("audit_agree", "审核同意", ("AUDITING",), "APPROVING", sign=True, effect=_audit_agree),
        Act("audit_return", "退回", ("AUDITING",), "DRAFT", opinion="required", style="warning"),
        Act("approve_agree", "核准", ("APPROVING",), "RELEASING", sign=True, effect=_approve_agree),
        Act("approve_return", "退回", ("APPROVING",), "DRAFT", opinion="required", style="warning"),
        Act("release", "发行", ("RELEASING",), "RELEASED", roles=("DC",), guard=_release_guard, effect=release_dcr,
            fields=[{"name": "effective_date", "label": "生效日期", "type": "date", "required": True},
                    {"name": "implement_date", "label": "实施日期", "type": "date"}],
            confirm="发行后旧版自动作废，并按发放范围推送签收，确认发行？"),
        Act("withdraw", "撤回作废", ("DRAFT", "DEPT_REVIEW"), "CANCELLED", by="creator", effect=_withdraw,
            style="danger", opinion="required"),
    ],
))


# ======================================================================== 借阅
def _borrow_guard(ctx):
    d = ctx.doc
    ck = Checker()
    items = ctx.db.scalars(select(BorrowItem).where(BorrowItem.borrow_id == d.id)).all()
    ck.check(bool(items), "items", "【文件】请至少选择 1 份文件")
    ck.check(bool(d.plan_return_date and d.borrow_date), "plan_return_date", "【借阅 / 归还日期】必须填写")
    ck.check(len((d.purpose or "").strip()) > 0, "purpose", "【借阅用途】必须填写")
    if d.plan_return_date and d.borrow_date:
        if d.plan_return_date < d.borrow_date:
            ck.add("plan_return_date", "【计划归还日期】不能早于借阅日期")
    ck.raise_if_any()


def _borrow_approve(ctx):
    d = ctx.doc
    if (d.plan_return_date - d.borrow_date).days > 30 and not ctx.user.has_role("DC"):
        raise BizError("借期超过 30 天须文控审批")
    for it in ctx.db.scalars(select(BorrowItem).where(BorrowItem.borrow_id == d.id)):
        f = ctx.db.get(DocFile, it.file_id)
        it.file_no, it.file_name, it.rev = f.file_no, f.file_name, f.rev


def _borrow_return(ctx):
    ctx.doc.return_date = _date(ctx.p("return_date")) or dt.date.today()
    ctx.doc.overdue = False


BORROW_FLOW = register(FlowDef(
    biz_type="borrow", name="文件借阅", model=Borrow, no_field="borrow_no", perm_prefix="doc.borrow",
    states={"DRAFT": "草稿", "APPLYING": "待文控审批", "LENT": "借出中", "RETURNED": "已归还", "REJECTED": "已驳回"},
    nodes=["DRAFT", "APPLYING", "LENT", "RETURNED"], terminal={"RETURNED", "REJECTED"},
    handlers={"APPLYING": lambda db, d: svc.users_by_role(db, "DC"),
              "LENT": lambda db, d: svc.users_by_role(db, "DC") if d.borrow_mode == "PAPER" else []},
    due_hours={"APPLYING": 24},
    editable={"DRAFT": ["borrow_mode", "borrow_date", "plan_return_date", "purpose", "child:items"]},
    children={"items": ChildDef(BorrowItem, "borrow_id")},
    acts=[
        Act("submit", "提交", ("DRAFT",), "APPLYING", by="creator", guard=_borrow_guard),
        Act("approve", "批准借阅", ("APPLYING",), "LENT", roles=("DC",), effect=_borrow_approve),
        Act("reject", "驳回", ("APPLYING",), "REJECTED", roles=("DC",), opinion="required", style="danger"),
        Act("return_confirm", "归还确认", ("LENT",), "RETURNED", roles=("DC",), effect=_borrow_return,
            fields=[{"name": "return_date", "label": "归还日期", "type": "date"}]),
        Act("renew", "续借", ("LENT",), "APPLYING", by="creator", style="default",
            effect=lambda ctx: setattr(ctx.doc, "plan_return_date", _date(ctx.p("plan_return_date"))),
            fields=[{"name": "plan_return_date", "label": "新的计划归还日期", "type": "date", "required": True}]),
    ],
))
BORROW_FLOW.summary = lambda d: f"{d.borrow_mode == 'PAPER' and '纸质' or '电子'}借阅"


# ======================================================================== 补发
def _reissue_guard(ctx):
    d = ctx.doc
    ck = Checker()
    if d.category == "DRAWING":
        ck.check(bool(d.drawing_id), "drawing_id", "【图面】类别为图面时须选择图纸")
    elif d.category == "FILE":
        f = ctx.db.get(DocFile, d.file_id) if d.file_id else None
        if not f:
            ck.add("file_id", "【文件】类别为文件时须选择文件")
        elif f.status not in ("CURRENT", "REVISING"):
            ck.add("file_id", "【文件】只能补发现行版文件")
    ck.check(1 <= (d.copies or 0) <= 20, "copies", "【申请张数】须在 1～20 之间")
    ck.check(d.need_date is not None and d.need_date >= dt.date.today(), "need_date", "【需求时间】不能早于今天")
    ck.check(bool((d.reason or "").strip()), "reason", "【申请理由】必须填写；遗失、破损须说明原因")
    ck.raise_if_any()


def _reissue_dc_pass(ctx):
    """文控审批通过后自动生成纸质发放行（分发号新增）。"""
    d = ctx.doc
    if d.category == "OTHER":
        return
    f = ctx.db.get(DocFile, d.file_id) if d.file_id else None
    dw = ctx.db.get(Drawing, d.drawing_id) if d.drawing_id else None
    rev = f.rev if f else (dw.sy_rev if dw else None)
    dist = make_dist(ctx.db, ctx.user, "FILE" if f else "DWG", file=f, drawing=dw, rev=rev, first=False,
                     reissue_id=d.id, items=[{"dept_id": d.dept_id, "mode": "PAPER", "copies": d.copies,
                                              "receiver": d.created_by}])
    it = ctx.db.scalar(select(DistItem).where(DistItem.dist_id == dist.id))
    d.dist_item_id = it.id if it else None


REISSUE_FLOW = register(FlowDef(
    biz_type="reissue", name="补发申请", model=Reissue, no_field="reissue_no", perm_prefix="doc.reissue",
    states={"DRAFT": "草稿", "DEPT_MGR": "申请单位主管", "DC": "文控中心", "RESP": "责任单位", "DONE": "已完成",
            "REJECTED": "已驳回"},
    nodes=["DRAFT", "DEPT_MGR", "DC", "RESP", "DONE"], terminal={"DONE", "REJECTED"},
    summary=lambda d: f"{d.file_no or d.part_no or ''} {d.copies or ''} 份",
    handlers={"DEPT_MGR": lambda db, d: _mgr(db, d.dept_id), "DC": lambda db, d: svc.users_by_role(db, "DC"),
              "RESP": lambda db, d: _mgr(db, d.resp_dept_id) or svc.users_by_role(db, "DC")},
    due_hours={"DEPT_MGR": 24, "DC": 24, "RESP": 24},
    editable={"DRAFT": ["category", "file_id", "drawing_id", "model", "part_no", "part_name", "file_no", "file_rev",
                        "file_name", "other_desc", "copies", "paper_size", "need_date", "reason", "resp_dept_id"]},
    acts=[
        Act("submit", "提交", ("DRAFT",), "DEPT_MGR", by="creator", guard=_reissue_guard,
            effect=lambda ctx: setattr(ctx.doc, "dept_id", ctx.doc.dept_id or ctx.user.dept_id)),
        Act("mgr_agree", "同意", ("DEPT_MGR",), "DC", sign=True),
        Act("dc_agree", "同意并补发", ("DC",), "RESP", roles=("DC",), sign=True, effect=_reissue_dc_pass),
        Act("resp_agree", "确认", ("RESP",), "DONE", sign=True),
        Act("return", "退回", ("DEPT_MGR", "DC", "RESP"), None, kind="return", opinion="required", style="warning",
            return_to=("DRAFT", "DEPT_MGR", "DC")),
        Act("reject", "驳回", ("DEPT_MGR", "DC"), "REJECTED", opinion="required", style="danger"),
    ],
))

_ = (User,)
