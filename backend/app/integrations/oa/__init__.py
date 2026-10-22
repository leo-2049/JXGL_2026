from functools import lru_cache

from ...config import get_settings
from .base import OAClient, OAUserInfo


@lru_cache
def get_oa() -> OAClient:
    if get_settings().oa_mode == "tongda":
        from .tongda import TongdaOAClient

        return TongdaOAClient()
    from .mock import MockOAClient

    return MockOAClient()


__all__ = ["get_oa", "OAClient", "OAUserInfo"]
