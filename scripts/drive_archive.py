"""把 GitHub Action 的报告归档到 Google Drive（通过 Apps Script 网关）。

规则：报告只保留 HTML 和 PDF 两种格式。
- 位置参数：主报告 HTML；默认同时用无头 Chrome 生成同名 PDF，两者一起上传。
- --pages DIR：附属 HTML 页面（如个股明细页），只上传 HTML。
- 其它格式（csv/json/md/zip …）一律不上传。

每个 dataset 在 Drive 中对应一个独立目录（见 apps-script/Code.gs 的 DATASET_DEFAULTS），
文件按 <目录>/<YYYY>/<YYYY-MM>/<YYYY-MM-DD>/ 存放；内容未变的文件自动跳过。

  python scripts/drive_archive.py --dataset daily_report --date 2026-10-09 reports/2026-10-09.html
  python scripts/drive_archive.py --dataset fund_tracker --date 2026-10-11 \
      --name 基金TOP50与持仓分析_2026-10-11 reports/funds/2026-10-11/index.html
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from src.apps_script_storage import AppsScriptDriveClient  # noqa: E402

REPORT_SUFFIXES = {".html", ".pdf"}


def render_pdf(html: Path, pdf: Path) -> Path:
    from pdf_drive import render_html_pdf  # 延迟导入：只有需要 PDF 时才依赖 Chrome

    return render_html_pdf(html, pdf)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--date", required=True, help="报告日期 YYYY-MM-DD")
    parser.add_argument("--name", default="", help="上传时的文件名（不含扩展名），仅在只有一个主报告时可用")
    parser.add_argument("--no-pdf", action="store_true", help="不生成 PDF")
    parser.add_argument("--pages", action="append", default=[], help="附属 HTML 页面目录（只传 HTML）")
    parser.add_argument("--pdf-dir", default="", help="生成的 PDF 另存一份到此目录")
    parser.add_argument("reports", nargs="*", help="主报告 HTML 文件")
    args = parser.parse_args()

    reports = [Path(p) for p in args.reports if Path(p).is_file()]
    missing = [p for p in args.reports if not Path(p).is_file()]
    for p in missing:
        print(f"::warning::未找到 {p}，已跳过")
    if args.name and len(reports) != 1:
        raise SystemExit("--name 只能配合一个主报告使用")

    client = AppsScriptDriveClient()
    folder = (client.ping().get("datasets") or {}).get(args.dataset)
    if not folder:
        raise SystemExit(
            f"Apps Script 网关不认识数据集 {args.dataset}；请更新并重新部署 apps-script/Code.gs。"
        )
    print(f"Drive 目录：{folder}/{args.date[:4]}/{args.date[:7]}/{args.date}")

    failures = 0
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        uploads: list[Path] = []
        for html in reports:
            if html.suffix.lower() not in {".html", ".htm"}:
                print(f"::warning::{html.name} 不是 HTML，按规则不上传")
                continue
            stem = args.name or html.stem
            named = work / f"{stem}.html"
            shutil.copyfile(html, named)
            uploads.append(named)
            if not args.no_pdf:
                try:
                    # 打印版：横向 A3，宽表格不被截断（只影响 PDF，不改 HTML）
                    printable = work / "print" / f"{stem}.html"
                    printable.parent.mkdir(exist_ok=True)
                    text = html.read_text(encoding="utf-8", errors="replace")
                    css = "<style>@page{size:A3 landscape;margin:8mm}</style>"
                    text = text.replace("</head>", css + "</head>", 1) if "</head>" in text else css + text
                    printable.write_text(text, encoding="utf-8")
                    pdf = render_pdf(printable, work / f"{stem}.pdf")
                    uploads.append(pdf)
                    if args.pdf_dir:
                        Path(args.pdf_dir).mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(pdf, Path(args.pdf_dir) / pdf.name)
                except Exception as exc:  # noqa: BLE001
                    failures += 1
                    print(f"::error::{html.name} 生成 PDF 失败：{exc}")
        for directory in args.pages:
            base = Path(directory)
            if not base.is_dir():
                print(f"::warning::未找到目录 {base}，已跳过")
                continue
            uploads.extend(sorted(p for p in base.iterdir() if p.is_file() and p.suffix.lower() == ".html"))

        names = [p.name for p in uploads]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise SystemExit(f"同一批次中文件名重复：{', '.join(dupes)}")

        for path in uploads:
            if path.suffix.lower() not in REPORT_SUFFIXES:
                continue
            try:
                result = client.upload_dataset_file(path, dataset=args.dataset, data_date=args.date)
                results.append({"file": path.name, **result})
                print(f"{result['status']:>8}  {result.get('drive_path')}")
            except Exception as exc:  # noqa: BLE001 - 单个文件失败不影响其余文件
                failures += 1
                results.append({"file": path.name, "status": "failed", "error": str(exc)})
                print(f"::error::{path.name} 上传失败：{exc}")

    Path("artifacts").mkdir(exist_ok=True)
    Path(f"artifacts/drive-{args.dataset}-{args.date}.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not uploads:
        print("没有可上传的文件")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
