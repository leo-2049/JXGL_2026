"""运行配置。全部可用环境变量（前缀 QMS_）或 backend/.env 覆盖。"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QMS_", env_file=str(BASE_DIR / ".env"), extra="ignore")

    app_name: str = "顺翼质量协同平台"
    # 生产：mysql+pymysql://qms:***@127.0.0.1:3336/td_qms?charset=utf8mb4（与通达OA共用 MySQL 实例，独立库）
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'qms.db'}"
    secret_key: str = "change-me-in-production"
    token_expire_minutes: int = 60 * 12
    upload_dir: str = str(BASE_DIR / "data" / "uploads")
    max_upload_mb: int = 50
    # 平台对外访问根地址（用于消息里的「打开单据」链接、OAuth 回调）
    public_base_url: str = "http://127.0.0.1:8000"
    frontend_base_url: str = "http://127.0.0.1:5173"

    # ---- 通达 OA 底座 ----
    # mock：本地模拟（开发 / 演示）；tongda：对接真实通达 OA restapi + oauth2
    oa_mode: str = "mock"
    oa_base_url: str = ""  # 如 http://192.168.1.10
    oa_client_id: str = ""
    oa_client_secret: str = ""
    oa_api_root: str = "/restapi/1.0"
    # 服务账号（client_credentials）用于组织同步、发消息；为空则跳过
    oa_service_user: str = "admin_qms"
    # 站内消息接口路径（通达 restapi 扩展，部署时由 OA 厂商开放，见 docs/oa-integration.md）
    oa_msg_path: str = "/sms/send"
    # 流程镜像：在 OA 流程中心为每张单据建一个「外部流程」实例，RUN_ID 写回 flow_run_id
    oa_flow_mirror: bool = False
    # biz_type -> OA 外部流程 flowId，JSON 字符串，如 {"nc":101,"ecn":102}
    oa_flow_ids: str = "{}"

    # ---- 鼎捷 ERP ----
    erp_mode: str = "mock"  # mock / middb / webapi
    erp_base_url: str = ""
    erp_token: str = ""

    # 后台定时任务（超时提醒、巡检任务生成、接口重试）
    scheduler_enabled: bool = True
    scheduler_interval_seconds: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
