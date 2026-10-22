"""统一错误：提示语格式「【字段名】问题 + 怎么改」（报告 5.0 约定 5）。"""
from fastapi import Request
from fastapi.responses import JSONResponse


class BizError(Exception):
    def __init__(self, message: str | list[str], field: str | None = None, status_code: int = 400):
        if isinstance(message, list):
            self.messages = message
        else:
            self.messages = [message]
        self.field = field
        self.status_code = status_code
        super().__init__("；".join(self.messages))


class Checker:
    """整单校验：收集全部问题后一次抛出，定位到字段。"""

    def __init__(self):
        self.errors: list[dict] = []

    def add(self, field: str, msg: str):
        self.errors.append({"field": field, "message": msg})
        return self

    def check(self, cond: bool, field: str, msg: str):
        if not cond:
            self.add(field, msg)
        return self

    def raise_if_any(self):
        if self.errors:
            raise ValidationFailed(self.errors)


class ValidationFailed(BizError):
    def __init__(self, errors: list[dict]):
        super().__init__([e["message"] for e in errors])
        self.errors = errors


class Forbidden(BizError):
    def __init__(self, message="您没有此操作权限"):
        super().__init__(message, status_code=403)


class Conflict(BizError):
    def __init__(self, message="单据已被他人修改，请刷新后重试"):
        super().__init__(message, status_code=409)


class NotFound(BizError):
    def __init__(self, message="单据不存在或已删除"):
        super().__init__(message, status_code=404)


async def biz_error_handler(_: Request, exc: BizError):
    body = {"ok": False, "message": "；".join(exc.messages), "messages": exc.messages}
    if isinstance(exc, ValidationFailed):
        body["errors"] = exc.errors
    return JSONResponse(status_code=exc.status_code, content=body)
