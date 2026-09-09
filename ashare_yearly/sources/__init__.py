"""数据源适配层：akshare 优先，不可用时兜底东方财富 / 同花顺 公开接口。"""

from .ak import AkAdapter, Attempt, SourceUnavailable

__all__ = ["AkAdapter", "Attempt", "SourceUnavailable"]
