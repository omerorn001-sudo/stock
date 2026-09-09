"""同花顺公开接口兜底层。

同花顺部分页面需要 ``hexin-v`` 等动态 Cookie，无法稳定匿名访问；因此：
1. 热点快讯优先用 akshare（``stock_info_global_ths``）；
2. 兜底用公开的资讯推送接口 ``news.10jqka.com.cn/tapp/news/push/stock/``；
3. 概念板块涨幅榜兜底解析 ``q.10jqka.com.cn`` HTML（需 beautifulsoup4）。
取不到时一律返回失败并在报告中注明原因，不编造内容。
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from ..netutil import FetchError, Http

NEWS_PUSH = "https://news.10jqka.com.cn/tapp/news/push/stock/"
CONCEPT_RANK_TEMPLATE = "https://q.10jqka.com.cn/gn/detail/field/199112/order/desc/page/%s/ajax/1/"
STOCK_OPERATE_TEMPLATE = "https://basic.10jqka.com.cn/%s/operate.html"

HEADERS = {
    "Referer": "https://news.10jqka.com.cn/",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}


def hot_news(http: Http, pages: int = 1, page_size: int = 30) -> pd.DataFrame:
    """同花顺财经热点推送（快讯/热议）。"""
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for page in range(1, max(1, pages) + 1):
        try:
            payload = http.get_json(
                NEWS_PUSH,
                {"page": page, "tag": "", "track": "website", "pagesize": page_size},
                extra_headers=HEADERS,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc)[:200])
            continue
        items = ((payload or {}).get("data") or {}).get("list") or []
        for item in items:
            rows.append(
                {
                    "title": item.get("title"),
                    "summary": (item.get("digest") or "")[:200],
                    "time": item.get("rtime") or item.get("ctime"),
                    "url": item.get("url") or item.get("appurl"),
                    "tag": item.get("tagInfo") or item.get("tag"),
                }
            )
    if not rows:
        raise FetchError("同花顺热点接口无数据：" + " | ".join(errors))
    return pd.DataFrame(rows)


def concept_rank(http: Http, pages: int = 1) -> pd.DataFrame:
    """同花顺概念板块涨幅榜（HTML 解析，需 beautifulsoup4）。"""
    try:
        from bs4 import BeautifulSoup  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        raise FetchError(f"缺少 beautifulsoup4，无法解析同花顺概念榜: {exc}") from exc

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for page in range(1, max(1, pages) + 1):
        try:
            html = http.get_text(
                CONCEPT_RANK_TEMPLATE % page,
                extra_headers={"Referer": "https://q.10jqka.com.cn/gn/", "X-Requested-With": "XMLHttpRequest"},
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc)[:200])
            continue
        soup = BeautifulSoup(html, "html.parser")
        for tr in soup.select("tbody tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) < 3:
                continue
            rows.append({"rank": cells[0], "board": cells[1], "pct_chg": cells[2], "detail": " | ".join(cells[3:6])})
    if not rows:
        raise FetchError("同花顺概念榜解析为空：" + " | ".join(errors))
    return pd.DataFrame(rows)


def main_business(http: Http, code: str) -> dict[str, Any]:
    """同花顺 F10 经营分析页的主营业务文本。"""
    html = http.get_text(STOCK_OPERATE_TEMPLATE % code, extra_headers={"Referer": "https://basic.10jqka.com.cn/"})
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    match = re.search(r"主营业务[：:]?\s*(.{10,400}?)(主营构成|经营评述|上市日期|$)", text)
    if not match:
        raise FetchError(f"同花顺未解析到主营业务: {code}")
    return {"main_business": match.group(1).strip(), "source": "ths:basic.10jqka.com.cn/operate"}
