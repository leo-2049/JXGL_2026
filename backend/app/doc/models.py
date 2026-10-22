"""文档管理（5.1.7）。"""
from sqlalchemy import UniqueConstraint

from ..core.model import Base, CommonMixin, d, dtm, flag, fk, i, js, s, txt


class DocFile(Base, CommonMixin):
    """受控文件台账 SY/FM0301"""
    __tablename__ = "qms_doc_file"
    file_no = s(40, unique=True, index=True, comment="文件编号")
    file_name = s(100)
    file_level = s(20, comment="QM / QP / WI / FM / EXT / DWG")
    parent_file_id = fk()
    rev = s(10, "A0")
    owner_dept_id = i()
    preparer = s(32)
    auditor = s(32)
    approver = s(32)
    effective_date = d()
    implement_date = d()
    dist_scope = js([], "发放范围：部门 ID 列表")
    pdf_att_id = fk(comment="受控 PDF")
    src_att_id = fk(comment="可编辑原文件")
    recall_pending = flag(False, "待回收")
    cur_dcr_id = fk(comment="在途更改申请")
    remark = s(2000)


class DocVersion(Base, CommonMixin):
    """版本履历 SY/FM0310"""
    __tablename__ = "qms_doc_version"
    __table_args__ = (UniqueConstraint("file_id", "rev"),)
    file_id = fk()
    rev = s(10)
    change_content = s(2000)
    reviser = s(50)
    revise_date = d()
    auditor = s(50)
    audit_date = d()
    approver = s(50)
    approve_date = d()
    dcr_id = fk()
    pdf_att_id = fk()
    is_history = flag(False, "上线时导入的历史版次")


class Dcr(Base, CommonMixin):
    """文件和资料更改申请单 SY/FM0304"""
    __tablename__ = "qms_doc_dcr"
    dcr_no = s(32, unique=True)
    apply_type = s(20, "REVISE", "NEW / REVISE / OBSOLETE")
    file_id = fk()
    file_no = s(40)
    file_name = s(100)
    file_level = s(20)
    parent_file_id = fk()
    owner_dept_id = i()
    cur_rev = s(10)
    new_rev = s(10)
    apply_dept_id = i()
    applicant = s(32)
    before_text = s(2000)
    after_text = s(2000)
    reason = s(1000)
    ecn_id = fk()
    nc_id = fk()
    dept_opinion = s(500)
    dept_sig_id = fk()
    sign_dept_ids = js([], "会签部门")
    dist_scope = js([], "发放范围")
    audit_opinion = s(500)
    audit_sig_id = fk()
    approve_opinion = s(500)
    approve_sig_id = fk()
    approver = s(32)
    auditor = s(32)
    effective_date = d()
    implement_date = d()
    sign_round = i(0)


class DocSign(Base, CommonMixin):
    """文件会签单 SY/FM0309"""
    __tablename__ = "qms_doc_sign"
    sign_no = s(32, unique=True)
    dcr_id = fk()
    category = s(20)
    name = s(150)


class DocSignDept(Base, CommonMixin):
    """会签部门明细"""
    __tablename__ = "qms_doc_sign_dept"
    sign_id = fk()
    dcr_id = fk()
    round = i(1)
    dept_id = i()
    receive_at = dtm()
    signer = s(32)
    signer_name = s(50)
    opinion_result = s(20, comment="AGREE / AGREE_WITH / DISAGREE")
    opinion = s(1000)
    sign_at = dtm()
    sig_id = fk()
    adopt = s(10, comment="ADOPT / REJECT")
    adopt_note = s(500)
    confirm_date = d()
    transfer_note = s(200)


class Dist(Base, CommonMixin):
    """发放单 SY/FM0303"""
    __tablename__ = "qms_doc_dist"
    dist_no = s(32, unique=True)
    dist_category = s(20)
    file_id = fk()
    drawing_id = fk()
    dcr_id = fk()
    reissue_id = fk()
    doc_no = s(40)
    doc_name = s(150)
    rev = s(10)
    release_date = d()
    first_issue = flag(True)


class DistItem(Base, CommonMixin):
    """发放明细"""
    __tablename__ = "qms_doc_dist_item"
    dist_id = fk()
    file_id = fk()
    drawing_id = fk()
    doc_no = s(40)
    doc_name = s(150)
    rev = s(10)
    dept_id = i()
    receiver = s(32, index=True)
    receiver_name = s(50)
    dist_mode = s(10, "ELEC", "ELEC / PAPER")
    copies = i(1)
    copy_no = s(20)
    ack_status = s(10, "PENDING", "PENDING / SIGNED / RECALLED")
    ack_at = dtm()
    sig_id = fk()


class Recall(Base, CommonMixin):
    """旧版回收明细"""
    __tablename__ = "qms_doc_recall"
    dist_id = fk()
    old_item_id = fk()
    file_id = fk()
    drawing_id = fk()
    doc_no = s(40)
    old_rev = s(10)
    dept_id = i()
    dist_mode = s(10)
    old_copies = i()
    copy_no = s(20)
    recall_copies = i()
    recall_by = s(32)
    recall_at = dtm()
    diff_reason = s(200)
    recall_status = s(10, "PENDING", "PENDING / DONE / DIFF")


class Drawing(Base, CommonMixin):
    """图纸档案"""
    __tablename__ = "qms_doc_drawing"
    drawing_no = s(40, index=True)
    part_no = s(32, index=True)
    cust_part_no = s(100)
    cust_code = s(20)
    drawing_type = s(20, "FINISHED", "CUSTOMER / FINISHED / PROCESS / PURCHASE")
    cust_rev = s(10)
    sy_rev = s(10)
    file_att_id = fk()
    ecn_id = fk()
    download_allowed = flag(False)
    push_dept_ids = js([])
    remark = s(500)


class DrawingAck(Base, CommonMixin):
    """图纸推送回签"""
    __tablename__ = "qms_doc_drawing_ack"
    drawing_id = fk()
    dept_id = i()
    receiver = s(32, index=True)
    receiver_name = s(50)
    ack_at = dtm()


class Borrow(Base, CommonMixin):
    """借阅申请 SY/FM0306"""
    __tablename__ = "qms_doc_borrow"
    borrow_no = s(32, unique=True)
    dept_id = i()
    borrower = s(32)
    borrow_mode = s(10, "ELEC", "ELEC 电子在线阅览 / PAPER 纸质借出")
    borrow_date = d()
    plan_return_date = d()
    purpose = s(500)
    return_date = d()
    overdue = flag(False)


class BorrowItem(Base, CommonMixin):
    """借阅文件明细"""
    __tablename__ = "qms_doc_borrow_item"
    borrow_id = fk()
    file_id = fk()
    file_no = s(40)
    file_name = s(100)
    rev = s(10)


class Reissue(Base, CommonMixin):
    """补发申请 SY/FM0305"""
    __tablename__ = "qms_doc_reissue"
    reissue_no = s(32, unique=True)
    category = s(10, "FILE", "DRAWING / FILE / OTHER")
    file_id = fk()
    drawing_id = fk()
    model = s(100)
    part_no = s(32)
    part_name = s(100)
    file_no = s(40)
    file_rev = s(10)
    file_name = s(100)
    other_desc = s(200)
    copies = i(1)
    paper_size = s(10, "A4")
    need_date = d()
    reason = s(1000)
    dept_id = i()
    resp_dept_id = i(comment="责任单位")
    dist_item_id = fk()
    remark = txt()
