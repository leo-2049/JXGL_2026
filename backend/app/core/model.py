"""公共字段（报告 5.0 开发规格约定 3）与列类型约定（约定 4）。"""
import datetime as dt
from decimal import Decimal

from sqlalchemy import JSON, BigInteger, Boolean, Date, DateTime, Integer, Numeric, String, Text, event, inspect
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from ..db import Base

# 类型约定：测量值 decimal(14,4)；数量 decimal(14,3)；金额 decimal(14,2)；比率 decimal(7,4)
Measure = Numeric(14, 4)
Qty = Numeric(14, 3)
Money = Numeric(14, 2)
Rate = Numeric(7, 4)
PK = BigInteger().with_variant(Integer, "sqlite")


def now() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


def col(type_, default=None, comment: str = "", **kw):
    return mapped_column(type_, default=default, comment=comment, **kw)


def s(length: int = 100, default=None, comment: str = "", **kw):
    return mapped_column(String(length), default=default, comment=comment, nullable=True, **kw)


def i(default=None, comment: str = "", **kw):
    return mapped_column(Integer, default=default, comment=comment, nullable=True, **kw)


def fk(comment: str = "", **kw):
    return mapped_column(PK, comment=comment, nullable=True, index=True, **kw)


def d(comment: str = "", **kw):
    return mapped_column(Date, comment=comment, nullable=True, **kw)


def dtm(comment: str = "", **kw):
    return mapped_column(DateTime, comment=comment, nullable=True, **kw)


def num(type_=Measure, default=None, comment: str = "", **kw):
    return mapped_column(type_, default=default, comment=comment, nullable=True, **kw)


def flag(default=False, comment: str = ""):
    return mapped_column(Boolean, default=default, comment=comment, nullable=False)


def txt(comment: str = ""):
    return mapped_column(Text, comment=comment, nullable=True)


def js(default=None, comment: str = ""):
    return mapped_column(JSON, default=default, comment=comment, nullable=True)


class IdMixin:
    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)


class CommonMixin(IdMixin):
    """每张业务表都有的公共字段。"""

    status: Mapped[str | None] = mapped_column(String(20), comment="单据状态", index=True)
    flow_run_id: Mapped[int | None] = mapped_column(Integer, comment="通达工作流 RUN_ID")
    created_by: Mapped[str | None] = mapped_column(String(32), comment="创建人 USER_ID")
    created_name: Mapped[str | None] = mapped_column(String(50), comment="创建人姓名")
    created_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=now)
    updated_by: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=now, onupdate=now)
    row_ver: Mapped[int] = mapped_column(Integer, default=1, nullable=False, comment="乐观锁版本")
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    @declared_attr.directive
    def __table_args__(cls):  # noqa: N805
        return {"comment": getattr(cls, "__doc__", "") or ""}


@event.listens_for(CommonMixin, "before_update", propagate=True)
def _bump_row_ver(mapper, connection, target):
    target.row_ver = (target.row_ver or 0) + 1


def _plain(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat(sep=" ") if isinstance(v, dt.datetime) else v.isoformat()
    return v


def to_dict(obj, exclude: tuple = ()) -> dict:
    if obj is None:
        return None
    out = {}
    for c in inspect(obj).mapper.column_attrs:
        if c.key in exclude:
            continue
        out[c.key] = _plain(getattr(obj, c.key))
    return out


def columns_of(model) -> dict:
    return {c.key: c for c in inspect(model).mapper.column_attrs}


def coerce(model, field: str, value):
    """把前端 JSON 值转换成列类型（日期字符串 → date 等）。"""
    if value == "" or value is None:
        return None
    col_ = columns_of(model).get(field)
    if col_ is None:
        return value
    pytype = col_.columns[0].type
    try:
        py = pytype.python_type
    except NotImplementedError:
        return value
    if py is dt.datetime and isinstance(value, str):
        return dt.datetime.fromisoformat(value.replace("T", " ").replace("Z", "")[:19])
    if py is dt.date and isinstance(value, str):
        return dt.date.fromisoformat(value[:10])
    if py is Decimal and not isinstance(value, Decimal):
        return Decimal(str(value))
    if py is bool:
        return bool(value) if not isinstance(value, str) else value in ("1", "true", "True", "是")
    if py is int and isinstance(value, str):
        return int(value)
    return value


__all__ = [
    "Base", "CommonMixin", "IdMixin", "Measure", "Qty", "Money", "Rate", "PK", "now", "col", "s", "i", "fk",
    "d", "dtm", "num", "flag", "txt", "js", "to_dict", "columns_of", "coerce",
]
