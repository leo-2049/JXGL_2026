"""真实通达 OA 对接（restapi/1.0 + oauth2 框架）。

接口口径来自通达 OA restapi 文档：
- OAuth2：{oa}/oauth2/authorize、{oa}/oauth2/token、{oa}/oauth2/userinfo（授权码 + refresh_token；
  平台另申请一个 client_credentials 客户端作为服务账号，用于组织同步与发消息）
- 组织：/directory/searchdepartments、/directory/departmentrecipients、/person/queryuser
- 外部流程：/works/new（带表单新建）、/works/turn（一键转交）、/works/back（退回）
  注意：只有在 OA 流程设计中标记为「外部流程」的流程才允许 API 操作（非外部流程返回 -999）。
- 站内消息：通达 restapi 默认未开放，需 OA 厂商按 docs/oa-integration.md 部署 sms 扩展（路径可配）。
返回信封统一为 {"errcode":0|1,"errmsg":..,"data":..}，errcode != 0 即业务失败。
"""
import json
import logging
import time
import urllib.parse

import httpx

from ...config import get_settings
from .base import OAClient, OAOrg, OAUserInfo

log = logging.getLogger("qms.oa.tongda")


class OAError(Exception):
    pass


class TongdaOAClient(OAClient):
    mode = "tongda"

    def __init__(self):
        self.st = get_settings()
        self.base = self.st.oa_base_url.rstrip("/")
        self.api_root = self.base + self.st.oa_api_root
        self._svc_token: tuple[str, float] | None = None
        self.http = httpx.Client(timeout=15)

    # ---------------- OAuth2 ----------------
    def authorize_url(self, state, redirect_uri):
        q = urllib.parse.urlencode({
            "response_type": "code", "client_id": self.st.oa_client_id, "redirect_uri": redirect_uri, "state": state,
        })
        return f"{self.base}/oauth2/authorize?{q}"

    def exchange_code(self, code, redirect_uri):
        r = self.http.post(f"{self.base}/oauth2/token", data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "client_id": self.st.oa_client_id, "client_secret": self.st.oa_client_secret,
        })
        r.raise_for_status()
        data = r.json()
        if "access_token" not in data:
            raise OAError(f"OA 授权失败：{data}")
        return data

    def userinfo(self, access_token):
        r = self.http.get(f"{self.base}/oauth2/userinfo", headers={"Authorization": f"Bearer {access_token}"})
        r.raise_for_status()
        d = r.json()
        d = d.get("data", d)
        return OAUserInfo(
            user_id=str(d.get("user_id") or d.get("USER_ID")), user_name=d.get("user_name") or d.get("USER_NAME"),
            uid=d.get("uid") or d.get("UID"), dept_id=d.get("dept_id"), dept_name=d.get("dept_name"),
            priv_name=d.get("priv_name"), byname=d.get("byname"), priv_id=d.get("user_priv"),
        )

    def _service_token(self) -> str:
        if self._svc_token and self._svc_token[1] > time.time() + 30:
            return self._svc_token[0]
        r = self.http.post(f"{self.base}/oauth2/token", data={
            "grant_type": "client_credentials", "client_id": self.st.oa_client_id,
            "client_secret": self.st.oa_client_secret,
        })
        r.raise_for_status()
        d = r.json()
        self._svc_token = (d["access_token"], time.time() + int(d.get("expires_in", 1200)))
        return self._svc_token[0]

    def _call(self, method: str, path: str, **params):
        headers = {"Authorization": f"Bearer {self._service_token()}"}
        url = self.api_root + path
        if method == "GET":
            r = self.http.get(url, params=params, headers=headers)
        else:
            r = self.http.post(url, data=params, headers=headers)
        r.raise_for_status()
        body = r.json()
        if body.get("errcode") not in (0, "0"):
            raise OAError(f"{path} 失败：{body.get('data') or body.get('errmsg')}")
        return body.get("data")

    # ---------------- 组织同步 ----------------
    def fetch_org(self):
        svc = self.st.oa_service_user
        depts_raw = self._call("POST", "/directory/searchdepartments", user_id=svc, keyword="", limit=100)
        depts = [
            {"dept_id": int(x["dept_id"]), "dept_name": x["dept_name"], "parent_id": int(x.get("dept_parent") or 0)}
            for x in (depts_raw or {}).get("departments", [])
        ]
        users: dict[str, dict] = {}
        for dp in depts:
            if dp["parent_id"] != 0:
                continue
            res = self._call("POST", "/directory/departmentrecipients", user_id=svc, dept_id=dp["dept_id"],
                             include_children=1, include_secondary_dept=0)
            for u in (res or {}).get("recipients", []):
                users[u["user_id"]] = {
                    "user_id": u["user_id"], "user_name": u["user_name"], "dept_id": int(u.get("dept_id") or 0),
                    "priv_name": u.get("priv_name"),
                }
        return OAOrg(depts=depts, users=list(users.values()))

    # ---------------- 外部流程镜像 ----------------
    @staticmethod
    def _user_info(op: OAUserInfo) -> str:
        return json.dumps({
            "LOGIN_UID": op.uid, "LOGIN_USER_ID": op.user_id, "LOGIN_USER_NAME": op.user_name,
            "LOGIN_DEPT_ID": op.dept_id, "LOGIN_USER_PRIV": op.priv_id, "LOGIN_DEPT_ID_OTHER": "",
            "LOGIN_USER_PRIV_OTHER": "",
        }, ensure_ascii=False)

    def start_flow(self, flow_id, run_name, form_data, operator):
        data = self._call("POST", "/works/new", flowId=flow_id, userId=operator.user_id,
                          postData=json.dumps({"run_name": run_name, **form_data}, ensure_ascii=False))
        run_id = data.get("runId") if isinstance(data, dict) else data
        return int(run_id) if run_id else None

    def turn_flow(self, flow_id, run_id, operator, to_users):
        info = self._call("GET", "/works/workruninfo", runId=run_id, columns="FLOW_PRCS,PRCS_ID")
        self._call("POST", "/works/turn", flowId=flow_id, runId=run_id, flowPrcs=info.get("FLOW_PRCS"),
                   prcsId=info.get("PRCS_ID"), userInfo=self._user_info(operator))
        return True

    def back_flow(self, flow_id, run_id, operator):
        info = self._call("GET", "/works/workruninfo", runId=run_id, columns="FLOW_PRCS,PRCS_ID")
        nodes = self._call("POST", "/works/backnodes", flowId=flow_id, runId=run_id, flowPrcs=info.get("FLOW_PRCS"),
                           prcsId=info.get("PRCS_ID"), userInfo=self._user_info(operator))
        to_prcs = (nodes or [{}])[0].get("prcs_id") if isinstance(nodes, list) else None
        self._call("POST", "/works/back", flowId=flow_id, runId=run_id, flowPrcs=info.get("FLOW_PRCS"),
                   prcsId=info.get("PRCS_ID"), toPrcs=to_prcs, userInfo=self._user_info(operator))
        return True

    # ---------------- 消息 ----------------
    def send_message(self, to_user_ids, title, content, url, push_app):
        self._call("POST", self.st.oa_msg_path, user_id=self.st.oa_service_user, to_id=",".join(to_user_ids),
                   content=f"{title}\n{content}", remind_url=url, sms_type="0", push=1 if push_app else 0)
        return True

    def oa_page_url(self, path):
        return f"{self.base}{path}"
