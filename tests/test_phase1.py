from __future__ import annotations

import zipfile
from datetime import date

import pytest

import src.phase1 as phase1


BASE = {
    "announcementId": "1220000001",
    "secCode": "000001",
    "secName": "示例股份",
    "announcementTitle": "<em>投资者关系活动记录表</em>",
    "announcementTime": 1741881600000,
    "adjunctUrl": "finalpage/2025-03-14/1220000001.PDF",
}


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [("PDF", "pdf"), ("doc", "doc"), ("DOCX", "docx")],
)
def test_normalize_accepts_three_native_formats(suffix, expected):
    raw = {**BASE, "adjunctUrl": f"finalpage/2025-03-14/1220000001.{suffix}"}
    row, reason = phase1.normalize_announcement(raw, "投资者关系活动记录表")
    assert reason is None
    assert row is not None
    assert row["market"] == "sz"
    assert row["format_hint"] == expected
    assert row["title"] == "投资者关系活动记录表"


@pytest.mark.parametrize("code", ["830799", "430139", "920008"])
def test_normalize_explicitly_excludes_beijing_exchange(code):
    row, reason = phase1.normalize_announcement(
        {**BASE, "secCode": code}, "投资者关系活动记录表"
    )
    assert row is None
    assert reason == "market"


def test_market_filter_can_select_shanghai_only():
    sh, reason = phase1.normalize_announcement(
        {**BASE, "secCode": "600519"}, "投资者关系活动记录表", "sh"
    )
    assert reason is None
    assert sh and sh["market"] == "sh"

    sz, reason = phase1.normalize_announcement(BASE, "投资者关系活动记录表", "sh")
    assert sz is None
    assert reason == "market_filter"


def test_attachment_url_allowlist():
    assert phase1.resolve_attachment_url("finalpage/2025/a.PDF")
    assert phase1.resolve_attachment_url("https://dataclouds.cninfo.com.cn/a.docx")
    assert phase1.resolve_attachment_url("https://example.com/a.pdf") is None
    assert phase1.resolve_attachment_url("file:///tmp/a.pdf") is None


def test_sniff_pdf_doc_and_docx(tmp_path):
    pdf = tmp_path / "a.bin"
    pdf.write_bytes(b"%PDF-1.7\nexample")
    assert phase1.sniff_format(pdf) == "pdf"

    doc = tmp_path / "b.bin"
    doc.write_bytes(phase1.OLE_MAGIC + b"legacy-word")
    assert phase1.sniff_format(doc) == "doc"

    docx = tmp_path / "c.bin"
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
    assert phase1.sniff_format(docx) == "docx"

    unknown = tmp_path / "d.bin"
    unknown.write_text("<html>rate limited</html>", encoding="utf-8")
    assert phase1.sniff_format(unknown) is None


def test_archive_filename_is_safe():
    row, _ = phase1.normalize_announcement(BASE, "投资者关系活动记录表")
    assert row is not None
    row["title"] = '标题:含/非法*字符?"'
    name = phase1.archive_filename(row, "pdf")
    assert name.endswith(".pdf")
    assert not any(char in name for char in '\\/:*?"<>|')


def test_iter_query_reads_all_pages(monkeypatch):
    pages = {
        1: {"announcements": [{"id": 1}], "hasMore": True},
        2: {"announcements": [{"id": 2}], "hasMore": False},
    }

    def fake_query(_session, _start, _end, _keyword, page):
        return pages[page]

    monkeypatch.setattr(phase1, "query_page", fake_query)
    result = list(
        phase1.iter_query(object(), date(2025, 3, 1), date(2025, 3, 2), "机构调研")
    )
    assert result == [{"id": 1}, {"id": 2}]


def test_build_payload_uses_empty_column_and_date_window():
    payload = phase1.build_payload(date(2025, 3, 1), date(2025, 3, 31), "机构调研", 2)
    assert payload["column"] == ""
    assert payload["seDate"] == "2025-03-01~2025-03-31"
    assert payload["pageNum"] == 2
