"""落盘与可追溯清单（manifest）。

每一步采集都会记录：步骤名、状态、实际生效的数据源、失败原因。
报告里不可得的字段一律显示 ``—`` 并附原因，不编造数据。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

MISSING = "—"


@dataclass
class Store:
    out_dir: Path
    data_dir: Path
    events: list[dict[str, Any]] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))

    def __post_init__(self) -> None:
        self.out_dir = Path(self.out_dir)
        self.data_dir = Path(self.data_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # ---------------- 事件记录 ----------------
    def record(
        self,
        step: str,
        *,
        status: str,
        source: str | None = None,
        detail: str | None = None,
        rows: int | None = None,
    ) -> None:
        """status: ok / fallback / missing / error。"""
        self.events.append(
            {
                "time": datetime.now().astimezone().isoformat(timespec="seconds"),
                "step": step,
                "status": status,
                "source": source,
                "rows": rows,
                "detail": (detail or "")[:500] or None,
            }
        )

    def failures(self) -> list[dict[str, Any]]:
        return [e for e in self.events if e["status"] in {"missing", "error"}]

    def fallbacks(self) -> list[dict[str, Any]]:
        return [e for e in self.events if e["status"] == "fallback"]

    # ---------------- 写文件 ----------------
    def _resolve(self, rel: str, base: Path) -> Path:
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def write_csv(self, df: Any, rel: str) -> Path | None:
        if df is None or len(df) == 0:
            return None
        path = self._resolve(rel, self.data_dir)
        df.to_csv(path, index=False, encoding="utf-8-sig")
        return path

    def write_json(self, payload: Any, rel: str, *, in_data_dir: bool = True) -> Path:
        path = self._resolve(rel, self.data_dir if in_data_dir else self.out_dir)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return path

    def write_text(self, text: str, rel: str, *, in_data_dir: bool = False) -> Path:
        path = self._resolve(rel, self.data_dir if in_data_dir else self.out_dir)
        path.write_text(text, encoding="utf-8")
        return path

    # ---------------- manifest ----------------
    def manifest(self, config_summary: dict[str, Any], akshare_info: dict[str, Any]) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "config": config_summary,
            "akshare": akshare_info,
            "counts": {
                "events": len(self.events),
                "ok": len([e for e in self.events if e["status"] == "ok"]),
                "fallback": len(self.fallbacks()),
                "missing_or_error": len(self.failures()),
            },
            "events": self.events,
        }

    def save_manifest(self, config_summary: dict[str, Any], akshare_info: dict[str, Any]) -> Path:
        return self.write_json(self.manifest(config_summary, akshare_info), "manifest.json", in_data_dir=False)
