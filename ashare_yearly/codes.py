"""A股代码工具：市场判定、secid、板块归属。

纯函数模块，无外部依赖，便于离线单测。
"""

from __future__ import annotations

import re

_DIGITS = re.compile(r"(\d{6})")


class CodeError(ValueError):
    """非法的股票代码。"""


def normalize(code: object) -> str:
    """把各种写法统一成 6 位数字代码。

    支持 ``600000`` / ``sh600000`` / ``600000.SH`` / ``SZ.000001`` / ``600000.0``。
    """
    text = str(code).strip()
    if not text:
        raise CodeError("空代码")
    if text.endswith(".0"):  # pandas 读 CSV 可能把代码读成浮点
        text = text[:-2]
    match = _DIGITS.search(text)
    if not match:
        # 纯数字但不足 6 位（Excel 会吃掉前导 0）
        stripped = re.sub(r"\D", "", text)
        if stripped and len(stripped) < 6:
            return stripped.zfill(6)
        raise CodeError(f"无法解析股票代码: {code!r}")
    return match.group(1)


def market(code: object) -> str:
    """返回 ``SH`` / ``SZ`` / ``BJ``。"""
    c = normalize(code)
    if c.startswith(("60", "68", "90", "58", "51", "56", "11")):
        return "SH"
    if c.startswith(("00", "30", "20", "15", "16", "18", "12")):
        return "SZ"
    if c.startswith(("43", "83", "87", "88", "92")):
        return "BJ"
    if c.startswith("8"):
        return "BJ"
    raise CodeError(f"无法判断市场: {code!r}")


def secid(code: object) -> str:
    """东方财富行情接口用的 secid（``1.`` 沪 / ``0.`` 深、北）。"""
    c = normalize(code)
    return f"1.{c}" if market(c) == "SH" else f"0.{c}"


def prefixed(code: object, upper: bool = False) -> str:
    """``sh600000`` 形式；``upper=True`` 时为 ``SH600000``。"""
    c = normalize(code)
    prefix = market(c)
    prefix = prefix if upper else prefix.lower()
    return f"{prefix}{c}"


def dotted(code: object) -> str:
    """``600000.SH`` 形式（东财数据中心 SECUCODE 用）。"""
    c = normalize(code)
    return f"{c}.{market(c)}"


def board(code: object) -> str:
    """所属交易板块（沪市主板/科创板/深市主板/创业板/北交所）。"""
    c = normalize(code)
    if c.startswith("688") or c.startswith("689"):
        return "科创板"
    if c.startswith("60"):
        return "沪市主板"
    if c.startswith(("300", "301", "302")):
        return "创业板"
    if c.startswith(("000", "001", "003")):
        return "深市主板"
    if c.startswith(("002", "004")):
        return "深市主板(原中小板)"
    if market(c) == "BJ":
        return "北交所"
    return "其他"


def is_a_share(code: object) -> bool:
    """是否为 A 股股票代码（排除基金/债券/指数等）。"""
    try:
        c = normalize(code)
    except CodeError:
        return False
    return c.startswith(
        ("600", "601", "603", "605", "688", "689", "000", "001", "002", "003", "004", "300", "301", "302", "430", "830", "831", "832", "833", "834", "835", "836", "837", "838", "839", "870", "871", "872", "873", "920")
    )
