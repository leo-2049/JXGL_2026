"""单据编号（5.8.3）：流水号按日/月/年重置，作废后编号不重用。"""
import datetime as dt
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

DEFAULT_RULES = {
    # biz_type: (名称, pattern, reset)
    "dcr": ("文件更改申请", "DCR-{YYYYMMDD}-{SEQ3}", "DAY"),
    "doc_sign": ("文件会签单", "SIGN-{YYYYMMDD}-{SEQ2}", "DAY"),
    "dist": ("发放单", "DIS-{YYYYMMDD}-{SEQ3}", "DAY"),
    "borrow": ("借阅申请", "BRW-{YYYYMMDD}-{SEQ3}", "DAY"),
    "reissue": ("补发申请", "REI-{YYYYMMDD}-{SEQ3}", "DAY"),
    "cp": ("控制计划", "CP-{YYYY}-{SEQ3}", "YEAR"),
    "insp_IQC": ("进料检验单", "IQC-{YYYYMMDD}-{SEQ3}", "DAY"),
    "insp_FA": ("首件检验单", "FA-{YYYYMMDD}-{SEQ2}", "DAY"),
    "insp_PT": ("巡检单", "PT-{YYYYMMDD}-{SEQ2}", "DAY"),
    "insp_LA": ("末件检验单", "LA-{YYYYMMDD}-{SEQ2}", "DAY"),
    "insp_FQC": ("成品检验单", "FQC-{YYYYMMDD}-{SEQ3}", "DAY"),
    "oqc": ("出货检验报告", "OQC-{YYYYMMDD}-{SEQ3}", "DAY"),
    "insp_task": ("待检任务", "TK-{YYYYMMDD}-{SEQ4}", "DAY"),
    "nc": ("品质异常单", "NC-{YYYYMMDD}-{SEQ3}", "DAY"),
    "ecn": ("工程变更单", "ECN{YYMMDD}{SEQ3}", "DAY"),
    "lab_contract": ("实验室委托", "LAB-{YYYYMMDD}-{SEQ3}", "DAY"),
    "lab_sample": ("样品编号", "SP-{YYMM}-{SEQ3}", "MONTH"),
    "lab_report": ("检测报告", "{PREFIX}-{YYYYMMDD}-{SEQ3}", "DAY"),
    "lab_loan": ("样品领用单", "LN-{YYYYMMDD}-{SEQ3}", "DAY"),
    "prj": ("项目", "PRJ-{YYYY}-{SEQ2}", "YEAR"),
    "cp_review": ("控制计划评审任务", "CPR-{YYYYMMDD}-{SEQ3}", "DAY"),
}


def _key(reset: str, today: dt.date) -> str:
    return {"DAY": today.strftime("%Y%m%d"), "MONTH": today.strftime("%Y%m"), "YEAR": today.strftime("%Y")}.get(reset, "ALL")


def next_no(db: Session, biz_type: str, prefix: str = "", today: dt.date | None = None) -> str:
    from ..sys.models import NumberRule

    today = today or dt.date.today()
    q = select(NumberRule).where(NumberRule.biz_type == biz_type)
    if db.bind.dialect.name != "sqlite":
        q = q.with_for_update()
    rule = db.scalar(q)
    if rule is None:
        name, pattern, reset = DEFAULT_RULES.get(biz_type, (biz_type, biz_type.upper() + "-{YYYYMMDD}-{SEQ3}", "DAY"))
        rule = NumberRule(biz_type=biz_type, name=name, pattern=pattern, reset_cycle=reset, current_seq=0)
        db.add(rule)
        db.flush()
    key = _key(rule.reset_cycle, today)
    if rule.current_key != key:
        rule.current_key, rule.current_seq = key, 0
    rule.current_seq += 1
    seq = rule.current_seq

    def rep(m: re.Match) -> str:
        tok = m.group(1)
        if tok.startswith("SEQ"):
            return str(seq).zfill(int(tok[3:] or 3))
        return {
            "YYYYMMDD": today.strftime("%Y%m%d"), "YYMMDD": today.strftime("%y%m%d"), "YYYY": today.strftime("%Y"),
            "YYMM": today.strftime("%y%m"), "PREFIX": prefix,
        }.get(tok, tok)

    return re.sub(r"\{(\w+)\}", rep, rule.pattern)
