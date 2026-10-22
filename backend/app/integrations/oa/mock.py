"""本地模拟 OA：开发、演示、自动化测试使用。组织取平台已有的 qms_sys_user / qms_sys_dept。"""
import itertools
import logging

from .base import OAClient, OAOrg, OAUserInfo

log = logging.getLogger("qms.oa.mock")
_run_seq = itertools.count(90001)


class MockOAClient(OAClient):
    mode = "mock"

    def __init__(self):
        self.sent: list[dict] = []  # 测试断言用

    def authorize_url(self, state, redirect_uri):
        return f"/api/auth/dev-authorize?state={state}&redirect_uri={redirect_uri}"

    def exchange_code(self, code, redirect_uri):
        return {"access_token": f"mock-{code}", "user_id": code}

    def userinfo(self, access_token):
        uid = access_token.removeprefix("mock-")
        return OAUserInfo(user_id=uid, user_name=uid)

    def fetch_org(self):
        return OAOrg()

    def start_flow(self, flow_id, run_name, form_data, operator):
        run_id = next(_run_seq)
        log.info("mock start_flow %s %s -> %s", flow_id, run_name, run_id)
        return run_id

    def turn_flow(self, flow_id, run_id, operator, to_users):
        return True

    def back_flow(self, flow_id, run_id, operator):
        return True

    def send_message(self, to_user_ids, title, content, url, push_app):
        self.sent.append({"to": to_user_ids, "title": title, "content": content, "url": url, "app": push_app})
        log.info("mock msg -> %s: %s", to_user_ids, title)
        return True
