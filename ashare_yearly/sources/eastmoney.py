"""东方财富公开接口兜底层。

akshare 不可用/报错时使用。字段含义按东财行情接口惯例：
f2 最新价、f3 涨跌幅、f5 成交量(手)、f6 成交额、f8 换手率、f9 市盈率(动态)、
f10 量比、f12 代码、f13 市场、f14 名称、f20 总市值、f21 流通市值、f23 市净率、
f26 上市日期、f100 所属行业、f114 市盈率(静态)、f115 市盈率(TTM)。
字段定义属于第三方未公开文档的约定，可能变动；报告中会标注数据来源。
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from .. import codes as codeutil
from ..frames import normalize_minute, normalize_ohlc, row_value, safe_float, ymd
from ..netutil import FetchError, Http

CLIST = "https://push2.eastmoney.com/api/qt/clist/get"
KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
TRENDS = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
QUOTE = "https://push2.eastmoney.com/api/qt/stock/get"
DATACENTER = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
DATACENTER_WEB = "https://datacenter-web.eastmoney.com/api/data/v1/get"
SEARCH = "https://search-api-web.eastmoney.com/search/jsonp"
F10_HOSTS = (
    "https://emweb.securities.eastmoney.com",
    "https://emweb.eastmoney.com",
)

# 沪深京 A 股（含科创/创业/北交所）
FS_A_SHARE = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048"
FS_INDUSTRY_BOARD = "m:90 t:2 f:!50"
SPOT_FIELDS = "f2,f3,f5,f6,f8,f9,f10,f12,f13,f14,f20,f21,f23,f26,f100,f114,f115"

SPOT_RENAME = {
    "f12": "code",
    "f14": "name",
    "f2": "price",
    "f3": "pct_chg",
    "f5": "volume",
    "f6": "amount",
    "f8": "turnover",
    "f9": "pe_dynamic",
    "f10": "volume_ratio",
    "f20": "total_mv",
    "f21": "float_mv",
    "f23": "pb",
    "f26": "list_date",
    "f100": "industry",
    "f114": "pe_static",
    "f115": "pe_ttm",
}

KLINE_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
KLINE_COLUMNS = [
    "date",
    "open",
    "close",
    "high",
    "low",
    "volume",
    "amount",
    "amplitude",
    "pct_chg",
    "change",
    "turnover",
]


def _clean(value: Any) -> Any:
    if value in ("-", "", None):
        return None
    return value


# ---------------- 快照 ----------------
def spot_all(http: Http, page_size: int = 100, max_pages: int = 80) -> pd.DataFrame:
    """全市场 A 股快照（含市值/流通市值/PE/换手率/上市日期）。"""
    rows: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        payload = http.get_json(
            CLIST,
            {
                "pn": page,
                "pz": page_size,
                "po": 0,
                "np": 1,
                "fltt": 2,
                "invt": 2,
                "fid": "f12",
                "fs": FS_A_SHARE,
                "fields": SPOT_FIELDS,
            },
            referer="https://quote.eastmoney.com/",
        )
        data = (payload or {}).get("data") or {}
        diff = data.get("diff") or []
        if isinstance(diff, dict):  # 旧接口会返回 dict
            diff = list(diff.values())
        if not diff:
            break
        rows.extend(diff)
        total = data.get("total") or 0
        if total and len(rows) >= total:
            break
    if not rows:
        raise FetchError("东财快照接口未返回数据")
    df = pd.DataFrame(rows).rename(columns=SPOT_RENAME)
    keep = [c for c in SPOT_RENAME.values() if c in df.columns]
    df = df[keep].copy()
    df["code"] = df["code"].astype(str).str.zfill(6)
    for col in ("price", "pct_chg", "volume", "amount", "turnover", "pe_dynamic", "pe_static", "pe_ttm", "pb", "total_mv", "float_mv", "volume_ratio"):
        if col in df.columns:
            df[col] = df[col].map(safe_float)
    if "list_date" in df.columns:
        df["list_date"] = df["list_date"].map(lambda v: ymd(v) if _clean(v) else None)
    return df.reset_index(drop=True)


def quote(http: Http, secid: str) -> dict[str, Any]:
    payload = http.get_json(
        QUOTE,
        {
            "secid": secid,
            "invt": 2,
            "fltt": 2,
            "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f116,f117,f162,f163,f164,f168,f169,f170,f173",
        },
        referer="https://quote.eastmoney.com/",
    )
    return (payload or {}).get("data") or {}


# ---------------- K 线 / 分时 ----------------
def kline(
    http: Http,
    secid: str,
    start_ymd: str,
    end_ymd: str,
    *,
    klt: int = 101,
    fqt: int = 1,
    limit: int = 10000,
) -> pd.DataFrame:
    """K 线。``klt``: 1/5/15/30/60 分钟，101 日，102 周；``fqt``: 0 不复权、1 前复权、2 后复权。"""
    payload = http.get_json(
        KLINE,
        {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": KLINE_FIELDS2,
            "klt": klt,
            "fqt": fqt,
            "beg": start_ymd,
            "end": end_ymd,
            "lmt": limit,
        },
        referer="https://quote.eastmoney.com/",
    )
    data = (payload or {}).get("data") or {}
    lines = data.get("klines") or []
    if not lines:
        raise FetchError(f"东财 K 线无数据 secid={secid} klt={klt}")
    rows = [dict(zip(KLINE_COLUMNS, line.split(","))) for line in lines]
    df = pd.DataFrame(rows)
    if klt < 101:
        df = df.rename(columns={"date": "time"})
        return normalize_minute(df)
    return normalize_ohlc(df)


def trends(http: Http, secid: str, ndays: int = 1) -> pd.DataFrame:
    """分时图数据（仅最近 1~5 个交易日）。"""
    payload = http.get_json(
        TRENDS,
        {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8",
            "fields2": "f51,f53,f56,f58",
            "iscr": 0,
            "iscca": 0,
            "ndays": max(1, min(5, ndays)),
        },
        referer="https://quote.eastmoney.com/",
    )
    data = (payload or {}).get("data") or {}
    lines = data.get("trends") or []
    if not lines:
        raise FetchError(f"东财分时接口无数据 secid={secid}")
    rows = [dict(zip(["time", "close", "volume", "avg"], line.split(","))) for line in lines]
    return normalize_minute(pd.DataFrame(rows))


# ---------------- F10：流通股东 / 主营 ----------------
def free_top10_holders(http: Http, code: str) -> tuple[pd.DataFrame, str | None]:
    """前十大流通股东，返回 (明细, 报告期)。"""
    c = codeutil.normalize(code)
    errors: list[str] = []

    # 1) 数据中心 API
    for base in (DATACENTER, DATACENTER_WEB):
        try:
            payload = http.get_json(
                base,
                {
                    "reportName": "RPT_F10_EH_FREEHOLDERS",
                    "columns": "ALL",
                    "quoteColumns": "",
                    "filter": f'(SECUCODE="{codeutil.dotted(c)}")',
                    "pageNumber": 1,
                    "pageSize": 10,
                    "sortColumns": "END_DATE,HOLDER_RANK",
                    "sortTypes": "-1,1",
                    "source": "HSF10",
                    "client": "PC",
                },
                referer="https://emweb.securities.eastmoney.com/",
            )
            rows = ((payload or {}).get("result") or {}).get("data") or []
            if rows:
                return _normalize_holders(rows), ymd(row_value(rows[0], ["END_DATE", "截止日期"]))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{base}: {exc}"[:200])

    # 2) F10 PageAjax
    for host in F10_HOSTS:
        try:
            payload = http.get_json(
                f"{host}/PC_HSF10/ShareholderResearch/PageAjax",
                {"code": codeutil.prefixed(c, upper=True)},
                referer=f"{host}/",
            )
            rows = (payload or {}).get("sdltgd") or (payload or {}).get("sdgd") or []
            if rows:
                return _normalize_holders(rows), ymd(row_value(rows[0], ["END_DATE", "RIQI", "截止日期"]))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{host}: {exc}"[:200])

    raise FetchError("东财流通股东接口全部失败：" + " | ".join(errors))


def _normalize_holders(rows: list[dict[str, Any]]) -> pd.DataFrame:
    out = []
    for idx, raw in enumerate(rows, start=1):
        out.append(
            {
                "rank": safe_float(row_value(raw, ["HOLDER_RANK", "名次"])) or idx,
                "holder": row_value(raw, ["HOLDER_NAME", "股东名称"]),
                "shares": safe_float(row_value(raw, ["HOLD_NUM", "持股数", "持股数量"])),
                "ratio": safe_float(row_value(raw, ["FREE_HOLDNUM_RATIO", "HOLD_NUM_RATIO", "占总流通股本持股比例", "占总股本持股比例"])),
                "change": row_value(raw, ["HOLD_NUM_CHANGE", "增减", "变动比例"]),
                "holder_type": row_value(raw, ["HOLDER_TYPE", "股东性质", "股份类型"]),
                "end_date": ymd(row_value(raw, ["END_DATE", "截止日期"]) or ""),
            }
        )
    return pd.DataFrame(out)


def business_profile(http: Http, code: str) -> dict[str, Any]:
    """主营业务 / 经营范围 / 行业。"""
    c = codeutil.normalize(code)
    errors: list[str] = []

    for base in (DATACENTER, DATACENTER_WEB):
        try:
            payload = http.get_json(
                base,
                {
                    "reportName": "RPT_F10_BASIC_ORGINFO",
                    "columns": "ALL",
                    "filter": f'(SECUCODE="{codeutil.dotted(c)}")',
                    "pageNumber": 1,
                    "pageSize": 1,
                    "source": "HSF10",
                    "client": "PC",
                },
                referer="https://emweb.securities.eastmoney.com/",
            )
            rows = ((payload or {}).get("result") or {}).get("data") or []
            if rows:
                raw = rows[0]
                return {
                    "main_business": row_value(raw, ["MAIN_BUSINESS", "BUSINESS_SCOPE", "ORG_PROFILE"]),
                    "business_scope": row_value(raw, ["BUSINESS_SCOPE"]),
                    "industry": row_value(raw, ["EM2016", "INDUSTRYCSRC1", "行业"]),
                    "source": "eastmoney:RPT_F10_BASIC_ORGINFO",
                }
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{base}: {exc}"[:200])

    for host in F10_HOSTS:
        try:
            payload = http.get_json(
                f"{host}/PC_HSF10/BusinessAnalysis/PageAjax",
                {"code": codeutil.prefixed(c, upper=True)},
                referer=f"{host}/",
            )
            blocks = (payload or {}).get("zyfw") or (payload or {}).get("jyps") or []
            if blocks:
                raw = blocks[0]
                return {
                    "main_business": row_value(raw, ["ZYFW", "MAIN_BUSINESS", "主营范围", "经营评述"]),
                    "business_scope": row_value(raw, ["ZYFW", "BUSINESS_SCOPE"]),
                    "industry": None,
                    "source": f"eastmoney:{host}/BusinessAnalysis",
                }
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{host}: {exc}"[:200])

    raise FetchError("东财主营业务接口全部失败：" + " | ".join(errors))


# ---------------- 板块 ----------------
def industry_boards(http: Http) -> pd.DataFrame:
    payload = http.get_json(
        CLIST,
        {
            "pn": 1,
            "pz": 500,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": FS_INDUSTRY_BOARD,
            "fields": "f12,f13,f14,f2,f3,f104,f105,f136",
        },
        referer="https://quote.eastmoney.com/center/boardlist.html",
    )
    diff = ((payload or {}).get("data") or {}).get("diff") or []
    if isinstance(diff, dict):
        diff = list(diff.values())
    if not diff:
        raise FetchError("东财行业板块列表无数据")
    df = pd.DataFrame(diff).rename(columns={"f12": "board_code", "f14": "board_name", "f2": "price", "f3": "pct_chg"})
    return df[[c for c in ("board_code", "board_name", "price", "pct_chg") if c in df.columns]]


def board_members(http: Http, board_code: str) -> pd.DataFrame:
    payload = http.get_json(
        CLIST,
        {
            "pn": 1,
            "pz": 1000,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": f"b:{board_code} f:!50",
            "fields": "f12,f14,f3",
        },
        referer="https://quote.eastmoney.com/center/boardlist.html",
    )
    diff = ((payload or {}).get("data") or {}).get("diff") or []
    if isinstance(diff, dict):
        diff = list(diff.values())
    df = pd.DataFrame(diff).rename(columns={"f12": "code", "f14": "name", "f3": "pct_chg"})
    if "code" in df.columns:
        df["code"] = df["code"].astype(str).str.zfill(6)
    return df


def board_kline(http: Http, board_code: str, start_ymd: str, end_ymd: str) -> pd.DataFrame:
    return kline(http, f"90.{board_code}", start_ymd, end_ymd, klt=101, fqt=0)


# ---------------- 热度与资讯 ----------------
def stock_comment(http: Http, page_size: int = 500, pages: int = 12) -> pd.DataFrame:
    """千股千评（综合得分/机构参与度/关注度）。"""
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for base in (DATACENTER_WEB, DATACENTER):
        rows = []
        try:
            for page in range(1, pages + 1):
                payload = http.get_json(
                    base,
                    {
                        "reportName": "RPT_DMSK_TS_STOCKNEW",
                        "columns": "ALL",
                        "quoteColumns": "",
                        "pageNumber": page,
                        "pageSize": page_size,
                        "sortColumns": "SECURITY_CODE",
                        "sortTypes": 1,
                        "source": "WEB",
                        "client": "WEB",
                    },
                    referer="https://data.eastmoney.com/stockcomment/",
                )
                data = ((payload or {}).get("result") or {}).get("data") or []
                if not data:
                    break
                rows.extend(data)
            if rows:
                df = pd.DataFrame(rows)
                if "SECURITY_CODE" in df.columns:
                    df["SECURITY_CODE"] = df["SECURITY_CODE"].astype(str).str.zfill(6)
                return df
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{base}: {exc}"[:200])
    raise FetchError("东财千股千评接口失败：" + " | ".join(errors))


def stock_news(http: Http, keyword: str, size: int = 10) -> pd.DataFrame:
    """东财资讯搜索（个股资讯/评论摘要）。"""
    param = {
        "uid": "",
        "keyword": keyword,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "default",
                "pageIndex": 1,
                "pageSize": size,
                "preTag": "",
                "postTag": "",
            }
        },
    }
    payload = http.get_json(
        SEARCH,
        {"cb": "jQuery", "param": json.dumps(param, ensure_ascii=False), "_": "1"},
        referer="https://so.eastmoney.com/",
        jsonp=True,
    )
    items = ((payload or {}).get("result") or {}).get("cmsArticleWebOld") or []
    rows = [
        {
            "title": (item.get("title") or "").replace("<em>", "").replace("</em>", ""),
            "summary": (item.get("content") or "").replace("<em>", "").replace("</em>", "")[:200],
            "time": item.get("date") or item.get("showTime"),
            "url": item.get("url"),
            "media": item.get("mediaName") or item.get("nickname"),
        }
        for item in items
    ]
    if not rows:
        raise FetchError(f"东财资讯搜索无结果: {keyword}")
    return pd.DataFrame(rows)


def most_active(http: Http, top: int = 30) -> pd.DataFrame:
    """成交额排名（人气榜需要 POST 接口，这里用成交额活跃度作为可公开替代）。"""
    payload = http.get_json(
        CLIST,
        {
            "pn": 1,
            "pz": max(1, top),
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f6",
            "fs": FS_A_SHARE,
            "fields": SPOT_FIELDS,
        },
        referer="https://quote.eastmoney.com/",
    )
    diff = ((payload or {}).get("data") or {}).get("diff") or []
    if isinstance(diff, dict):
        diff = list(diff.values())
    if not diff:
        raise FetchError("东财活跃股接口无数据")
    df = pd.DataFrame(diff).rename(columns=SPOT_RENAME)
    if "code" in df.columns:
        df["code"] = df["code"].astype(str).str.zfill(6)
    return df
