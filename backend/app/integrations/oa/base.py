"""通达 OA 底座适配接口。

平台把通达 OA 当作底座使用以下五项能力：
1. 统一身份：OAuth2 授权码登录（/oauth2/authorize → /oauth2/token → /oauth2/userinfo），平台不另设密码；
2. 组织架构：部门、人员、角色从 OA 同步，业务里「人员 / 部门」字段全部从 OA 组织选择；
3. 流程实例：每张单据在 OA 流程中心镜像一个「外部流程」实例（/works/new、/works/turn、/works/back），
   RUN_ID 写回业务表 flow_run_id，OA 待办列表可见、可点开直达平台办理页；
4. 消息：站内消息（必发）+ 移动端推送，消息带「打开单据」链接；
5. 门户：平台页面挂到 OA 菜单（应用中心 / 自定义菜单），从 OA 进入即免登。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class OAUserInfo:
    user_id: str
    user_name: str
    uid: int | None = None
    dept_id: int | None = None
    dept_name: str | None = None
    priv_name: str | None = None
    byname: str | None = None
    priv_id: int | None = None


@dataclass
class OAOrg:
    depts: list[dict] = field(default_factory=list)  # {dept_id, dept_name, parent_id}
    users: list[dict] = field(default_factory=list)  # {user_id, user_name, uid, dept_id, byname, priv_name}


class OAClient(ABC):
    mode: str = "base"

    # ---- 身份 ----
    @abstractmethod
    def authorize_url(self, state: str, redirect_uri: str) -> str: ...

    @abstractmethod
    def exchange_code(self, code: str, redirect_uri: str) -> dict: ...

    @abstractmethod
    def userinfo(self, access_token: str) -> OAUserInfo: ...

    # ---- 组织 ----
    @abstractmethod
    def fetch_org(self) -> OAOrg: ...

    # ---- 流程镜像 ----
    @abstractmethod
    def start_flow(self, flow_id: int, run_name: str, form_data: dict, operator: OAUserInfo) -> int | None: ...

    @abstractmethod
    def turn_flow(self, flow_id: int, run_id: int, operator: OAUserInfo, to_users: list[str]) -> bool: ...

    @abstractmethod
    def back_flow(self, flow_id: int, run_id: int, operator: OAUserInfo) -> bool: ...

    # ---- 消息 ----
    @abstractmethod
    def send_message(self, to_user_ids: list[str], title: str, content: str, url: str, push_app: bool) -> bool: ...

    def oa_page_url(self, path: str) -> str:
        return path
