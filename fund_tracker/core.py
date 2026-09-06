"""Pure, testable fund ranking and disclosed-holdings calculations.

All returns and weights use percentage points (12.5 means 12.5%, not 0.125).
No model-generated financial facts are used.
"""
from __future__ import annotations

import calendar
import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any

PERIODS = {"1y": "近1年", "6m": "近6月", "3m": "近3月", "1m": "近1月", "1w": "近1周"}
NON_THEME = {"融资融券", "沪股通", "深股通", "转融券标的", "基金重仓", "机构重仓", "证金持股", "社保重仓", "标准普尔", "MSCI中国"}
MISSING = {"", "--", "---", "—", "-", "nan", "none", "null", "nat", "<na>", "暂无数据"}


def text(value: Any) -> str | None:
    s = str(value).strip() if value is not None else ""
    return None if s.lower() in MISSING else s


def number(value: Any) -> float | None:
    s = text(value)
    if s is None:
        return None
    try:
        x = float(s.replace(",", "").replace("%", "").replace("％", ""))
        return x if math.isfinite(x) else None
    except (ValueError, TypeError):
        return None


def iso_date(value: Any) -> str | None:
    s = text(value) or ""
    m = re.search(r"(\d{4})[-年/](\d{1,2})[-月/](\d{1,2})", s)
    if not m:
        m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s)
    if not m:
        return None
    try:
        return date(*map(int, m.groups())).isoformat()
    except ValueError:
        return None


def period_end(value: Any) -> str | None:
    direct = iso_date(value)
    if direct:
        return direct
    m = re.search(r"(\d{4})年?\s*第?\s*([1-4一二三四])\s*季", str(value))
    if not m:
        return None
    digit = m[2] if m[2].isdigit() else str("一二三四".index(m[2]) + 1)
    y, month = int(m[1]), int(digit) * 3
    return date(y, month, calendar.monthrange(y, month)[1]).isoformat()


def fund_code(value: Any) -> str:
    s = re.sub(r"\.0$", "", str(value).strip())
    if not s.isdigit() or len(s) > 6:
        raise ValueError(f"Invalid fund code: {value!r}")
    return s.zfill(6)


def security_id(value: Any) -> tuple[str, str]:
    # Never pad every security to six digits: 00700 (HK) is not 000700 (SZ).
    s = str(value).strip().upper()
    match = re.fullmatch(r"(SH|SZ|BJ|HK)[.:]?(\d+)", s)
    if not match:
        match = re.fullmatch(r"(\d+)\.(SH|SZ|BJ|HK)", s)
        if match:
            market, s = match[2], match[1]
            return market, s.zfill(5 if market == "HK" else 6)
    else:
        market, s = match[1], match[2]
        return market, s.zfill(5 if market == "HK" else 6)
    if re.fullmatch(r"\d{5}", s):
        return "HK", s
    if re.fullmatch(r"\d{6}", s):
        if s.startswith("6"):
            return "SH", s
        if s.startswith(("0", "3")):
            return "SZ", s
        if s.startswith(("4", "8", "92")):
            return "BJ", s
    return "UNKNOWN", s


def previous_session(calendar_rows: list[dict], run_date: date) -> str:
    days = sorted({d for r in calendar_rows if (d := iso_date(r.get("trade_date")))})
    # A historical calendar ending before today cannot verify intervening holidays.
    if not days or days[-1] < run_date.isoformat():
        raise ValueError("交易日历未覆盖采集日，不能可靠确定上一交易日")
    past = [d for d in days if d < run_date.isoformat()]
    if not past:
        raise ValueError("交易日历缺少上一交易日")
    return past[-1]


def rank_funds(rows: list[dict], top_n: int, run_date: date, previous_day: str | None, max_age: int = 14) -> tuple[list[dict], dict]:
    if not rows or not 1 <= top_n <= 500:
        raise ValueError("Empty universe or invalid top_n")
    required = {"基金代码", "基金简称", "日期", "单位净值", "日增长率", *PERIODS.values()}
    if not required.issubset(rows[0]):
        raise ValueError(f"Ranking schema changed: {sorted(required - rows[0].keys())}")
    pool, seen, excluded = [], {}, Counter()
    for row in rows:
        code = fund_code(row["基金代码"])
        nav_date = iso_date(row["日期"])
        if not nav_date:
            excluded["invalid_nav_date"] += 1
            continue
        age = (run_date - date.fromisoformat(nav_date)).days
        if age < 0 or age > max_age:
            excluded["future_or_stale_nav"] += 1
            continue
        f = {"code": code, "name": text(row["基金简称"]), "nav_date": nav_date,
             "nav_age_days": age, "nav": number(row["单位净值"]),
             "latest_daily_return_pct": number(row["日增长率"]),
             "1d": number(row["日增长率"]) if nav_date == previous_day else None,
             **{k: number(row[v]) for k, v in PERIODS.items()}}
        if f["nav"] is None or f["nav"] <= 0:
            excluded["invalid_nav"] += 1
            continue
        if code in seen:
            if f != seen[code]:
                raise ValueError(f"Conflicting duplicate fund: {code}")
            excluded["identical_duplicate"] += 1
            continue
        seen[code] = f
        pool.append(f)
    # Rank BEFORE selecting the one-year top N. Missing returns are not zero.
    for metric in ["nav", *PERIODS, "1d"]:
        values = Counter(f[metric] for f in pool if f[metric] is not None)
        ranks, offset = {}, 1
        for value in sorted(values, reverse=True):
            ranks[value] = offset
            offset += values[value]
        for f in pool:
            f.setdefault("ranks", {})[metric] = {"rank": ranks.get(f[metric]), "total": offset - 1}
    eligible = [f for f in pool if f["1y"] is not None]
    if len(eligible) < top_n:
        raise ValueError(f"近一年有效基金仅 {len(eligible)} 只，无法生成 TOP{top_n}")
    top = sorted(eligible, key=lambda f: (-f["1y"], f["code"]))[:top_n]
    meta = {"raw_rows": len(rows), "rank_universe": len(pool), "one_year_eligible": len(eligible),
            "selected": len(top), "excluded": dict(excluded), "previous_trading_day": previous_day,
            "nav_date_counts": dict(Counter(f["nav_date"] for f in pool)),
            "rank_method": "descending competition rank: 1,1,3; ties cut by fund code for exactly top_n",
            "max_nav_age_calendar_days": max_age}
    return top, meta


def profile_fields(data: dict) -> dict:
    def pick(*keys):
        return next((text(data[k]) for k in keys if text(data.get(k))), None)
    raw_aum = pick("资产规模", "基金规模")
    m = re.search(r"([\d,.]+)\s*(亿元|万元|元)", raw_aum or "")
    amount = number(m[1]) if m else None
    unit = m[2] if m else None
    # Do not silently interpret share counts ('份额规模' / totshare) as AUM.
    return {"manager": pick("基金经理人", "基金经理"),
            "inception_date": iso_date(pick("成立日期/规模", "成立日期", "成立时间")),
            "fund_type": pick("基金类型"), "aum_raw": raw_aum,
            "aum_value": amount, "aum_unit": unit, "aum_as_of": iso_date(raw_aum)}


def latest_holdings(rows: list[dict], run_date: date) -> tuple[list[dict], dict]:
    dated = [(r, period_end(r.get("季度"))) for r in rows]
    valid = [(r, d) for r, d in dated if d and d <= run_date.isoformat()]
    if not valid:
        raise ValueError("未取得可识别报告期的股票持仓；不等于没有持股")
    latest = max(d for _, d in valid)
    holdings, seen = [], {}
    for r, d in valid:
        if d != latest:
            continue
        raw = text(r.get("股票代码"))
        if not raw:
            continue
        market, code = security_id(raw)
        weight = number(r.get("占净值比例"))
        if weight is not None and not 0 <= weight <= 100:
            raise ValueError(f"Invalid holding weight: {weight}")
        item = {"security_id": f"{market}:{code}", "market": market, "stock_code": code,
                "stock_name": text(r.get("股票名称")), "weight_pct": weight,
                "shares_10k": number(r.get("持股数")), "value_10k_reported": number(r.get("持仓市值")),
                "report_date": latest, "report_label": str(r.get("季度"))}
        if item["security_id"] in seen:
            if item != seen[item["security_id"]]:
                raise ValueError(f"Conflicting holding: {item['security_id']}")
            continue
        seen[item["security_id"]] = item
        holdings.append(item)
    if not holdings:
        raise ValueError("股票持仓表为空；不能将抓取失败解释为零仓位")
    return holdings, {"report_date": latest, "report_age_days": (run_date - date.fromisoformat(latest)).days,
                      "holding_count": len(holdings), "possibly_truncated": len(holdings) >= 100,
                      "scope": "公开披露明细，可能仅重仓股；不是实时仓位，也不保证全量"}


def summarize_holdings(holdings: list[dict]) -> dict:
    industries, concepts = defaultdict(float), defaultdict(float)
    total = industry_known = concept_known = 0.0
    unknown_weights = 0
    for h in holdings:
        w = h.get("weight_pct")
        if w is None:
            unknown_weights += 1
            continue
        total += w
        industry = h.get("industry") or "未分类"
        industries[industry] += w
        if h.get("industry"):
            industry_known += w
        tags = set(h.get("concepts", []))
        if tags:
            concept_known += w
        for tag in tags:
            concepts[tag] += w
    def sorted_weights(values):
        return [{"name": k, "weight_pct_of_fund_nav": round(v, 4)}
                for k, v in sorted(values.items(), key=lambda kv: (-kv[1], kv[0]))]
    return {"disclosed_weight_pct": round(total, 4), "unknown_weight_rows": unknown_weights,
            "industry_classified_weight_pct": round(industry_known, 4),
            "concept_classified_weight_pct": round(concept_known, 4),
            "industry_coverage_of_disclosed_pct": round(industry_known / total * 100, 2) if total else None,
            "concept_coverage_of_disclosed_pct": round(concept_known / total * 100, 2) if total else None,
            "industries": sorted_weights(industries), "concepts": sorted_weights(concepts),
            "note": "权重分母为基金净值；概念可重叠，不能相加解释成资产配置。分类采用采集时标签，不能用于无前视偏差的回测。"}
