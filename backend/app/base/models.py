"""公共主数据（5.8.6）。ERP 来源的表只读同步；设备、量具、标准库本地维护。"""
from sqlalchemy import UniqueConstraint

from ..core.model import Base, CommonMixin, Money, Qty, d, dtm, flag, i, js, num, s


class Part(Base, CommonMixin):
    """物料（I-01）"""
    __tablename__ = "qms_base_part"
    part_no = s(32, unique=True, index=True)
    part_name = s(100)
    spec = s(200)
    mat_category = s(10, comment="RAW 原材 / BUY 外购 / OUT 委外 / SEMI 半品 / FG 成品")
    material_grade = s(40)
    surface = s(60, comment="表面处理")
    unit = s(10)
    cust_code = s(20, index=True)
    cust_part_no = s(200)
    cust_rev = s(10)
    hsf_flag = flag(True, "是否环保")
    std_cost = num(Money, comment="标准成本单价（I-09）")
    mat_price = num(Money, comment="材料单价")
    erp_updated_at = dtm()


class Bom(Base, CommonMixin):
    """BOM"""
    __tablename__ = "qms_base_bom"
    __table_args__ = (UniqueConstraint("parent_part_no", "child_part_no"),)
    parent_part_no = s(32, index=True)
    child_part_no = s(32, index=True)
    qty = num(Qty)


class Customer(Base, CommonMixin):
    """客户（I-01 + 本地扩展）"""
    __tablename__ = "qms_base_customer"
    cust_code = s(20, unique=True)
    name = s(100)
    address = s(200)
    is_japanese = flag(False, "日系客户（变更时限 6 个月）")
    need_supplier_code = flag(False)
    supplier_code_at_cust = s(30)
    report_mails = s(500, comment="出货报告收件邮箱，; 分隔")
    need_cust_approve_cp = flag(False, "控制计划需客户批准")
    report_attach_list = js([], "出货报告必带附件")


class Supplier(Base, CommonMixin):
    """供应商（I-01 + 本地扩展）"""
    __tablename__ = "qms_base_supplier"
    supplier_code = s(20, unique=True)
    name = s(100)
    supply_category = s(20)
    contact_mail = s(200)
    tighten_lots_left = i(0, "剩余加严批数")


class WorkOrder(Base, CommonMixin):
    """工单与工序（I-02）"""
    __tablename__ = "qms_base_wo"
    wo_no = s(32, unique=True)
    batch_no = s(32, unique=True, index=True)
    part_no = s(32, index=True)
    qty = num(Qty)
    order_no = s(32, index=True)
    route = js([], "工艺路线 [{proc_no, proc_name, equip_no}]")
    heat_nos = s(200, comment="领料炉批号")
    operator = s(32)
    started_at = dtm()
    finished_at = dtm()
    reported_qty = num(Qty, comment="报工数")


class Arrival(Base, CommonMixin):
    """到货 / 委外收货（I-03）"""
    __tablename__ = "qms_base_arrival"
    __table_args__ = (UniqueConstraint("arrival_no", "line_no"),)
    arrival_no = s(32, index=True)
    line_no = i(1)
    part_no = s(32)
    supplier_code = s(20)
    heat_no = s(40)
    qty = num(Qty)
    unit = s(10)
    arrival_date = d()
    out_proc = s(40, comment="委外工序")


class ShipNotice(Base, CommonMixin):
    """出货通知（I-07）"""
    __tablename__ = "qms_base_ship_notice"
    __table_args__ = (UniqueConstraint("ship_notice_no", "batch_no"),)
    ship_notice_no = s(32, index=True)
    delivery_no = s(32)
    cust_code = s(20)
    order_no = s(32)
    part_no = s(32)
    batch_no = s(32)
    qty = num(Qty)
    ship_date = d()


class Stock(Base, CommonMixin):
    """批次库存快照（I-05 mock 数据源）"""
    __tablename__ = "qms_base_stock"
    part_no = s(32, index=True)
    mat_code = s(32)
    mat_name = s(100)
    mat_type = s(10, comment="RAW 原材料 / SEMI 半成品 / FG 成品 / NG 不良管制品")
    batch_no = s(40)
    location = s(40)
    qty = num(Qty)
    unit = s(10)
    locked_qty = num(Qty, 0)


class Equipment(Base, CommonMixin):
    """设备（生产 + 实验室）"""
    __tablename__ = "qms_base_equipment"
    equip_no = s(32, unique=True)
    name = s(100)
    model = s(100)
    dept = s(50)
    cqi_type = s(10, comment="CQI-9 / CQI-11")
    calib_due = d()
    auto_export = flag(False)
    current_wo = s(32)


class GaugeType(Base, CommonMixin):
    """量具类型"""
    __tablename__ = "qms_base_gauge_type"
    type_code = s(20, unique=True)
    name = s(50)


class Gauge(Base, CommonMixin):
    """量具"""
    __tablename__ = "qms_base_gauge"
    gauge_no = s(32, unique=True)
    gauge_type_id = i()
    name = s(50)
    resolution = num()
    calib_due = d()
    msa_result = s(10, comment="PASS / FAIL")
    msa_date = d()


class Standard(Base, CommonMixin):
    """标准库"""
    __tablename__ = "qms_base_standard"
    std_no = s(60)
    name = s(200)
    version = s(20)
