"""模型注册表：按类名或表名查模型（接口回写、附件关联等通用逻辑使用）。"""
from ..db import Base


class _Models(dict):
    def get(self, key, default=None):  # noqa: D401
        if not len(self):
            for m in Base.registry.mappers:
                cls = m.class_
                self[cls.__name__] = cls
                self[cls.__tablename__] = cls
        return super().get(key, default)


MODELS = _Models()
