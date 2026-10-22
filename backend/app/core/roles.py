"""角色定义（5.8.4 权限矩阵）。一人可兼多角色；角色 → 按钮权限编码由后台「角色与按钮权限」维护。"""

ROLES: dict[str, tuple[str, list[str]]] = {
    # code: (名称, 初始按钮权限)
    "ADMIN": ("系统管理员", ["*"]),
    "GM": ("总经理 / 副总", ["doc.*", "rpt.*", "nc.view", "ecn.view", "cp.view", "lab.view", "prj.view"]),
    "QA_MGR": ("品质经理", ["doc.dcr.add", "cp.*", "insp.*", "spc.*", "nc.*", "ecn.eval", "lab.*", "rpt.*",
                            "doc.file.export", "nc.config", "nc.warn.release"]),
    "QA_SUP": ("品质副主管", ["insp.review", "insp.oqc.confirm", "nc.add", "nc.judge", "rpt.quality", "doc.dcr.add",
                             "insp.supplement.approve", "spc.limit"]),
    "DC": ("体系 / 文控", ["doc.*", "rpt.doc", "sys.log", "doc.file.import"]),
    "IQC": ("IQC 检验员", ["insp.iqc", "insp.lab.req", "nc.add", "lab.contract.add", "doc.dcr.add"]),
    "IQC_LEADER": ("IQC 组长", ["insp.iqc", "insp.review", "insp.task.cancel", "nc.add", "lab.contract.add"]),
    "IPQC": ("IPQC 检验员", ["insp.ipqc", "insp.task.scan", "insp.fa.add", "spc.view", "nc.add",
                             "lab.contract.add"]),
    "IPQC_LEADER": ("IPQC 组长", ["insp.ipqc", "insp.review", "insp.task.cancel", "insp.fa.add", "spc.view",
                                  "nc.add"]),
    "OQC": ("OQC / FQC 检验员", ["insp.fqc", "insp.oqc", "insp.oqc.send", "nc.add", "lab.contract.add"]),
    "OQC_LEADER": ("OQC 组长", ["insp.fqc", "insp.oqc", "insp.oqc.send", "insp.oqc.confirm", "insp.oqc.tpl",
                                "insp.review", "nc.add"]),
    "CQE": ("CQE / SQE / 项目 QE", ["nc.add", "nc.judge", "nc.sqe", "lab.contract.add", "rpt.supplier"]),
    "LAB_TESTER": ("实验室测试员", ["lab.task", "lab.report", "nc.add"]),
    "LAB_LEADER": ("实验室组长", ["lab.*", "rpt.lab"]),
    "LAB_SAMPLE": ("样品管理员", ["lab.sample", "lab.contract.accept"]),
    "ENG_MGR": ("工程经理", ["cp.*", "ecn.*", "prj.*", "doc.dwg.*", "rpt.eng", "ecn.config", "prj.tpl"]),
    "ENG": ("项目 / 设计工程师", ["cp.add", "cp.import", "cp.revise", "cp.copy", "cp.export", "ecn.add", "prj.add",
                                  "doc.dwg.upload", "doc.dcr.add", "lab.contract.add"]),
    "ENG_PRE": ("变更初审专人", ["ecn.pre"]),
    "PROD": ("计划 / 生产主管、班组长", ["nc.add", "ecn.eval", "rpt.prod"]),
    "MOLD": ("模具仓", ["ecn.mold"]),
    "PUR": ("采购", ["ecn.eval", "rpt.supplier"]),
    "SALES": ("业务", ["ecn.add", "ecn.cust", "nc.add", "lab.contract.add", "rpt.customer"]),
    "STAFF": ("普通员工", ["doc.dcr.add", "doc.borrow"]),
}

# 岗位 → 默认角色（组织同步后按岗位自动授予）
POST_ROLES = {
    "QA_MGR": ["QA_MGR"], "QA_SUP": ["QA_SUP"], "DC": ["DC"], "IQC": ["IQC"], "IQC_LEADER": ["IQC_LEADER", "IQC"],
    "IPQC": ["IPQC"], "IPQC_LEADER": ["IPQC_LEADER", "IPQC"], "OQC": ["OQC"], "OQC_LEADER": ["OQC_LEADER", "OQC"],
    "CQE": ["CQE"], "LAB_TESTER": ["LAB_TESTER"], "LAB_LEADER": ["LAB_LEADER", "LAB_TESTER"],
    "LAB_SAMPLE": ["LAB_SAMPLE"], "ENG_MGR": ["ENG_MGR", "ENG"], "ENG": ["ENG"], "PROD": ["PROD"],
    "MOLD": ["MOLD"], "PUR": ["PUR"], "SALES": ["SALES"], "GM": ["GM"],
}
