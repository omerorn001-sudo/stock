from __future__ import annotations

from datetime import date

import src.research_complete as complete

DAY = date(2026, 8, 13)
SUMMARY_SZ = {
    "SECUCODE": "000530.SZ",
    "SECURITY_CODE": "000530",
    "SECURITY_NAME_ABBR": "冰山冷热",
    "NOTICE_DATE": "2026-08-13 00:00:00",
    "RECEIVE_START_DATE": "2026-08-13 00:00:00",
    "RECEIVE_END_DATE": None,
    "RECEIVE_TIME_EXPLAIN": "2026年8月13日",
    "RECEIVE_WAY_EXPLAIN": "分析师会议",
    "IS_SOURCE": "1",
    "NUMBERNEW": "1",
}
DETAIL_SZ = {
    **SUMMARY_SZ,
    "NUMBERNEW": "20",
    "URL": "AN202608131827939441",
    "FILE_EXTENSION": "pdf",
}
SUMMARY_SH = {
    **SUMMARY_SZ,
    "SECUCODE": "600409.SH",
    "SECURITY_CODE": "600409",
    "SECURITY_NAME_ABBR": "三友化工",
    "RECEIVE_START_DATE": "2026-08-12 00:00:00",
    "RECEIVE_TIME_EXPLAIN": "2026年8月12日15:00-16:00",
    "RECEIVE_WAY_EXPLAIN": "现场交流",
}
DETAIL_SH = {
    **SUMMARY_SH,
    "NUMBERNEW": "3",
    "URL": "AN202608131827943298",
    "FILE_EXTENSION": "docx",
}


def test_summary_query_uses_frontend_filters():
    params = complete.dataset_params(complete.SUMMARY_REPORT, DAY, 1)
    assert params["filter"] == (
        '(NUMBERNEW="1")(IS_SOURCE="1")(NOTICE_DATE=\'2026-08-13\')'
    )
    assert params["pageSize"] == "50"


def test_detail_query_uses_all_fields_and_small_pages():
    params = complete.dataset_params(complete.DETAIL_REPORT, DAY, 2)
    assert params["filter"] == "(NOTICE_DATE='2026-08-13')"
    assert params["columns"] == "ALL"
    assert params["pageNumber"] == "2"
    assert params["pageSize"] == "50"


def test_record_uses_source_url_and_original_format_hint():
    pdf, reason = complete.normalize_detail(DETAIL_SZ)
    assert reason is None and pdf is not None
    assert pdf["attachment_url"] == (
        "https://pdf.dfcfw.com/pdf/H2_AN202608131827939441_1.pdf"
    )
    assert pdf["format_hint"] == "pdf"
    docx, reason = complete.normalize_detail(DETAIL_SH)
    assert reason is None and docx is not None
    assert docx["format_hint"] == "docx"


def test_filename_omits_announcement_id():
    record, _reason = complete.normalize_detail(DETAIL_SZ)
    assert record is not None
    name = complete.archive_filename(record, "pdf")
    assert name.startswith("000530_冰山冷热_2026-08-13_")
    assert "AN202608131827939441" not in name
    assert name.endswith("投资者关系活动记录表.pdf")


def test_duplicate_institution_rows_become_one_file(monkeypatch):
    second_institution = {**DETAIL_SZ, "NUMBERNEW": "21"}
    monkeypatch.setattr(
        complete, "fetch_summary_date", lambda _day, _session: [SUMMARY_SZ, SUMMARY_SH]
    )
    monkeypatch.setattr(
        complete,
        "fetch_detail_date",
        lambda _day, _session: [DETAIL_SZ, second_institution, DETAIL_SH],
    )
    rows, failures, stats = complete.fetch_range(DAY, DAY)
    assert failures == []
    assert {row["stock_code"] for row in rows} == {"000530", "600409"}
    assert stats["accepted"] == 2
    assert stats["expected_companies"] == 2
    assert stats["missing_expected_companies"] == 0
    assert stats["expected_records"] == 2
    assert stats["missing_expected_records"] == 0
    assert stats["coverage_pct"] == 100.0
    assert stats["record_coverage_pct"] == 100.0


def test_missing_company_or_activity_fails(monkeypatch):
    monkeypatch.setattr(
        complete, "fetch_summary_date", lambda _day, _session: [SUMMARY_SZ, SUMMARY_SH]
    )
    monkeypatch.setattr(
        complete, "fetch_detail_date", lambda _day, _session: [DETAIL_SZ]
    )
    _rows, failures, stats = complete.fetch_range(DAY, DAY)
    assert stats["missing_expected_companies"] == 1
    assert stats["missing_expected_records"] == 1
    assert stats["coverage_pct"] == 50.0
    assert any(item["stage"] == "completeness" for item in failures)


def test_beijing_exchange_is_excluded(monkeypatch):
    summary_bj = {
        **SUMMARY_SZ,
        "SECUCODE": "920346.BJ",
        "SECURITY_CODE": "920346",
        "SECURITY_NAME_ABBR": "北交示例",
    }
    detail_bj = {**summary_bj, "URL": "AN202608131827930000"}
    monkeypatch.setattr(
        complete, "fetch_summary_date", lambda _day, _session: [summary_bj]
    )
    monkeypatch.setattr(
        complete, "fetch_detail_date", lambda _day, _session: [detail_bj]
    )
    rows, failures, stats = complete.fetch_range(DAY, DAY)
    assert rows == []
    assert failures == []
    assert stats["expected_companies"] == 0
    assert stats["excluded_market"] == 1
