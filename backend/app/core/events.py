"""模块间联动的轻量事件总线（同一事务内同步执行）。

例：文件发行 → 工程变更「文件更新」事项完成；实验报告签发 → 唤醒待实验结果的检验单；
控制计划发行 → 项目任务自动完成。
"""
from collections import defaultdict
from typing import Callable

_handlers: dict[str, list[Callable]] = defaultdict(list)


def on(name: str):
    def deco(fn):
        _handlers[name].append(fn)
        return fn

    return deco


def emit(name: str, db, **kw):
    for fn in _handlers.get(name, []):
        fn(db, **kw)
