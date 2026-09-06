import csv
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from bs4 import BeautifulSoup

from fund_tracker.core import (PERIODS, fund_code, iso_date, latest_holdings, number,
    period_end, previous_session, profile_fields, rank_funds, security_id, summarize_holdings)
from fund_tracker.main import demo_report, load_overrides, main
from fund_tracker.providers import Provider, bounded_http, js_property, parse_archive_html, parse_profile_html
from fund_tracker.report import csv_write, render, write_reports

TODAY = date(2026, 9, 6)


def fund(code, year=20, nav_date="2026-09-04", **extra):
    row = {"基金代码": code, "基金简称": "样例" + str(code), "日期": nav_date,
           "单位净值": 1.2, "日增长率": 0, **{v: 1 for v in PERIODS.values()}}
    row["近1年"] = year
    row.update(extra)
    return row


class CoreTests(unittest.TestCase):
    def test_numeric_missing_and_zero(self):
        self.assertIsNone(number("--"))
        self.assertIsNone(number("NaN"))
        self.assertIsNone(number("inf"))
        self.assertEqual(number("0%"), 0)
        self.assertEqual(number("1,234.50％"), 1234.5)

    def test_fund_and_stock_codes_are_different(self):
        self.assertEqual(fund_code(1), "000001")
        self.assertEqual(security_id("00700"), ("HK", "00700"))
        self.assertEqual(security_id("000700"), ("SZ", "000700"))
        self.assertEqual(security_id("700.HK"), ("HK", "00700"))
        self.assertEqual(security_id("AAPL")[0], "UNKNOWN")
        self.assertEqual(security_id("700")[0], "UNKNOWN")
        self.assertEqual(security_id("920001")[0], "BJ")

    def test_ranks_use_full_universe(self):
        rows = [fund(1, 30, **{"近6月": 5}), fund(2, 20, **{"近6月": 4}),
                fund(3, None, **{"近6月": 99})]
        top, meta = rank_funds(rows, 2, TODAY, "2026-09-04")
        self.assertEqual(top[0]["ranks"]["6m"], {"rank": 2, "total": 3})
        self.assertEqual(meta["one_year_eligible"], 2)
        # Independent direct comparison, not the implementation's Counter path.
        for f in top:
            expected = 1 + sum(r["近6月"] > f["6m"] for r in rows)
            self.assertEqual(f["ranks"]["6m"]["rank"], expected)

    def test_ties_and_exact_limit(self):
        top, _ = rank_funds([fund(3, 20), fund(2, 30), fund(1, 30)], 3, TODAY, "2026-09-04")
        self.assertEqual([f["ranks"]["1y"]["rank"] for f in top], [1, 1, 3])
        cut, _ = rank_funds([fund(2, 30), fund(1, 30)], 1, TODAY, None)
        self.assertEqual(cut[0]["code"], "000001")

    def test_previous_day_not_stale_daily_return(self):
        top, _ = rank_funds([fund(1, 30), fund(2, 20, "2026-09-03", **{"日增长率": 8})], 2, TODAY, "2026-09-04")
        self.assertEqual(top[0]["1d"], 0)
        self.assertIsNone(top[1]["1d"])
        self.assertEqual(top[0]["ranks"]["1d"], {"rank": 1, "total": 1})
        self.assertEqual(top[1]["latest_daily_return_pct"], 8)

    def test_unknown_calendar_keeps_daily_rank_empty(self):
        top, _ = rank_funds([fund(1)], 1, TODAY, None)
        self.assertIsNone(top[0]["ranks"]["1d"]["rank"])

    def test_stale_future_and_invalid_nav_excluded(self):
        rows = [fund(1), fund(2, 99, "2025-09-04"), fund(3, 99, "2026-09-07"), fund(4, 99, **{"单位净值": 0})]
        top, meta = rank_funds(rows, 1, TODAY, None)
        self.assertEqual(top[0]["code"], "000001")
        self.assertEqual(meta["rank_universe"], 1)
        self.assertEqual(sum(meta["excluded"].values()), 3)

    def test_duplicates_conflict_fails(self):
        with self.assertRaises(ValueError):
            rank_funds([fund(1, 20), fund(1, 30)], 1, TODAY, None)
        _, meta = rank_funds([fund(1), fund(1)], 1, TODAY, None)
        self.assertEqual(meta["excluded"]["identical_duplicate"], 1)

    def test_missing_year_does_not_become_zero(self):
        with self.assertRaises(ValueError):
            rank_funds([fund(1, None)], 1, TODAY, None)

    def test_calendar_coverage_required(self):
        calendar = [{"trade_date": x} for x in ["2026-09-03", "2026-09-04", "2026-09-07"]]
        self.assertEqual(previous_session(calendar, TODAY), "2026-09-04")
        with self.assertRaises(ValueError):
            previous_session(calendar[:2], TODAY)

    def test_dates_and_quarters(self):
        self.assertEqual(iso_date("2024年2月29日 / 10亿份"), "2024-02-29")
        self.assertIsNone(iso_date("2025-02-29"))
        self.assertEqual(period_end("2026年第2季度股票投资明细"), "2026-06-30")
        self.assertEqual(period_end("2025年第四季度"), "2025-12-31")

    def test_latest_period_not_sum_of_quarters(self):
        rows = [{"股票代码": "600001", "股票名称": "甲", "占净值比例": 8, "季度": "2026年2季度"},
                {"股票代码": "600001", "股票名称": "甲", "占净值比例": 9, "季度": "2026年1季度"}]
        holdings, info = latest_holdings(rows, TODAY)
        self.assertEqual(len(holdings), 1)
        self.assertEqual(holdings[0]["weight_pct"], 8)
        self.assertEqual(info["report_date"], "2026-06-30")

    def test_holdings_missing_is_not_zero(self):
        with self.assertRaises(ValueError):
            latest_holdings([], TODAY)
        with self.assertRaises(ValueError):
            latest_holdings([{"股票代码": "600001", "占净值比例": -1, "季度": "2026年2季度"}], TODAY)

    def test_aum_not_share_count(self):
        profile = profile_fields({"基金经理人": "甲 乙", "成立日期/规模": "2020年01月02日 / 10亿份",
                                  "资产规模": "12.50亿元（截止至：2026年06月30日）", "份额规模": "11亿份"})
        self.assertEqual(profile["aum_value"], 12.5)
        self.assertEqual(profile["aum_unit"], "亿元")
        self.assertEqual(profile["aum_as_of"], "2026-06-30")
        self.assertEqual(profile["inception_date"], "2020-01-02")
        self.assertIsNone(profile_fields({"份额规模": "11亿份"})["aum_value"])

    def test_overlap_weights_and_coverage(self):
        result = summarize_holdings([{"weight_pct": 10, "industry": "行业甲", "concepts": ["A", "B", "A"]},
                                     {"weight_pct": 5, "industry": None, "concepts": []}, {"weight_pct": None}])
        self.assertEqual(result["disclosed_weight_pct"], 15)
        self.assertEqual(result["industry_coverage_of_disclosed_pct"], 66.67)
        self.assertEqual(sum(a["weight_pct_of_fund_nav"] for a in result["concepts"]), 20)
        self.assertEqual(result["unknown_weight_rows"], 1)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.p = Provider(self.root / "cache", self.root / "raw")

    def test_js_does_not_execute(self):
        self.assertEqual(js_property('var a={content:"<b>ok</b>"};', "content"), "<b>ok</b>")
        with self.assertRaises(ValueError):
            js_property("var a={content:dangerous()};", "content")

    def test_profile_fallback_html(self):
        raw = '<table><tr><th>基金经理人</th><td>甲 / 乙</td><th>资产规模</th><td>0.00亿元（2026-06-30）</td></tr></table>'
        self.assertEqual(profile_fields(parse_profile_html(raw))["aum_value"], 0)
        with self.assertRaises(ValueError):
            parse_profile_html("<html>blocked</html>")

    def test_holdings_html_preserves_hk_and_report_period(self):
        html = '<h4 class="t">持仓明细 2026年2季度</h4><table><thead><tr><th>股票代码</th><th>股票名称</th><th>占净值 比例</th><th>持股数（万股）</th></tr></thead><tbody><tr><td>00700</td><td>样例</td><td>5.20%</td><td>1.2</td></tr></tbody></table>'
        raw = "var apidata={content:" + json.dumps(html, ensure_ascii=False) + ",arryear:[2026]};"
        rows = parse_archive_html(raw)
        holdings, _ = latest_holdings(rows, TODAY)
        self.assertEqual(holdings[0]["security_id"], "HK:00700")
        self.assertEqual(holdings[0]["weight_pct"], 5.2)

    def test_cache_and_provenance(self):
        fn = Mock(return_value=[{"value": 1}])
        a, sid = self.p.get("sample", "test", "https://example.com", fn, 1)
        b, other = self.p.get("sample", "test", "https://example.com", fn, 1)
        self.assertEqual(fn.call_count, 1)
        self.assertEqual(a, b)
        self.assertEqual(sid, other)
        self.assertTrue(self.p.sources[sid]["cache_hit"])
        self.assertEqual(len(self.p.sources[sid]["sha256"]), 64)

    def test_rank_fallback_after_ak_failure(self):
        self.p.ak = Mock(side_effect=RuntimeError("upstream changed"))
        self.p.rank_web = Mock(return_value=[fund(1)])
        rows, sid = self.p.rankings()
        self.assertEqual(rows[0]["基金代码"], 1)
        self.assertIn("public-web", self.p.sources[sid]["source"])

    def test_rank_web_pagination_and_order(self):
        def page(code, total):
            entry = f"{code},样例,X,2026-09-04,1.23,1.50,0,1,2,3,4,50"
            return Mock(text="var rankData={datas:" + json.dumps([entry]) + f",allRecords:{total}}};")
        self.p.public_get = Mock(side_effect=[page("000001", 2), page("000002", 2)])
        rows = self.p.rank_web("全部")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["近1年"], "50")
        self.assertEqual(rows[0]["日增长率"], "0")

    def test_repeated_pages_fail(self):
        entry = "000001,样例,X,2026-09-04,1.23,1.50,0,1,2,3,4,50"
        self.p.public_get = Mock(return_value=Mock(text="var r={datas:" + json.dumps([entry]) + ",allRecords:2};"))
        with self.assertRaises(ValueError):
            self.p.rank_web("全部")

    def test_robot_exclusion(self):
        self.p.session.get = Mock(return_value=Mock(text="User-agent: *\nDisallow: /private\n"))
        with self.assertRaises(PermissionError):
            self.p.public_get("https://example.com/private/data")
        self.assertEqual(self.p.session.get.call_count, 1)

    def test_http_403_no_retry_no_bypass(self):
        response = requests.Response()
        response.status_code = 403
        response.url = "https://example.com/data"
        with patch.object(requests.sessions.Session, "request", return_value=response) as original:
            with bounded_http(delay=0):
                with self.assertRaises(requests.HTTPError):
                    requests.get(response.url)
                with self.assertRaises(RuntimeError):
                    requests.get(response.url)
            self.assertEqual(original.call_count, 1)

    def test_foreign_stock_is_not_sent_to_a_share_endpoint(self):
        self.p.ak = Mock()
        self.assertIsNone(self.p.industry("HK", "00700")["industry"])
        self.p.ak.assert_not_called()


class DeliveryTests(unittest.TestCase):
    def test_demo_is_explicit_and_has_all_columns(self):
        report = demo_report(3)
        html = render(report)
        self.assertIn("全部为测试样例", html)
        self.assertIn("上一交易日 / 排名", html)
        self.assertNotIn("@@", html)
        soup = BeautifulSoup(html, "lxml")
        self.assertEqual(len(soup.select(".rankings tbody tr")), 3)
        self.assertTrue(soup.select_one(".fund-detail a")["href"].startswith("https://fundf10.eastmoney.com/ccmx_"))

    def test_escape_upstream_html(self):
        report = demo_report(1)
        report["funds"][0]["name"] = '<img src=x onerror="alert(1)">'
        soup = BeautifulSoup(render(report), "lxml")
        self.assertIsNone(soup.find("img"))

    def test_exports_and_csv_safety(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            write_reports(path, demo_report(2))
            self.assertEqual(len(json.loads((path / "funds.json").read_text())["funds"]), 2)
            self.assertTrue((path / "holdings.csv").exists())
            csv_write(path / "safe.csv", [{"a": "=SUM(1,2)", "b": -1}], ["a", "b"])
            with (path / "safe.csv").open(encoding="utf-8-sig") as stream:
                row = next(csv.DictReader(stream))
                self.assertTrue(row["a"].startswith("'="))
                self.assertEqual(row["b"], "-1")

    def test_failure_does_not_publish_fake_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("fund_tracker.main.collect", side_effect=RuntimeError("network unavailable")):
                code = main(["--output", str(root / "out"), "--raw", str(root / "raw")])
            self.assertEqual(code, 1)
            self.assertFalse((root / "out" / "latest").exists())
            self.assertEqual(len(list((root / "raw").glob("*/failure.json"))), 1)

    def test_override_requires_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "map.json"
            path.write_text(json.dumps({"HK:00700": {"industry": "X"}}))
            with self.assertRaises(ValueError):
                load_overrides(path, TODAY)


if __name__ == "__main__":
    unittest.main()
