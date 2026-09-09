"""项目配置与指数清单。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
REPO_ROOT = PROJECT_DIR.parent

DEFAULT_OUT_DIR = REPO_ROOT / "reports" / "ashare-yearly"
DEFAULT_CACHE_DIR = PROJECT_DIR / ".cache"

ALL_STEPS: tuple[str, ...] = ("index", "new", "profile", "sector", "sentiment", "report")
DEFAULT_STEPS: tuple[str, ...] = ALL_STEPS
UNIVERSES: tuple[str, ...] = ("new", "codes", "active", "all")


@dataclass(frozen=True)
class IndexSpec:
    """指数定义。

    ``code``      6 位代码（akshare ``index_zh_a_hist`` 用）
    ``secid``     东方财富 secid（兜底行情用）
    ``ak_symbol`` 带市场前缀代码（akshare ``stock_zh_index_daily_em`` 用）
    """

    code: str
    name: str
    secid: str
    ak_symbol: str


INDEXES: tuple[IndexSpec, ...] = (
    IndexSpec("000001", "上证指数", "1.000001", "sh000001"),
    IndexSpec("399001", "深证成指", "0.399001", "sz399001"),
    IndexSpec("399006", "创业板指", "0.399006", "sz399006"),
    IndexSpec("000688", "科创50", "1.000688", "sh000688"),
    IndexSpec("000300", "沪深300", "1.000300", "sh000300"),
    IndexSpec("000905", "中证500", "1.000905", "sh000905"),
    IndexSpec("000852", "中证1000", "1.000852", "sh000852"),
    IndexSpec("899050", "北证50", "0.899050", "bj899050"),
)


@dataclass
class Config:
    """一次采集运行的全部参数。"""

    end: date = field(default_factory=date.today)
    lookback_days: int = 365
    universe: str = "new"
    codes: tuple[str, ...] = ()
    deep_limit: int = 30
    first_days: int = 7
    steps: tuple[str, ...] = DEFAULT_STEPS
    out_dir: Path = DEFAULT_OUT_DIR
    data_dir: Path | None = None
    cache_dir: Path = DEFAULT_CACHE_DIR
    use_akshare: bool = True
    use_cache: bool = True
    min_interval: float = 0.35
    timeout: float = 20.0
    retries: int = 3
    news_per_stock: int = 8
    adjust: str = "qfq"
    indexes: tuple[IndexSpec, ...] = INDEXES

    def __post_init__(self) -> None:
        self.out_dir = Path(self.out_dir)
        self.data_dir = Path(self.data_dir) if self.data_dir else self.out_dir / "data"
        self.cache_dir = Path(self.cache_dir)
        if self.universe not in UNIVERSES:
            raise ValueError(f"universe 需为 {UNIVERSES} 之一，收到 {self.universe!r}")
        bad = [s for s in self.steps if s not in ALL_STEPS]
        if bad:
            raise ValueError(f"未知步骤 {bad}，可选 {ALL_STEPS}")

    # ---------------- 派生属性 ----------------
    @property
    def start(self) -> date:
        return self.end - timedelta(days=self.lookback_days)

    @property
    def start_ymd(self) -> str:
        return self.start.strftime("%Y%m%d")

    @property
    def end_ymd(self) -> str:
        return self.end.strftime("%Y%m%d")

    @property
    def start_dash(self) -> str:
        return self.start.strftime("%Y-%m-%d")

    @property
    def end_dash(self) -> str:
        return self.end.strftime("%Y-%m-%d")

    def enabled(self, step: str) -> bool:
        return step in self.steps

    def summary(self) -> dict[str, object]:
        return {
            "区间": f"{self.start_dash} ~ {self.end_dash}",
            "股票池": self.universe,
            "指定代码": list(self.codes),
            "深度采集上限": self.deep_limit,
            "新股首日数": self.first_days,
            "执行步骤": list(self.steps),
            "复权方式": self.adjust or "不复权",
            "启用akshare": self.use_akshare,
            "输出目录": str(self.out_dir),
        }
