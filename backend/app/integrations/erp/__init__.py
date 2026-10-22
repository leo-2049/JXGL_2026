"""鼎捷 ERP 接口（5.8.2 I-01～I-09）。

拉取类接口（I-01/02/03/05/07/09）落地到 qms_base_* 表；写入类接口（I-04/06/08）经 ErpService.push
记 qms_sys_if_log，以「业务单号+动作」为幂等键，失败每 5 分钟重试，最多 3 次后置 FAILED 并通知系统管理员。
接口方式（中间库 / Web API）待与鼎捷确认，实现类按 QMS_ERP_MODE 切换。
"""
import datetime as dt
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...core.model import now
from ...sys.models import IfLog

log = logging.getLogger("qms.erp")

IF_NAMES = {
    "I-01": "主数据", "I-02": "工单与工序", "I-03": "到货/委外收货", "I-04": "检验结果回写", "I-05": "批次库存查询",
    "I-06": "批次锁定/解锁", "I-07": "出货通知", "I-08": "新品号", "I-09": "成本与费率",
}
MAX_RETRY = 3
RETRY_MINUTES = 5


class ErpGateway:
    """传输层。mock 下写入类接口直接成功并更新本地库存锁定量；webapi 下 POST 到鼎捷中间件。"""

    def __init__(self):
        self.st = get_settings()
        self.fail_next = 0  # 测试用：让接下来 N 次调用失败

    def send(self, db: Session, if_code: str, payload: dict) -> dict:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise RuntimeError("ERP 接口超时（模拟）")
        if self.st.erp_mode == "webapi":
            r = httpx.post(f"{self.st.erp_base_url.rstrip('/')}/{if_code}", json=payload,
                           headers={"Authorization": f"Bearer {self.st.erp_token}"}, timeout=15)
            r.raise_for_status()
            return r.json()
        if if_code == "I-06":
            from ...base.models import Stock

            for st in db.scalars(select(Stock).where(Stock.batch_no == payload.get("batch_no"))):
                if payload.get("action") == "LOCK":
                    st.locked_qty = st.qty
                elif payload.get("action") in ("UNLOCK", "TO_NG"):
                    st.locked_qty = 0
                    if payload.get("action") == "TO_NG":
                        st.mat_type = "NG"
        return {"ok": True, "erp_doc": f"ERP-{if_code}-{payload.get('biz_no')}"}


gateway = ErpGateway()


def push(db: Session, if_code: str, biz_no: str, action: str, payload: dict) -> IfLog:
    """写入类接口调用。返回 IfLog（status=SUCCESS / RETRYING / FAILED）。"""
    idem = f"{if_code}:{biz_no}:{action}"
    done = db.scalar(select(IfLog).where(IfLog.idem_key == idem, IfLog.status == "SUCCESS"))
    if done:
        return done
    rec = db.scalar(select(IfLog).where(IfLog.idem_key == idem))
    if rec is None:
        rec = IfLog(if_code=if_code, direction="OUT", biz_no=biz_no, idem_key=idem,
                    request={**payload, "biz_no": biz_no, "action": action}, status="PENDING", retry_count=0)
        db.add(rec)
        db.flush()
    _attempt(db, rec)
    return rec


def _attempt(db: Session, rec: IfLog):
    rec.status = "SENDING"
    try:
        rec.response = gateway.send(db, rec.if_code, rec.request or {})
        rec.status, rec.error_msg, rec.next_retry_at = "SUCCESS", None, None
    except Exception as e:  # noqa: BLE001 - 任何传输错误都进入重试
        rec.retry_count = (rec.retry_count or 0) + 1
        rec.error_msg = str(e)[:1000]
        if rec.retry_count >= MAX_RETRY:
            rec.status = "FAILED"
            from ...sys.services import notify_admins

            notify_admins(db, "if.failed", {"单号": rec.biz_no, "接口": f"{rec.if_code} {IF_NAMES.get(rec.if_code, '')}",
                                            "错误信息": rec.error_msg})
        else:
            rec.status = "RETRYING"
            rec.next_retry_at = now() + dt.timedelta(minutes=RETRY_MINUTES)
    _sync_back(db, rec)


def _sync_back(db: Session, rec: IfLog):
    """把接口结果写回业务单据的同步状态字段。"""
    req = rec.request or {}
    model_name, field = req.get("_model"), req.get("_field")
    if not model_name:
        return
    from ...core.registry import MODELS

    model = MODELS.get(model_name)
    obj = db.get(model, req.get("_id")) if model else None
    if obj is not None and field:
        setattr(obj, field, {"SUCCESS": "SYNCED", "FAILED": "FAILED"}.get(rec.status, "PENDING")
                if field == "erp_sync" else {"SUCCESS": req.get("_ok_value", "LOCKED"), "FAILED": "FAILED"}.get(rec.status, "PENDING"))


def retry_due(db: Session) -> int:
    n = 0
    for rec in db.scalars(select(IfLog).where(IfLog.status == "RETRYING", IfLog.next_retry_at <= now())):
        _attempt(db, rec)
        n += 1
    return n


def resend(db: Session, rec: IfLog):
    rec.status, rec.retry_count = "PENDING", 0
    _attempt(db, rec)


def stock_query(db: Session, part_no: str) -> list[dict]:
    """I-05 批次库存查询：原材料按 BOM 反查。"""
    from ...base.models import Bom, Stock

    codes = [part_no] + list(db.scalars(select(Bom.child_part_no).where(Bom.parent_part_no == part_no)))
    rows = db.scalars(select(Stock).where(Stock.part_no.in_(codes), Stock.is_deleted.is_(False)))
    t = now()
    db.add(IfLog(if_code="I-05", direction="IN", biz_no=part_no, status="SUCCESS", request={"part_no": part_no}))
    return [{
        "mat_type": r.mat_type, "mat_code": r.mat_code or r.part_no, "mat_name": r.mat_name, "batch_no": r.batch_no,
        "location": r.location, "qty": float(r.qty or 0), "unit": r.unit, "read_at": t,
    } for r in rows]
