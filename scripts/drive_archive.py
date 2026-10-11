"""把任意 GitHub Action 的报告文件归档到 Google Drive（通过 Apps Script 网关）。

每个 dataset 在 Drive 中对应一个独立目录（见 apps-script/Code.gs 的 DATASET_DEFAULTS），
文件按 <目录>/<YYYY>/<YYYY-MM>/<YYYY-MM-DD>/ 存放；同名且内容未变的文件会自动跳过。

用法：
  python scripts/drive_archive.py --dataset fund_tracker --date 2026-10-11 reports/funds/2026-10-11
  python scripts/drive_archive.py --dataset daily_report --date 2026-10-09 reports/2026-10-09.html
  python scripts/drive_archive.py --dataset ashare_yearly --date 2026-10-06 \
      reports/ashare-yearly/ashare-yearly-2026-10-06.html --zip-dir stocks=reports/ashare-yearly/stocks
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.apps_script_storage import AppsScriptDriveClient  # noqa: E402


def collect(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(sorted(p for p in path.iterdir() if p.is_file()))
        elif path.is_file():
            files.append(path)
        else:
            print(f"::warning::未找到 {raw}，已跳过")
    names = [p.name for p in files]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise SystemExit(f"同一批次中文件名重复：{', '.join(dupes)}")
    return files


def zip_dir(spec: str, workdir: Path, date: str) -> Path | None:
    label, _, folder = spec.partition("=")
    source = Path(folder or label)
    if not source.is_dir():
        print(f"::warning::未找到目录 {source}，已跳过打包")
        return None
    target = workdir / f"{label}-{date}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(source.rglob("*")):
            if item.is_file():
                archive.write(item, item.relative_to(source))
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--date", required=True, help="报告日期 YYYY-MM-DD")
    parser.add_argument("--zip-dir", action="append", default=[],
                        help="把目录打包成 <名称>-<日期>.zip 再上传，格式 名称=目录")
    parser.add_argument("paths", nargs="*", help="要上传的文件或目录（目录只取第一层文件）")
    args = parser.parse_args()

    client = AppsScriptDriveClient()
    ping = client.ping()
    folder = (ping.get("datasets") or {}).get(args.dataset)
    if not folder:
        raise SystemExit(
            f"Apps Script 网关不认识数据集 {args.dataset}；"
            "请把 apps-script/Code.gs 更新到 Apps Script 并重新部署（新版本）。"
        )
    print(f"Drive 目录：{folder}/{args.date[:4]}/{args.date[:7]}/{args.date}")

    with tempfile.TemporaryDirectory() as tmp:
        files = collect(args.paths)
        for spec in args.zip_dir:
            packed = zip_dir(spec, Path(tmp), args.date)
            if packed:
                files.append(packed)
        if not files:
            print("没有可上传的文件")
            return 0
        failures = 0
        results = []
        for path in files:
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
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
