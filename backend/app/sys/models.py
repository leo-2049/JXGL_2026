"""系统表（报告 5.8.6）+ 组织镜像（来自通达 OA 组织架构）。"""
import datetime as dt

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..core.model import Base, IdMixin, d, dtm, flag, fk, i, js, now, s, txt


# ---------------- 组织（从通达 OA 同步：USER / DEPARTMENT / USER_PRIV） ----------------
class Dept(Base, IdMixin):
    """部门（OA DEPARTMENT 镜像）"""
    __tablename__ = "qms_sys_dept"
    dept_id: Mapped[int] = mapped_column(Integer, unique=True, comment="OA DEPT_ID")
    dept_name = s(100)
    parent_id = i()
    manager_user_id = s(32, comment="部门负责人")
    sort = i(0)


class User(Base, IdMixin):
    """用户（OA USER 镜像 + 平台扩展）"""
    __tablename__ = "qms_sys_user"
    user_id: Mapped[str] = mapped_column(String(32), unique=True, comment="OA USER_ID")
    oa_uid = i(comment="OA UID")
    user_name = s(50)
    byname = s(32, comment="工号")
    dept_id = i()
    posts = s(200, comment="岗位，逗号分隔，如 IQC,IQC_LEADER")
    phone = s(30)
    email = s(100)
    enabled = flag(True)
    sig_image = txt("预存签名（base64）")
    synced_at = dtm()


class Role(Base, IdMixin):
    """平台角色（初始按 5.8.4 权限矩阵）"""
    __tablename__ = "qms_sys_role"
    role_code: Mapped[str] = mapped_column(String(32), unique=True)
    role_name = s(50)
    perms = js([], "按钮权限编码列表；* 表示全部")
    data_scope = s(20, "ALL", "ALL / DEPT / SELF")
    remark = s(200)


class UserRole(Base, IdMixin):
    __tablename__ = "qms_sys_user_role"
    __table_args__ = (UniqueConstraint("user_id", "role_code"),)
    user_id = s(32, index=True)
    role_code = s(32, index=True)


# ---------------- 字典 / 编号 / 配置 ----------------
class Dict(Base, IdMixin):
    """字典"""
    __tablename__ = "qms_sys_dict"
    __table_args__ = (UniqueConstraint("dict_type", "code"),)
    dict_type = s(40, index=True)
    code = s(40)
    name = s(100)
    sort = i(0)
    enabled = flag(True)
    extra = js(None)


class NumberRule(Base, IdMixin):
    """编号规则（5.8.3）"""
    __tablename__ = "qms_sys_number_rule"
    biz_type: Mapped[str] = mapped_column(String(32), unique=True)
    name = s(50)
    pattern = s(60, comment="如 NC-{YYYYMMDD}-{SEQ3}")
    reset_cycle = s(10, "DAY", "DAY / MONTH / YEAR / NEVER")
    current_key = s(20)
    current_seq = i(0)


class Config(Base, IdMixin):
    """键值配置（阈值、借期、报告周期天数等）"""
    __tablename__ = "qms_sys_config"
    cfg_key: Mapped[str] = mapped_column(String(60), unique=True)
    cfg_value = s(500)
    name = s(100)
    unit = s(20)


class TimerCfg(Base, IdMixin):
    """时限与提醒配置"""
    __tablename__ = "qms_sys_timer_cfg"
    __table_args__ = (UniqueConstraint("biz_type", "node"),)
    biz_type = s(32)
    node = s(32)
    limit_hours = i()
    calendar = s(10, "WORK", "WORK 工作日 / NATURAL 自然日")
    remind_every_hours = i(4)
    escalate_after_hours = i(24)
    escalate_to = s(50, "DEPT_MANAGER")


# ---------------- 附件 / 签名 / 日志 ----------------
class Attachment(Base, IdMixin):
    """附件（按 biz_type + biz_id + field_code 关联）"""
    __tablename__ = "qms_sys_attachment"
    biz_type = s(32, index=True)
    biz_id = fk()
    field_code = s(40)
    file_name = s(200)
    file_size = i()
    mime = s(100)
    storage_path = s(300)
    thumb_path = s(300)
    uploaded_by = s(32)
    uploaded_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=now)


class Signature(Base, IdMixin):
    """手写签名（图片 + 时间戳 + IP + 设备）"""
    __tablename__ = "qms_sys_signature"
    user_id = s(32, index=True)
    user_name = s(50)
    image = txt("PNG base64")
    signed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=now)
    ip = s(50)
    device = s(200)
    biz_type = s(32)
    biz_id = fk()
    node = s(32)


class OperLog(Base, IdMixin):
    """操作日志：状态变化与字段修改留痕"""
    __tablename__ = "qms_sys_oper_log"
    biz_type = s(32, index=True)
    biz_id = fk()
    biz_no = s(40)
    action = s(40)
    action_name = s(50)
    from_status = s(20)
    to_status = s(20)
    opinion = s(2000)
    field_changes = js(None)
    user_id = s(32)
    user_name = s(50)
    at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=now, index=True)
    ip = s(50)


class Todo(Base, IdMixin):
    """待办（同步推送到通达 OA 待办 / 消息）"""
    __tablename__ = "qms_sys_todo"
    biz_type = s(32, index=True)
    biz_id = fk()
    biz_no = s(40)
    node = s(32)
    node_name = s(50)
    title = s(300)
    assignee = s(32, index=True)
    assignee_name = s(50)
    status = s(10, "OPEN", "OPEN / DONE / CANCELLED")
    arrived_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=now)
    due_at = dtm()
    done_at = dtm()
    last_remind_at = dtm()
    escalated = flag(False)
    link = s(300)


class MsgTpl(Base, IdMixin):
    """消息模板"""
    __tablename__ = "qms_sys_msg_tpl"
    event_code: Mapped[str] = mapped_column(String(60), unique=True)
    module = s(20)
    event_name = s(60)
    channels = s(50, "SITE,APP", "SITE 站内 / APP 推送 / MAIL 邮件")
    title_tpl = s(300)
    body_tpl = s(1000)
    receivers_rule = s(200)
    enabled = flag(True)


class MsgLog(Base, IdMixin):
    """消息发送记录"""
    __tablename__ = "qms_sys_msg_log"
    event_code = s(60, index=True)
    biz_type = s(32)
    biz_id = fk()
    receiver = s(100, index=True)
    channel = s(10)
    title = s(300)
    body = s(2000)
    link = s(300)
    status = s(10, "SENT", "SENT / FAILED / SKIPPED")
    error = s(500)
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=now)
    read_at = dtm()


class IfLog(Base, IdMixin):
    """接口日志（I-01～I-09 及 OA 接口）"""
    __tablename__ = "qms_sys_if_log"
    if_code = s(20, index=True)
    direction = s(10)
    biz_no = s(60, index=True)
    idem_key = s(120, index=True, comment="幂等键：业务单号+动作")
    request = js(None)
    response = js(None)
    status = s(10, "PENDING", "PENDING/SENDING/SUCCESS/RETRYING/FAILED")
    retry_count = i(0)
    error_msg = s(1000)
    next_retry_at = dtm()
    at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=now)
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=now, onupdate=now)


class Stamp(Base, IdMixin):
    """电子章（质量合格章 / 检测专用章）"""
    __tablename__ = "qms_sys_stamp"
    stamp_code: Mapped[str] = mapped_column(String(20), unique=True)
    name = s(50)
    image = txt()
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


__all__ = [
    "Dept", "User", "Role", "UserRole", "Dict", "NumberRule", "Config", "TimerCfg", "Attachment", "Signature",
    "OperLog", "Todo", "MsgTpl", "MsgLog", "IfLog", "Stamp",
]
_ = (d,)
