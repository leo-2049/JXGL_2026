"""通用单据流程引擎。

每类单据声明一个 FlowDef：状态、动作（流转表）、各节点处理人解析、各节点可编辑字段、时限。
引擎负责：
- 只执行流转表中声明的动作（「不在流转表中的动作，系统一律拒绝」5.0 约定 8）；
- 权限：当前节点待办处理人 / 动作声明的角色 / 起草人；
- 校验：动作 guard 抛 ValidationFailed，列出全部问题并定位到字段；
- 退回：可退回到声明范围内且「已经过」的任一节点，必填原因；
- 并行（会签 / 评估）：partial 动作只关闭本人待办，全部完成后才推进；
- 留痕：每次状态变化写 qms_sys_oper_log；已提交单据的字段修改记录原值/新值；
- 待办与消息：进入节点即按处理人生成待办（推送 OA 消息），离开节点关闭；
- OA 流程镜像：首次提交时在 OA 建外部流程实例，RUN_ID 写回 flow_run_id，之后同步转交 / 退回。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..sys import services as svc
from ..sys.models import IfLog, OperLog, TimerCfg
from .errors import BizError, Conflict, Forbidden, ValidationFailed
from .model import coerce, columns_of, now, to_dict

log = logging.getLogger("qms.flow")


@dataclass
class Act:
    code: str
    label: str
    frm: tuple[str, ...]
    to: str | Callable[["Ctx"], str] | None = None
    roles: tuple[str, ...] = ()
    by: str = "assignee"  # assignee：当前待办处理人或 roles；creator：起草人；roles：仅 roles
    kind: str = "normal"  # normal / return / partial
    opinion: str = "optional"  # required / optional / none
    sign: bool = False  # 需要手写签名
    guard: Callable[["Ctx"], None] | None = None
    effect: Callable[["Ctx"], None] | None = None
    style: str = "primary"
    fields: list[dict] = field(default_factory=list)  # 前端弹窗额外输入项
    return_to: tuple[str, ...] | None = None
    confirm: str | None = None
    hidden: bool = False  # 系统内部动作，不在按钮中显示


@dataclass
class ChildDef:
    model: Any
    fk: str
    order_by: str = "id"


@dataclass
class FlowDef:
    biz_type: str
    name: str
    model: Any
    no_field: str
    states: dict[str, str]
    acts: list[Act]
    handlers: dict[str, Callable[[Session, Any], list[str]]] = field(default_factory=dict)
    editable: dict[str, list[str]] = field(default_factory=dict)
    due_hours: dict[str, float] = field(default_factory=dict)
    terminal: set[str] = field(default_factory=set)
    hold_states: set[str] = field(default_factory=set)
    nodes: list[str] = field(default_factory=list)  # 进度条显示的主线节点
    children: dict[str, ChildDef] = field(default_factory=dict)
    summary: Callable[[Any], str] = lambda doc: ""
    initial: str = "DRAFT"
    perm_prefix: str = ""

    def act(self, code: str) -> Act:
        for a in self.acts:
            if a.code == code:
                return a
        raise BizError(f"不支持的操作：{code}")


@dataclass
class Ctx:
    db: Session
    flow: FlowDef
    doc: Any
    user: Any
    payload: dict
    act: Act | None = None
    from_state: str | None = None
    to_state: str | None = None
    extra: dict = field(default_factory=dict)

    def p(self, key, default=None):
        v = self.payload.get(key, default)
        return default if v == "" else v

    @property
    def opinion(self) -> str | None:
        return self.payload.get("opinion")


FLOWS: dict[str, FlowDef] = {}


def register(flow: FlowDef) -> FlowDef:
    FLOWS[flow.biz_type] = flow
    return flow


def get_flow(biz_type: str) -> FlowDef:
    if biz_type not in FLOWS:
        raise BizError(f"未知单据类型 {biz_type}")
    return FLOWS[biz_type]


# ------------------------------------------------------------------ 时限
def add_hours(start: dt.datetime, hours: float, calendar: str = "WORK") -> dt.datetime:
    """工作日历：按 8 小时 = 1 个工作日折算，跳过周六日；24 小时以内的时限按自然时间计（如会签 24 小时）。"""
    if calendar == "NATURAL" or hours <= 24:
        return start + dt.timedelta(hours=hours)
    days = hours / 24
    cur = start
    while days > 0:
        cur += dt.timedelta(days=1)
        if cur.weekday() < 5:
            days -= 1
    return cur


def due_for(db: Session, flow: FlowDef, state: str) -> dt.datetime | None:
    if state in flow.hold_states or state in flow.terminal:
        return None
    tc = db.scalar(select(TimerCfg).where(TimerCfg.biz_type == flow.biz_type, TimerCfg.node == state))
    hours = tc.limit_hours if tc and tc.limit_hours else flow.due_hours.get(state)
    if not hours:
        return None
    return add_hours(now(), hours, tc.calendar if tc else "WORK")


# ------------------------------------------------------------------ 处理人与权限
def handlers_of(db: Session, flow: FlowDef, doc, state: str) -> list[str]:
    h = flow.handlers.get(state)
    if h is None:
        return [doc.created_by] if state == flow.initial and doc.created_by else []
    return [u for u in dict.fromkeys(h(db, doc) or []) if u]


def _todo_holders(db: Session, flow: FlowDef, doc) -> set[str]:
    return {t.assignee for t in svc.open_todos(db, flow.biz_type, doc.id, doc.status)}


def can_do(db: Session, flow: FlowDef, act: Act, doc, user) -> bool:
    if doc.status not in act.frm:
        return False
    if user.is_admin:
        return True
    if act.by == "creator":
        return doc.created_by == user.user_id
    if act.roles and user.has_role(*act.roles):
        return True
    if act.by == "roles":
        return False
    holders = _todo_holders(db, flow, doc)
    if user.user_id in holders:
        return True
    if not holders and doc.status == flow.initial:
        return doc.created_by == user.user_id
    return False


def visited(db: Session, flow: FlowDef, doc) -> list[str]:
    rows = db.scalars(select(OperLog.from_status).where(OperLog.biz_type == flow.biz_type, OperLog.biz_id == doc.id))
    seen = []
    for r in rows:
        if r and r not in seen:
            seen.append(r)
    return seen


def return_targets(db: Session, flow: FlowDef, act: Act, doc) -> list[dict]:
    seen = visited(db, flow, doc)
    allowed = act.return_to if act.return_to is not None else tuple(flow.nodes or flow.states)
    return [{"value": s, "label": flow.states.get(s, s)} for s in allowed if s in seen and s != doc.status]


def available_actions(db: Session, flow: FlowDef, doc, user) -> list[dict]:
    out = []
    for a in flow.acts:
        if a.hidden or not can_do(db, flow, a, doc, user):
            continue
        item = {
            "code": a.code, "label": a.label, "kind": a.kind, "opinion": a.opinion, "sign": a.sign, "style": a.style,
            "fields": a.fields, "confirm": a.confirm,
        }
        if a.kind == "return":
            item["targets"] = return_targets(db, flow, a, doc)
            if not item["targets"]:
                continue
        out.append(item)
    return out


def editable_fields(db: Session, flow: FlowDef, doc, user) -> list[str]:
    st = doc.status
    if st in flow.terminal:
        return []
    if not (user.is_admin or any(can_do(db, flow, a, doc, user) for a in flow.acts if not a.hidden)):
        return []
    return list(flow.editable.get(st, []))


# ------------------------------------------------------------------ 编辑（后端再校验）
def apply_update(db: Session, flow: FlowDef, doc, data: dict, user, row_ver: int | None = None) -> list[dict]:
    if row_ver is not None and int(row_ver) != int(doc.row_ver):
        raise Conflict()
    allowed = set(editable_fields(db, flow, doc, user))
    cols = columns_of(flow.model)
    changes = []
    denied = []
    for k, v in data.items():
        if k in ("id", "row_ver") or k.startswith("_"):
            continue
        if k in flow.children:
            continue
        if k not in cols:
            continue
        new = coerce(flow.model, k, v)
        old = getattr(doc, k)
        if _same(old, new):
            continue
        if k not in allowed:
            denied.append(k)
            continue
        setattr(doc, k, new)
        changes.append({"field": k, "old": _js(old), "new": _js(new)})
    if denied:
        raise Forbidden(f"当前节点不允许修改：{'、'.join(denied)}")
    for name, rows in data.items():
        if name in flow.children and isinstance(rows, list):
            if f"child:{name}" not in allowed:
                raise Forbidden(f"当前节点不允许修改子表：{name}")
            changes += replace_children(db, flow.children[name], doc, rows)
    doc.updated_by = user.user_id
    if changes and doc.status != flow.initial:
        svc.oper_log(db, flow.biz_type, doc.id, getattr(doc, flow.no_field), "edit", "修改", doc.status,
                     doc.status, user, field_changes=changes)
    return changes


def _same(a, b):
    if a is None and b is None:
        return True
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def _js(v):
    return json.loads(json.dumps(v, default=str))


def replace_children(db: Session, child: ChildDef, doc, rows: list[dict]) -> list[dict]:
    model = child.model
    cols = columns_of(model)
    existing = {r.id: r for r in db.scalars(select(model).where(getattr(model, child.fk) == doc.id))}
    keep = set()
    changes = []
    for idx, row in enumerate(rows):
        rid = row.get("id")
        obj = existing.get(rid) if rid else None
        if obj is None:
            obj = model(**{child.fk: doc.id})
            db.add(obj)
            changes.append({"field": model.__tablename__, "old": None, "new": "新增行"})
        else:
            keep.add(rid)
        for k, v in row.items():
            if k in ("id", child.fk) or k not in cols:
                continue
            setattr(obj, k, coerce(model, k, v))
        if "seq" in cols and row.get("seq") is None:
            obj.seq = idx + 1
    for rid, obj in existing.items():
        if rid not in keep:
            db.delete(obj)
            changes.append({"field": model.__tablename__, "old": f"行 {rid}", "new": "删除"})
    db.flush()
    return changes


def children_of(db: Session, flow: FlowDef, doc) -> dict:
    out = {}
    for name, ch in flow.children.items():
        q = select(ch.model).where(getattr(ch.model, ch.fk) == doc.id).order_by(getattr(ch.model, ch.order_by))
        out[name] = [to_dict(r) for r in db.scalars(q)]
    return out


# ------------------------------------------------------------------ 执行
def execute(db: Session, flow: FlowDef, doc, act_code: str, user, payload: dict | None = None) -> Ctx:
    payload = payload or {}
    act = flow.act(act_code)
    if doc.status not in act.frm:
        raise BizError(f"单据当前状态为「{flow.states.get(doc.status, doc.status)}」，不能执行「{act.label}」")
    if not can_do(db, flow, act, doc, user):
        raise Forbidden(f"您不是「{flow.states.get(doc.status)}」节点的处理人，不能执行「{act.label}」")
    rv = payload.get("row_ver")
    if rv is not None and int(rv) != int(doc.row_ver):
        raise Conflict()
    ctx = Ctx(db=db, flow=flow, doc=doc, user=user, payload=payload, act=act, from_state=doc.status)
    if act.opinion == "required" and not (payload.get("opinion") or "").strip():
        raise ValidationFailed([{"field": "opinion", "message": f"【意见/原因】「{act.label}」必须填写原因"}])
    if act.sign:
        ctx.extra["sig_id"] = svc.save_signature(db, user, payload.get("signature"), flow.biz_type, doc.id, doc.status)
    if payload.get("data"):
        apply_update(db, flow, doc, payload["data"], user)
    if act.kind == "return":
        target = payload.get("target")
        valid = [t["value"] for t in return_targets(db, flow, act, doc)]
        if target not in valid:
            raise BizError(f"【退回节点】请选择已经过的节点：{'、'.join(flow.states[v] for v in valid) or '无'}")
        to_state = target
    else:
        if act.guard:
            act.guard(ctx)
        to_state = act.to(ctx) if callable(act.to) else (act.to or doc.status)
    ctx.to_state = to_state
    if act.effect:
        act.effect(ctx)
    to_state = ctx.to_state
    moved = to_state != ctx.from_state
    doc.updated_by = user.user_id
    no = getattr(doc, flow.no_field)
    svc.oper_log(db, flow.biz_type, doc.id, no, act.code, act.label, ctx.from_state, to_state, user,
                 payload.get("opinion"))
    if moved:
        doc.status = to_state
        svc.close_todos(db, flow.biz_type, doc.id, ctx.from_state,
                        status="DONE" if act.kind != "return" else "CANCELLED")
        db.flush()
        refresh_todos(db, flow, doc)
        _mirror_oa(db, flow, doc, act, user)
        if act.kind == "return":
            svc.send_msg(db, "flow.returned", handlers_of(db, flow, doc, to_state), {
                "单号": no, "处理人": user.user_name, "节点名": flow.states.get(to_state), "原因": payload.get("opinion"),
                "单据": flow.name,
            }, flow.biz_type, doc.id, force=True)
    else:
        svc.close_todos(db, flow.biz_type, doc.id, ctx.from_state, assignee=user.user_id)
        refresh_todos(db, flow, doc)
    db.flush()
    return ctx


def system_move(db: Session, flow: FlowDef, doc, to_state: str, action_name: str, opinion: str | None = None):
    """系统自动推进（如：实验报告签发后唤醒检验单、全部事项完成后变更单自动进入待关闭）。"""
    frm = doc.status
    if frm == to_state:
        return
    doc.status = to_state
    svc.oper_log(db, flow.biz_type, doc.id, getattr(doc, flow.no_field), "system", action_name, frm, to_state,
                 None, opinion)
    svc.close_todos(db, flow.biz_type, doc.id, frm)
    db.flush()
    refresh_todos(db, flow, doc)


def refresh_todos(db: Session, flow: FlowDef, doc):
    """按当前节点处理人生成待办（已存在的不重复），并发待办到达消息。"""
    st = doc.status
    if st in flow.terminal:
        svc.close_todos(db, flow.biz_type, doc.id)
        return
    want = handlers_of(db, flow, doc, st)
    have = {t.assignee: t for t in svc.open_todos(db, flow.biz_type, doc.id, st)}
    for t in have.values():
        if t.assignee not in want:
            t.status, t.done_at = "CANCELLED", now()
    due = due_for(db, flow, st)
    no = getattr(doc, flow.no_field)
    label = flow.states.get(st, st)
    title = f"【{flow.name}】{no} {flow.summary(doc)} 待您{label}".replace("  ", " ")
    new = []
    for u in want:
        if u not in have:
            svc.create_todo(db, flow.biz_type, doc.id, no, st, label, title, u, due)
            new.append(u)
    if new and st != flow.initial:
        svc.send_msg(db, "flow.todo", new, {
            "单据": flow.name, "单号": no, "摘要": flow.summary(doc), "节点名": label,
            "截止时间": due.strftime("%m-%d %H:%M") if due else "—",
        }, flow.biz_type, doc.id, title=title)


def _mirror_oa(db: Session, flow: FlowDef, doc, act: Act, user):
    st = get_settings()
    if not st.oa_flow_mirror:
        return
    try:
        flow_id = json.loads(st.oa_flow_ids or "{}").get(flow.biz_type)
    except json.JSONDecodeError:
        flow_id = None
    if not flow_id:
        return
    from ..integrations.oa import OAUserInfo, get_oa
    from ..sys.models import User

    oa = get_oa()
    u = db.scalar(select(User).where(User.user_id == user.user_id))
    op = OAUserInfo(user_id=user.user_id, user_name=user.user_name, uid=u.oa_uid if u else None, dept_id=user.dept_id)
    rec = IfLog(if_code="OA-FLOW", direction="OUT", biz_no=getattr(doc, flow.no_field), status="SENDING",
                request={"biz_type": flow.biz_type, "act": act.code, "to": doc.status})
    db.add(rec)
    try:
        if not doc.flow_run_id:
            doc.flow_run_id = oa.start_flow(flow_id, f"{flow.name} {getattr(doc, flow.no_field)}",
                                            {"qms_link": svc.doc_link(flow.biz_type, doc.id)}, op)
        elif act.kind == "return":
            oa.back_flow(flow_id, doc.flow_run_id, op)
        else:
            oa.turn_flow(flow_id, doc.flow_run_id, op, handlers_of(db, flow, doc, doc.status))
        rec.status, rec.response = "SUCCESS", {"run_id": doc.flow_run_id}
    except Exception as e:  # noqa: BLE001 - 镜像失败不阻塞业务，接口监控页可见
        rec.status, rec.error_msg = "FAILED", str(e)[:1000]
        log.warning("OA 流程镜像失败 %s: %s", flow.biz_type, e)


def new_doc(db: Session, flow: FlowDef, user, data: dict, no: str | None = None):
    from .numbering import next_no

    doc = flow.model()
    cols = columns_of(flow.model)
    for k, v in (data or {}).items():
        if k in cols and k not in ("id", "status", "row_ver", "created_by", flow.no_field):
            setattr(doc, k, coerce(flow.model, k, v))
    setattr(doc, flow.no_field, no or next_no(db, flow.biz_type))
    doc.status = flow.initial
    doc.created_by, doc.created_name, doc.updated_by = user.user_id, user.user_name, user.user_id
    db.add(doc)
    db.flush()
    for name, rows in (data or {}).items():
        if name in flow.children and isinstance(rows, list):
            replace_children(db, flow.children[name], doc, rows)
    svc.oper_log(db, flow.biz_type, doc.id, getattr(doc, flow.no_field), "create", "新建", None, doc.status, user)
    refresh_todos(db, flow, doc)
    return doc


def detail(db: Session, flow: FlowDef, doc, user) -> dict:
    logs = db.scalars(select(OperLog).where(OperLog.biz_type == flow.biz_type, OperLog.biz_id == doc.id)
                      .order_by(OperLog.id))
    todos = svc.open_todos(db, flow.biz_type, doc.id)
    return {
        "data": to_dict(doc),
        "children": children_of(db, flow, doc),
        "status_name": flow.states.get(doc.status, doc.status),
        "actions": available_actions(db, flow, doc, user),
        "editable": editable_fields(db, flow, doc, user),
        "nodes": [{"code": n, "label": flow.states[n]} for n in flow.nodes],
        "states": flow.states,
        "logs": [to_dict(x) for x in logs],
        "todos": [to_dict(t) for t in todos],
        "biz_type": flow.biz_type,
        "name": flow.name,
    }
