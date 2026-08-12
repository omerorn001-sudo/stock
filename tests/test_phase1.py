from __future__ import annotations

import zipfile
from datetime import date

import pytest

import src.phase1 as phase1

BASE = {
    "announcementId": "1220000001", "secCode": "000001", "secName": "示例股份",
    "announcementTitle": "<em>投资者关系活动记录表</em>",
    "announcementTime": 1741881600000,
    "adjunctUrl": "finalpage/2025-03-14/1220000001.PDF",
}


@pytest.mark.parametrize(("suffix", "expected"), [("PDF", "pdf"), ("doc", "doc"), ("DOCX", "docx")])
def test_normalize_accepts_three_native_formats(suffix, expected):
    raw = {**BASE, "adjunctUrl": f"finalpage/2025-03-14/1220000001.{suffix}"}
    row, reason = phase1.normalize_announcement(raw, "投资者关系活动记录表")
    assert reason is None and row is not None
    assert row["market"] == "sz" and row["format_hint"] == expected


@pytest.mark.parametrize("code", ["830799", "430139", "920008"])
def test_excludes_beijing_exchange(code):
    row, reason = phase1.normalize_announcement({**BASE, "secCode": code}, "机构调研")
    assert row is None and reason == "market"


def test_market_filter():
    row, reason = phase1.normalize_announcement({**BASE, "secCode": "600519"}, "机构调研", "sh")
    assert reason is None and row and row["market"] == "sh"


def test_attachment_url_allowlist():
    assert phase1.resolve_attachment_url("finalpage/2025/a.PDF")
    assert phase1.resolve_attachment_url("https://dataclouds.cninfo.com.cn/a.docx")
    assert phase1.resolve_attachment_url("https://example.com/a.pdf") is None


def test_sniff_pdf_doc_and_docx(tmp_path):
    pdf = tmp_path / "a.bin"; pdf.write_bytes(b"%PDF-1.7\nexample")
    assert phase1.sniff_format(pdf) == "pdf"
    doc = tmp_path / "b.bin"; doc.write_bytes(phase1.OLE_MAGIC + b"legacy-word")
    assert phase1.sniff_format(doc) == "doc"
    docx = tmp_path / "c.bin"
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
    assert phase1.sniff_format(docx) == "docx"


def test_iter_query_reads_all_pages(monkeypatch):
    pages = {1: {"announcements": [{"id": 1}], "hasMore": True},
             2: {"announcements": [{"id": 2}], "hasMore": False}}
    monkeypatch.setattr(phase1, "query_page", lambda _s, _a, _b, _k, page: pages[page])
    result = list(phase1.iter_query(object(), date(2025, 3, 1), date(2025, 3, 2), "机构调研"))
    assert result == [{"id": 1}, {"id": 2}]
