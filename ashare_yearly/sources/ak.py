"""akshare 优先适配层。

akshare 各版本函数名/参数有差异，这里为每个能力维护一组“候选函数 + 候选参数”，
按顺序尝试，全部失败则抛 ``SourceUnavailable``，由调用方转入兜底源。
所有尝试都记录在 ``AkAdapter.attempts`` 里，最终写入 manifest.json，保证可追溯。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import pandas as pd

from .. import codes as codeutil
from ..frames import normalize_minute, normalize_ohlc

Candidate = tuple[str, dict]


class SourceUnavailable(RuntimeError):
    """该能力在当前数据源上不可用。"""


@dataclass
class Attempt:
    """一次 akshare 调用尝试的记录。"""

    capability: str
    func: str
    ok: bool
    rows: int | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "func": self.func,
            "ok": self.ok,
            "rows": self.rows,
            "error": self.error,
        }


class AkAdapter:
    """akshare 封装。``available=False`` 时所有调用直接抛 ``SourceUnavailable``。"""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.module: Any = None
        self.import_error: str | None = None
        self.attempts: list[Attempt] = []
        if not enabled:
            self.import_error = "已通过 --no-akshare 禁用"
            return
        try:
            import akshare  # noqa: PLC0415 - 延迟导入，缺失时不影响其他功能

            self.module = akshare
        except Exception as exc:  # noqa: BLE001
            self.import_error = f"{type(exc).__name__}: {exc}"

    # ---------------- 基础能力 ----------------
    @property
    def available(self) -> bool:
        return self.module is not None

    @property
    def version(self) -> str | None:
        return getattr(self.module, "__version__", None) if self.module else None

    def has(self, name: str) -> bool:
        return bool(self.module) and callable(getattr(self.module, name, None))

    def info(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "available": self.available,
            "version": self.version,
            "import_error": self.import_error,
            "attempts": [a.as_dict() for a in self.attempts],
        }

    def try_call(self, capability: str, candidates: Sequence[Candidate], allow_empty: bool = False) -> Any:
        """按顺序尝试候选函数，返回第一个成功且非空的结果。"""
        if not self.available:
            raise SourceUnavailable(f"akshare 不可用（{self.import_error or '未安装'}）")
        errors: list[str] = []
        for name, kwargs in candidates:
            func = getattr(self.module, name, None)
            if not callable(func):
                self.attempts.append(Attempt(capability, name, False, error="当前 akshare 版本无此函数"))
                errors.append(f"{name}: 函数不存在")
                continue
            try:
                result = func(**kwargs)
            except Exception as exc:  # noqa: BLE001 - akshare 异常类型不可预知
                message = f"{type(exc).__name__}: {exc}"[:300]
                self.attempts.append(Attempt(capability, name, False, error=message))
                errors.append(f"{name}: {message}")
                continue
            rows = len(result) if hasattr(result, "__len__") else None
            empty = result is None or (rows == 0)
            if empty and not allow_empty:
                self.attempts.append(Attempt(capability, name, False, rows=0, error="返回空数据"))
                errors.append(f"{name}: 空数据")
                continue
            self.attempts.append(Attempt(capability, name, True, rows=rows))
            return result
        raise SourceUnavailable(f"{capability} 在 akshare 上全部尝试失败：" + " | ".join(errors))

    # ---------------- 指数与个股行情 ----------------
    def index_hist(self, code: str, ak_symbol: str, start_ymd: str, end_ymd: str) -> pd.DataFrame:
        df = self.try_call(
            f"index_hist:{code}",
            [
                (
                    "index_zh_a_hist",
                    {"symbol": code, "period": "daily", "start_date": start_ymd, "end_date": end_ymd},
                ),
                ("stock_zh_index_daily_em", {"symbol": ak_symbol, "start_date": start_ymd, "end_date": end_ymd}),
                ("stock_zh_index_daily_em", {"symbol": ak_symbol}),
                ("stock_zh_index_daily", {"symbol": ak_symbol}),
            ],
        )
        return normalize_ohlc(df)

    def stock_hist(self, code: str, start_ymd: str, end_ymd: str, adjust: str = "qfq") -> pd.DataFrame:
        c = codeutil.normalize(code)
        df = self.try_call(
            f"stock_hist:{c}",
            [
                (
                    "stock_zh_a_hist",
                    {
                        "symbol": c,
                        "period": "daily",
                        "start_date": start_ymd,
                        "end_date": end_ymd,
                        "adjust": adjust,
                    },
                ),
                ("stock_zh_a_daily", {"symbol": codeutil.prefixed(c), "adjust": adjust}),
                ("stock_bj_a_hist", {
                    "symbol": c,
                    "period": "daily",
                    "start_date": start_ymd,
                    "end_date": end_ymd,
                    "adjust": adjust,
                }),
            ],
        )
        return normalize_ohlc(df)

    def stock_minute(self, code: str, start_dt: str, end_dt: str, period: str = "1") -> pd.DataFrame:
        """分钟级行情。``start_dt``/``end_dt`` 格式 ``YYYY-MM-DD HH:MM:SS``。"""
        c = codeutil.normalize(code)
        df = self.try_call(
            f"stock_minute:{c}:{period}",
            [
                (
                    "stock_zh_a_hist_min_em",
                    {
                        "symbol": c,
                        "start_date": start_dt,
                        "end_date": end_dt,
                        "period": period,
                        "adjust": "",
                    },
                ),
                ("stock_zh_a_minute", {"symbol": codeutil.prefixed(c), "period": period, "adjust": ""}),
            ],
        )
        return normalize_minute(df)

    # ---------------- 快照与新股 ----------------
    def spot_all(self) -> pd.DataFrame:
        return self.try_call(
            "spot_all",
            [
                ("stock_zh_a_spot_em", {}),
                ("stock_zh_a_spot", {}),
            ],
        )

    def ipo_list(self) -> pd.DataFrame:
        return self.try_call(
            "ipo_list",
            [
                ("stock_xgsglb_em", {"symbol": "全部股票"}),
                ("stock_xgsglb_em", {}),
                ("stock_zh_a_new", {}),
                ("stock_new_a_spot_em", {}),
            ],
        )

    def individual_info(self, code: str) -> pd.DataFrame:
        c = codeutil.normalize(code)
        return self.try_call(
            f"individual_info:{c}",
            [
                ("stock_individual_info_em", {"symbol": c}),
                ("stock_individual_basic_info_xq", {"symbol": codeutil.prefixed(c, upper=True)}),
            ],
        )

    # ---------------- 股东、主营、估值 ----------------
    def free_top10_holders(self, code: str, report_date: str | None = None) -> pd.DataFrame:
        """前十大流通股东。``report_date`` 为 ``YYYYMMDD`` 报告期。"""
        c = codeutil.normalize(code)
        symbol_lower = codeutil.prefixed(c)
        candidates: list[Candidate] = []
        if report_date:
            candidates.append(("stock_gdfx_free_top_10_em", {"symbol": symbol_lower, "date": report_date}))
        candidates.extend(
            [
                ("stock_gdfx_free_top_10_em", {"symbol": symbol_lower}),
                ("stock_gdfx_free_holding_detail_em", {"date": report_date or ""}),
            ]
        )
        return self.try_call(f"free_top10:{c}", candidates)

    def top10_holders(self, code: str, report_date: str | None = None) -> pd.DataFrame:
        """前十大股东（含限售），仅在流通股东取不到时作参考。"""
        c = codeutil.normalize(code)
        candidates: list[Candidate] = []
        if report_date:
            candidates.append(("stock_gdfx_top_10_em", {"symbol": codeutil.prefixed(c), "date": report_date}))
        candidates.append(("stock_gdfx_top_10_em", {"symbol": codeutil.prefixed(c)}))
        return self.try_call(f"top10:{c}", candidates)

    def main_business(self, code: str) -> pd.DataFrame:
        c = codeutil.normalize(code)
        return self.try_call(
            f"main_business:{c}",
            [
                ("stock_zyjs_ths", {"symbol": c}),
                ("stock_zygc_em", {"symbol": codeutil.prefixed(c, upper=True)}),
                ("stock_zygc_ym", {"symbol": c}),
            ],
        )

    def valuation_hist(self, code: str) -> pd.DataFrame:
        """估值历史（PE/PE-TTM/PB/总市值）。"""
        c = codeutil.normalize(code)
        return self.try_call(
            f"valuation_hist:{c}",
            [
                ("stock_a_indicator_lg", {"symbol": c}),
                ("stock_zh_valuation_baidu", {"symbol": c, "indicator": "市盈率(TTM)", "period": "近一年"}),
            ],
        )

    # ---------------- 板块 ----------------
    def industry_boards(self) -> pd.DataFrame:
        return self.try_call(
            "industry_boards",
            [
                ("stock_board_industry_name_em", {}),
                ("stock_board_industry_summary_ths", {}),
            ],
        )

    def industry_board_members(self, board_name: str) -> pd.DataFrame:
        return self.try_call(
            f"industry_members:{board_name}",
            [
                ("stock_board_industry_cons_em", {"symbol": board_name}),
                ("stock_board_industry_cons_ths", {"symbol": board_name}),
            ],
        )

    def industry_board_hist(self, board_name: str, start_ymd: str, end_ymd: str) -> pd.DataFrame:
        df = self.try_call(
            f"industry_hist:{board_name}",
            [
                (
                    "stock_board_industry_hist_em",
                    {
                        "symbol": board_name,
                        "start_date": start_ymd,
                        "end_date": end_ymd,
                        "period": "日k",
                        "adjust": "",
                    },
                ),
                (
                    "stock_board_industry_hist_em",
                    {
                        "symbol": board_name,
                        "start_date": start_ymd,
                        "end_date": end_ymd,
                        "period": "daily",
                        "adjust": "",
                    },
                ),
                ("stock_board_industry_index_ths", {"symbol": board_name, "start_date": start_ymd, "end_date": end_ymd}),
            ],
        )
        return normalize_ohlc(df)

    def concept_boards(self) -> pd.DataFrame:
        return self.try_call(
            "concept_boards",
            [
                ("stock_board_concept_name_em", {}),
                ("stock_board_concept_name_ths", {}),
            ],
        )

    # ---------------- 热点与评论 ----------------
    def stock_comment(self) -> pd.DataFrame:
        """东方财富千股千评（综合得分/机构参与度/关注指数）。"""
        return self.try_call("stock_comment_em", [("stock_comment_em", {})])

    def stock_news(self, code: str) -> pd.DataFrame:
        c = codeutil.normalize(code)
        return self.try_call(f"stock_news:{c}", [("stock_news_em", {"symbol": c})])

    def hot_rank(self) -> pd.DataFrame:
        """东财人气榜。"""
        return self.try_call(
            "hot_rank",
            [
                ("stock_hot_rank_em", {}),
                ("stock_hot_up_em", {}),
            ],
        )

    def em_hot_keywords(self) -> pd.DataFrame:
        return self.try_call("em_hot_keyword", [("stock_hot_keyword_em", {"symbol": "全部"}), ("stock_hot_search_baidu", {})])

    def ths_news(self) -> pd.DataFrame:
        """同花顺快讯/全球财经直播。"""
        return self.try_call(
            "ths_news",
            [
                ("stock_info_global_ths", {}),
                ("stock_info_cjzc_em", {}),
            ],
        )

    def ths_hot_rank(self) -> pd.DataFrame:
        """同花顺（问财）热门股票排名 / 概念资金流。"""
        return self.try_call(
            "ths_hot",
            [
                ("stock_hot_rank_wc", {}),
                ("stock_fund_flow_concept", {"symbol": "即时"}),
                ("stock_board_concept_summary_ths", {}),
            ],
        )


@dataclass
class ProbeResult:
    """``--self-check`` 的单项探测结果。"""

    capability: str
    ok: bool
    detail: str = ""
    rows: int | None = None
    extras: dict[str, Any] = field(default_factory=dict)
