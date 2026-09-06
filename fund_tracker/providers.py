"""AKShare first; bounded public-site fallbacks, cache and provenance.

Fallbacks share Eastmoney's upstream with AKShare. They protect against parser
breakage, not an independent outage. No login, CAPTCHA, proxy rotation or JS eval.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import signal
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from .core import NON_THEME, PERIODS, fund_code, latest_holdings, profile_fields, security_id, text

RANK_URL = "https://fund.eastmoney.com/data/rankhandler.aspx"
ARCHIVE_URL = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
UA = "StockFundTracker/1.0 (+https://github.com/zencolab/stock)"


def dump(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    tmp.replace(path)


@contextmanager
def deadline(seconds=90):
    # Actions runs Linux. On non-POSIX systems HTTP connect/read timeouts remain.
    if not hasattr(signal, "setitimer"):
        yield
        return
    def expired(*_):
        raise TimeoutError(f"Source exceeded {seconds}s deadline")
    old = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


@contextmanager
def bounded_http(delay=1.0):
    """Serial CLI only: apply timeout, pacing and a circuit breaker to AKShare too."""
    original = requests.sessions.Session.request
    last, failures, blocked = {}, {}, set()
    def wrapped(session, method, url, **kwargs):
        host = urlsplit(url).netloc
        if host in blocked or failures.get(host, 0) >= 3:
            raise RuntimeError(f"Host circuit open: {host}")
        kwargs["timeout"] = kwargs.get("timeout") or (10, 25)
        for attempt in range(2):
            time.sleep(max(0, delay - (time.monotonic() - last.get(host, 0))))
            last[host] = time.monotonic()
            try:
                response = original(session, method, url, **kwargs)
                if response.status_code in (401, 403, 429):
                    blocked.add(host)
                response.raise_for_status()
                failures[host] = 0
                return response
            except requests.RequestException as exc:
                status = exc.response.status_code if exc.response is not None else 0
                if status and status < 500:
                    raise
                failures[host] = failures.get(host, 0) + 1
                if attempt or failures[host] >= 3:
                    raise
                time.sleep(2)
    requests.sessions.Session.request = wrapped
    try:
        yield
    finally:
        requests.sessions.Session.request = original


def js_property(raw: str, name: str):
    """Decode one quoted property, never execute remote JavaScript."""
    match = re.search(r"\b" + re.escape(name) + r'''\s*:\s*("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')''', raw, re.S)
    if not match:
        raise ValueError(f"Missing JS property: {name}")
    token = match[1]
    return json.loads(token) if token.startswith('"') else ast.literal_eval(token)


def parse_profile_html(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    result = {}
    for row in soup.select("table tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        for i in range(0, len(cells) - 1, 2):
            key = re.sub(r"\s+", "", cells[i].get_text()).rstrip("：:")
            result[key] = cells[i + 1].get_text(" ", strip=True)
    if not any(k in result for k in ["基金经理人", "基金经理", "资产规模", "成立日期/规模"]):
        raise ValueError("Fund profile HTML schema changed")
    return result


def parse_archive_html(raw: str) -> list[dict]:
    html = js_property(raw, "content") if "<table" not in raw[:20].lower() else raw
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for table in soup.find_all("table"):
        heading = table.find_previous("h4")
        if heading is None:
            continue
        label = heading.get_text(" ", strip=True)
        headers = [re.sub(r"\s+", "", th.get_text()) for th in table.select("thead th")]
        if not headers:
            first = table.find("tr")
            headers = [re.sub(r"\s+", "", c.get_text()) for c in first.find_all(["td", "th"])] if first else []
        for tr in table.find_all("tr"):
            cells = tr.find_all("td", recursive=False)
            if len(cells) != len(headers):
                continue
            row = dict(zip(headers, [c.get_text(" ", strip=True) for c in cells]))
            if "股票代码" not in row or row["股票代码"] == "股票代码":
                continue
            result = {"季度": label}
            for key, value in row.items():
                for name in ["股票代码", "股票名称", "占净值比例", "持股数", "持仓市值"]:
                    if key.startswith(name):
                        result[name] = value
            rows.append(result)
    if not rows:
        raise ValueError("未取得股票持仓表，可能无披露或页面格式已变")
    return rows


class Provider:
    def __init__(self, cache: Path, raw_dir: Path):
        self.cache, self.raw_dir = cache, raw_dir
        self.sources, self.warnings = {}, []
        self.ak_failures, self.robots, self.stock_tags = {}, {}, {}
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Referer": "https://fund.eastmoney.com/"})

    def warn(self, source, exc):
        self.warnings.append({"source": source, "error": f"{type(exc).__name__}: {exc}"[:400]})
        print(f"WARNING {source}: {str(exc)[:160]}", flush=True)

    def ak(self, name, **params):
        if self.ak_failures.get(name, 0) >= 3:
            raise RuntimeError(f"AKShare method circuit open: {name}")
        try:
            import akshare as ak
            frame = getattr(ak, name)(**params)
            result = json.loads(frame.to_json(orient="records", date_format="iso", force_ascii=False))
            if not result:
                raise ValueError("Empty upstream response")
            self.ak_failures[name] = 0
            return result
        except Exception:
            self.ak_failures[name] = self.ak_failures.get(name, 0) + 1
            raise

    def public_get(self, url, **kwargs):
        origin = f"{urlsplit(url).scheme}://{urlsplit(url).netloc}"
        if origin not in self.robots:
            parser = RobotFileParser()
            try:
                response = self.session.get(origin + "/robots.txt")
                parser.parse(response.text.splitlines())
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    parser.parse([])
                else:
                    raise RuntimeError(f"Cannot verify crawl permission: {origin}") from exc
            self.robots[origin] = parser
        if not self.robots[origin].can_fetch(UA, url):
            raise PermissionError(f"robots.txt disallows fallback: {url}")
        return self.session.get(url, **kwargs)

    def get(self, key, source, url, fn, ttl_hours=0):
        ident = hashlib.sha256(("v1:" + key).encode()).hexdigest()[:24]
        cache_file = self.cache / (ident + ".json")
        packet = None
        if cache_file.exists() and ttl_hours:
            try:
                saved = json.loads(cache_file.read_text(encoding="utf-8"))
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(saved["fetched_at"])).total_seconds()
                if 0 <= age < ttl_hours * 3600:
                    packet = saved
            except (ValueError, KeyError, OSError):
                pass
        hit = packet is not None
        if packet is None:
            with deadline():
                data = fn()
            if not data:
                raise ValueError(f"Empty data: {key}")
            packet = {"source": source, "url": url, "fetched_at": datetime.now(timezone.utc).isoformat(), "data": data}
            if ttl_hours:
                dump(cache_file, packet)
        raw_file = self.raw_dir / (ident + ".json")
        dump(raw_file, packet)
        self.sources[ident] = {k: v for k, v in packet.items() if k != "data"}
        self.sources[ident].update({"cache_hit": hit, "raw_file": raw_file.name,
                                    "sha256": hashlib.sha256(raw_file.read_bytes()).hexdigest()})
        return packet["data"], ident

    def attempt(self, *args, **kwargs):
        try:
            return self.get(*args, **kwargs)
        except Exception as exc:
            self.warn(args[0], exc)
            return None, None

    def rankings(self, category="全部"):
        def primary():
            rows = self.ak("fund_open_fund_rank_em", symbol=category)
            if len(rows) >= 30000 or not {"基金代码", "基金简称", "日期", "单位净值", "日增长率", *PERIODS.values()}.issubset(rows[0]):
                raise ValueError("AKShare ranking truncated or schema changed; use paginated fallback")
            return rows
        rows, source = self.attempt("rank:" + category, "akshare:fund_open_fund_rank_em", RANK_URL, primary)
        if rows is not None:
            return rows, source
        return self.get("rank-web:" + category, "public-web:rankhandler", RANK_URL,
                        lambda: self.rank_web(category))

    def rank_web(self, category):
        types = {"全部": "all", "股票型": "gp", "混合型": "hh", "债券型": "zq", "指数型": "zs", "QDII": "qdii", "FOF": "fof"}
        now = datetime.now().date()
        try:
            start = now.replace(year=now.year - 1)
        except ValueError:
            start = now.replace(year=now.year - 1, day=28)
        rows = []
        for page in range(1, 101):
            raw = self.public_get(RANK_URL, params={"op": "ph", "dt": "kf", "ft": types[category], "gs": "0",
                "sc": "1nzf", "st": "desc", "sd": start.isoformat(), "ed": now.isoformat(),
                "pi": page, "pn": 5000, "dx": "1"}).text
            match = re.search(r"\bdatas\s*:\s*(\[.*?\])", raw, re.S)
            total = re.search(r"\ballRecords\s*:\s*(\d+)", raw)
            if not match or not total:
                raise ValueError("Ranking JS schema/total changed")
            entries = json.loads(match[1])
            if not entries:
                raise ValueError("Ranking pagination incomplete")
            columns = {0: "基金代码", 1: "基金简称", 3: "日期", 4: "单位净值", 6: "日增长率",
                       7: "近1周", 8: "近1月", 9: "近3月", 10: "近6月", 11: "近1年"}
            for entry in entries:
                parts = entry.split(",")
                if len(parts) < 12:
                    raise ValueError("Ranking row too short")
                rows.append({v: parts[k] for k, v in columns.items()})
            if len(rows) >= int(total[1]):
                if len({r["基金代码"] for r in rows}) != int(total[1]):
                    raise ValueError("Ranking total mismatch or duplicate pages")
                return rows
        raise ValueError("Ranking pagination exceeded safety limit")

    def profile(self, code):
        url = "https://fundf10.eastmoney.com/jbgk_" + code + ".html"
        data, sid = self.attempt("profile:" + code, "akshare:fund_overview_em", url,
                                 lambda: self.ak("fund_overview_em", symbol=code), 24)
        fields = profile_fields(data[0] if data else {})
        sources = {k: sid for k, v in fields.items() if v is not None}
        if any(fields[k] is None for k in ["manager", "inception_date", "aum_raw", "aum_value"]):
            web, wid = self.attempt("profile-web:" + code, "public-web:fund-profile", url,
                                    lambda: parse_profile_html(self.public_get(url).text), 24)
            extra = profile_fields(web or {})
            for k, v in extra.items():
                if fields[k] is None and v is not None:
                    fields[k], sources[k] = v, wid
        fields["field_sources"] = sources
        fields["missing_fields"] = [k for k in ["manager", "inception_date", "aum_raw", "aum_value", "aum_unit", "aum_as_of"] if fields[k] is None]
        return fields

    def holdings(self, code, run_date):
        for year in [run_date.year, run_date.year - 1]:
            rows, sid = self.attempt(f"hold:{code}:{year}", "akshare:fund_portfolio_hold_em", ARCHIVE_URL,
                                     lambda: self.ak("fund_portfolio_hold_em", symbol=code, date=str(year)), 12)
            chosen = None
            if rows:
                try:
                    hs, info = latest_holdings(rows, run_date)
                    chosen = (hs, info, sid)
                except ValueError as exc:
                    self.warn("hold-parse:" + code, exc)
            if chosen is None or chosen[1]["possibly_truncated"]:
                web, wid = self.attempt(f"hold-web:{code}:{year}", "public-web:fund-archives", ARCHIVE_URL,
                    lambda: parse_archive_html(self.public_get(ARCHIVE_URL, params={"type": "jjcc", "code": code,
                        "year": year, "month": "", "topline": 10000}).text), 12)
                if web:
                    try:
                        hs, info = latest_holdings(web, run_date)
                        if chosen is None or (info["report_date"], len(hs)) >= (chosen[1]["report_date"], len(chosen[0])):
                            # Even an expanded public table cannot prove full disclosure.
                            info["possibly_truncated"] = len(hs) >= 10000
                            chosen = (hs, info, wid)
                    except ValueError as exc:
                        self.warn("hold-web-parse:" + code, exc)
            if chosen:
                hs, info, source = chosen
                info["source_id"] = source
                for h in hs:
                    h["holdings_source_id"] = source
                return hs, info
        return [], {"status": "unavailable", "reason": "未取得股票持仓：可能未披露、不适用或源站不可用，不能据此判断零持股"}

    def clist(self, fs):
        rows = []
        for page in range(1, 501):
            body = self.public_get(CLIST_URL, params={"pn": page, "pz": 100, "po": 1, "np": 1,
                "fltt": 2, "invt": 2, "fid": "f12", "fs": fs, "fields": "f12,f14"}).json()
            data = body.get("data") or {}
            diff, total = data.get("diff"), data.get("total")
            if isinstance(diff, dict):
                diff = list(diff.values())
            if not diff or total is None:
                raise ValueError("Concept list missing data/total")
            rows.extend(diff)
            if len(rows) >= int(total):
                if len({str(r["f12"]) for r in rows}) != int(total):
                    raise ValueError("Concept pagination incomplete/duplicate")
                return rows
        raise ValueError("Concept pagination safety limit")

    def concepts(self):
        boards, sid = self.attempt("concept-boards", "akshare:stock_board_concept_name_em", CLIST_URL,
                                   lambda: self.ak("stock_board_concept_name_em"), 168)
        if not boards or not {"板块代码", "板块名称"}.issubset(boards[0]):
            raw, sid = self.attempt("concept-boards-web", "public-web:concept-boards", CLIST_URL,
                                    lambda: self.clist("m:90 t:3 f:!50"), 168)
            boards = [{"板块代码": r["f12"], "板块名称": r["f14"]} for r in raw] if raw else []
        index, completed, streak = {}, 0, 0
        stats = {"boards_total": len(boards), "boards_completed": 0, "complete": False, "source_id": sid}
        for board in boards:
            code, name = str(board["板块代码"]), str(board["板块名称"])
            rows, src = self.attempt("concept:" + code, "akshare:stock_board_concept_cons_em", CLIST_URL,
                                     lambda: self.ak("stock_board_concept_cons_em", symbol=code), 168)
            if not rows or "代码" not in rows[0]:
                raw, src = self.attempt("concept-web:" + code, "public-web:concept-members", CLIST_URL,
                                        lambda: self.clist(f"b:{code} f:!50"), 168)
                rows = [{"代码": r["f12"]} for r in raw] if raw else []
            if not rows:
                streak += 1
                if streak >= 3:
                    self.warn("concept-index", RuntimeError("3 consecutive board failures; stop and flag partial coverage"))
                    break
                continue
            streak = 0
            completed += 1
            for row in rows:
                market, stock = security_id(row["代码"])
                key = f"{market}:{stock}"
                info = index.setdefault(key, {"concepts": [], "market_tags": [], "concept_source_ids": []})
                field = "market_tags" if name in NON_THEME else "concepts"
                info[field].append(name)
                info["concept_source_ids"].append(src)
        stats.update({"boards_completed": completed, "complete": bool(boards) and completed == len(boards)})
        return index, stats

    def industry(self, market, code):
        key = f"{market}:{code}"
        if key in self.stock_tags:
            return self.stock_tags[key]
        result = {"industry": None, "industry_source_id": None, "classification_status": "unmapped_market"}
        if market in {"SH", "SZ", "BJ"}:
            url = "https://push2.eastmoney.com/api/qt/stock/get"
            rows, sid = self.attempt("industry:" + key, "akshare:stock_individual_info_em", url,
                                     lambda: self.ak("stock_individual_info_em", symbol=code, timeout=15), 168)
            info = {r.get("item"): r.get("value") for r in rows or []}
            industry = text(info.get("行业"))
            returned_code = text(info.get("股票代码"))
            if returned_code and returned_code != code:
                industry = None
            if not industry:
                raw, sid = self.attempt("industry-web:" + key, "public-web:stock-info", url,
                    lambda: self.public_get(url, params={"secid": f"{1 if market == 'SH' else 0}.{code}",
                                                        "fields": "f57,f58,f127"}).json().get("data"), 168)
                industry = text(raw.get("f127")) if raw and str(raw.get("f57")) == code else None
            result = {"industry": industry, "industry_source_id": sid if industry else None,
                      "classification_status": "mapped" if industry else "unavailable"}
        self.stock_tags[key] = result
        return result
