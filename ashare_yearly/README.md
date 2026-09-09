# ashare_yearly —— A 股近一年详细信息采集与报告

子项目目标：把“指数近一年行情 + 新股上市至今行情（含上市前 7 个交易日日内图）+ 个股画像（股东/市值/估值/主营/换手）+ 所属板块行情 + 东方财富与同花顺热点评论”汇总成一份自包含的 HTML 报告，并把原始数据落盘为 CSV/JSON。

**数据源策略**：`akshare` 优先；当 akshare 未安装、接口改名或临时不可用时，自动兜底到**东方财富**（push2 / push2his / datacenter / F10 / 搜索）与**同花顺**（news.10jqka / q.10jqka / basic.10jqka）公开接口。

**诚实性约定（与仓库现有报告一致）**：不可得的数据一律显示 `—`，并在报告第 6 节“数据源与缺失说明”里列出具体原因（哪个接口失败、是否兜底），**绝不估算、不插值、不编造**。

## 目录结构

```text
ashare_yearly/
├─ main.py            # 命令行入口（argparse）
├─ config.py          # 运行配置、区间计算、指数清单
├─ pipeline.py        # 编排：try_chain 多源兜底 + 各步骤采集
├─ report.py          # HTML 渲染（无模板引擎，内嵌 SVG）
├─ charts.py          # 线图/多线对比/K 线/分时图，纯手写 SVG
├─ store.py           # 落盘（CSV/JSON/HTML）与采集事件记录、manifest
├─ frames.py          # 中英文列名归一、数值清洗、区间裁剪
├─ codes.py           # 股票代码归一（600519 / sh600519 / 600519.SH）与 secid
├─ netutil.py         # 限速、重试、磁盘缓存、JSON/JSONP 解析
├─ demo.py            # 离线演示（合成数据，不联网）
├─ sources/
│  ├─ ak.py           # akshare 适配层：同一能力尝试多个函数名，免受版本改名影响
│  ├─ eastmoney.py    # 东财行情/快照/F10 股东/板块/千股千评/搜索资讯
│  └─ ths.py          # 同花顺快讯/概念榜/F10 主营
└─ tests/             # 全部离线单测（不请求网络）
```

输出（默认）：

```text
reports/ashare-yearly/
├─ index.html                    # 最新报告
├─ ashare-yearly-YYYY-MM-DD.html # 按日归档
├─ payload.json                  # 报告数据（已剔除 SVG）
├─ manifest.json                 # 本次运行的配置、akshare 状态、全部采集事件
└─ data/                         # 原始落盘 CSV（指数/新股/股东/板块/评论……）
```

## 快速开始

```bash
pip install -r ashare_yearly/requirements.txt

# 0) 不联网预览报告样式（合成数据，仅自检用）
python -m ashare_yearly.main --offline-demo

# 1) 首次部署强烈建议：先自检接口可用性
python -m ashare_yearly.main --self-check

# 2) 默认：近一年新股（最多 30 只）全量采集
python -m ashare_yearly.main --universe new --deep-limit 30

# 3) 指定个股
python -m ashare_yearly.main --universe codes --codes 600519,300750,688111

# 4) 只跑部分步骤
python -m ashare_yearly.main --steps index,sentiment,report

# 5) 禁用 akshare，直接走公开接口兜底（用于排查 akshare 问题）
python -m ashare_yearly.main --no-akshare
```

常用参数：`--universe new|codes|active|all`、`--codes`、`--deep-limit`、`--first-days`、`--steps index,new,profile,sector,sentiment,report`、`--end`、`--lookback-days`、`--adjust qfq|hfq`、`--out`、`--no-cache`、`--min-interval`、`--timeout`、`--retries`。

## 对应需求的字段与来源

| 需求 | 实现 | 首选（akshare） | 兜底 |
| --- | --- | --- | --- |
| 指数近一年图 | 8 大宽基（上证/深证/创业板/科创 50/沪深 300/中证 500/中证 1000/北证 50）收盘线 + 日 K + 归一对比 | `index_zh_a_hist` 等 | 东财 `stock/kline` |
| 新股自上市至今 | 上市日起全段日 K + 收盘线 | `stock_zh_a_hist` | 东财 `stock/kline`（前复权） |
| 上市前 7 个交易日日内图 | 每日一张分时/分钟图 + 当日行情表 | `stock_zh_a_hist_min_em`（1/5 分钟） | 东财 `kline(klt=1/5)` → `trends2` |
| 前十大流通股东 | 名次/名称/持股数/占比/增减/性质，自动回退到上一报告期 | `stock_gdfx_free_top_10_em` | 东财 `RPT_F10_EH_FREEHOLDERS` |
| 市值/流通市值/市盈率/换手率 | 全市场快照字段 + 个股详情补充 | `stock_zh_a_spot_em`、`stock_individual_info_em` | 东财 `clist`（f20/f21/f9/f114/f115/f8） |
| 主营业务 | 主营描述文本 | `stock_zyjs_ths`/`stock_zygc_em` | 东财 F10 业务分析 → 同花顺 F10 |
| 估值走势 | 近一年 PE(TTM) 曲线 | `stock_a_indicator_lg` | 无公开充分兜底，缺失时标 `—` |
| 所属板块行情图 | 按个股行业聚合，取行业板块近一年走势 | `stock_board_industry_hist_em` | 东财 `90.BKxxxx` K 线 |
| 东财热点评论 | 千股千评（得分/排名/机构参与度/关注指数）+ 人气榜 + 个股资讯 | `stock_comment_em`、`stock_hot_rank_em`、`stock_news_em` | 东财 datacenter / 成交额活跃榜 / 搜索 API |
| 同花顺热点评论 | 快讯流 + 热门概念/板块榜 | `stock_info_global_ths`、`stock_hot_rank_wc` | `news.10jqka` 推送、`q.10jqka` 概念榜 |

## 字段口径（必读）

- **市盈率(静)**：LYR，上一完整年度利润；**市盈率(动)**：东财“动态市盈率”，当期利润年化推算；**市盈率(TTM)**：最近四个季度滚动。三者**不可直接比较**，亏损股可能为空或负值。东财兜底时使用 `f9`（动）、`f114`（静）、`f115`（TTM），这组字段含义基于公开推断，建议首次使用时与网页端交叉校验一次。
- **市值单位**：接口返回元，报告按亿元展示（保留 2 位）。
- **持股数量**：沿用数据源口径（通常为股），报告按万股展示；占比为占流通股本比例。
- **复权**：个股日 K 默认前复权（`--adjust qfq`）；指数不复权。
- **区间**：默认 `今天 - 365 天` 至今天，可用 `--end` / `--lookback-days` 调整。

## 已知限制

1. **历史日内数据不一定可回溯**。东财 `trends2` 仅保留近 5 个交易日，分钟 K 线保留期也有限；akshare 的分钟接口能回溯更久但并非无限。因此“上市前 7 个交易日每日分时图”对**较早上市**的新股可能取不到：程序会依次尝试 `1 分钟 → 5 分钟 → trends2`，全部失败时画空图并标注原因，当日日 K（开/高/低/收/换手/成交额）仍然保留。若需长期回溯，建议按日定时运行并将 `data/` 归档。
2. **同花顺部分页面需 `hexin-v` Cookie**（由 JS 生成）。本项目只用无需验证的公开端点（快讯推送、概念榜 ajax、F10 主营页），仍可能被风控拦截；失败时优先走 akshare，否则标为缺失。
3. **第三方接口无 SLA**，akshare 函数名与东财字段可能变动。`sources/ak.py` 对同一能力登记了多个候选函数名，少量改名不会直接崩溃；若全部失效，`--self-check` 会直接指出。
4. **并发与频控**：默认串行、请求最小间隔 0.35s、失败指数退避重试 3 次、磁盘缓存 6 小时。不建议降低 `--min-interval`。全量深度采集（30 只×7 天分时）约需十几到几十分钟。
5. **当前开发环境无外网**，因此代码只完成了编译检查 + 离线单测 + 离线演示渲染，**尚未与 akshare / 东财 / 同花顺 真实联调**。请先跑 `--self-check` 确认可用性。

## 开发与测试

```bash
python -m compileall ashare_yearly
python -m pytest ashare_yearly/tests -q     # 全部离线，不请求网络
python -m ashare_yearly.main --offline-demo # 生成 demo.html 预览样式
```

## 自动化

`.github/workflows/ashare-yearly.yml`：工作日北京时间 17:30 自动运行（也可手动触发），流程为：安装依赖 → 接口自检（不中断）→ 离线单测 → 采集生成 → 提交 `reports/ashare-yearly` → 上传 artifact。若不希望机器提交报告，删除“提交报告”步骤即可，产物仍会上传。

## 免责声明

本项目仅做公开数据汇总与展示，不构成投资建议；请遵守数据源的使用条款与频率限制。
