# ashare_yearly：A 股新股近一年数据采集与报告

本项目**只用于搜集新股**：股票池 = 近一年（默认 365 天）内上市的**全部**新股，按上市日倒序。
指数、板块、热点章节仍然保留，但都围绕新股服务（做对比基准与情绪背景）。

数据源优先 [akshare](https://github.com/akfamily/akshare)，接口不可用时兜底东方财富 / 同花顺公开接口；
仍然取不到的字段一律标 `—`，并在报告第 6 节列出原因，**不估算、不编造**。

## 报告内容

总览页 `index.html`：

1. **指数近一年行情** —— 上证、深证成指、创业板指、科创 50、沪深 300、中证 500/1000、北证 50 的收盘线、日 K 与相对走势对比（首日=100）
2. **新股总览** —— 一行一只：上市日期、首日/最新收盘、上市以来涨跌、交易日数、首 N 日中取到分时的天数
3. **个股画像总览** —— 所属板块、行业、最新价、涨跌幅、总市值、流通市值、市盈率（静/动/TTM）、换手率、股东行数
4. **所属板块行情** —— 按新股行业聚合出前 8 个板块的近一年走势与命中成分股
5. **热点与评论** —— 东方财富千股千评、热度/活跃度榜、个股资讯；同花顺快讯与热门板块/概念
6. **数据源与缺失说明** —— 每一步的实际数据源、兜底次数与缺失原因

每只新股另有明细页 `stocks/<代码>.html`：上市至今收盘线与日 K、上市初期逐日行情与每日分时图、
前十大流通股东、主营业务、市盈率走势；总览表里点股票名即可进入。

> 为什么拆页：近一年新股常有 200~300 只，全部图表塞进单页会让 HTML 涨到几十 MB，浏览器根本打不开。

## 目录结构

```
ashare_yearly/
├── main.py            # CLI 入口
├── config.py          # 运行配置、指数清单
├── collect.py         # 采集：指数、新股名单、日线与分时
├── pipeline.py        # 画像/板块/热点采集 + 报告组装与执行
├── report.py          # HTML 渲染（总览页 + 新股明细页）
├── charts.py          # 纯 SVG 图表（折线/K 线/分时/多线对比）
├── frames.py          # DataFrame 归一化工具
├── codes.py           # 股票代码与板块判断
├── netutil.py         # 限速、重试、磁盘缓存的 HTTP 客户端
├── store.py           # 落盘与采集事件清单
├── demo.py            # 离线合成数据演示
├── sources/
│   ├── ak.py          # akshare 适配层
│   ├── eastmoney.py   # 东方财富公开接口
│   └── ths.py         # 同花顺公开接口
├── tests/             # 离线单测（不联网）
└── requirements.txt
```

## 安装与运行

```bash
pip install -r ashare_yearly/requirements.txt

python -m ashare_yearly.main                        # 采集近一年全部新股并生成报告
python -m ashare_yearly.main --deep-limit 30        # 只跑最近 30 只（压缩耗时）
python -m ashare_yearly.main --codes 301999 688008  # 只跑指定代码（调试）
python -m ashare_yearly.main --steps index new      # 只跑部分章节
python -m ashare_yearly.main --self-check           # 接口可用性自检，不写报告
python -m ashare_yearly.main --offline-demo         # 合成数据演示（只渲染总览页）
```

### 主要参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--lookback-days` | 365 | 区间长度（天） |
| `--end` | 今天 | 区间结束日 |
| `--deep-limit` | 0 | 0 = 不限，采集区间内全部新股；>0 时按上市日倒序截断 |
| `--first-days` | 7 | 每只新股采集上市初期的交易日数 |
| `--intraday-days` | 10 | 只对距今 N 天内的交易日请求分时；超期直接跳过并注明理由（0 = 关闭分时） |
| `--news-limit` | 20 | 个股资讯只取最新上市的 N 只新股 |
| `--news-per-stock` | 8 | 每只股票的资讯条数 |
| `--codes` | 无 | 只跑指定代码，用于单只复跑与调试 |
| `--steps` | 全部 | `index new profile sector sentiment report` 任选 |
| `--adjust` | qfq | 复权方式（qfq/hfq/空） |
| `--no-akshare` | - | 强制走公开接口兜底路径 |
| `--no-cache` | - | 关闭磁盘缓存 |
| `--min-interval` / `--timeout` / `--retries` | 0.35 / 20 / 3 | 请求限速与重试 |
| `--out` / `--cache-dir` | `reports/ashare-yearly` / `.cache` | 输出与缓存目录 |

## 输出

```
reports/ashare-yearly/
├── index.html                  # 总览页
├── ashare-yearly-<日期>.html   # 当日归档
├── stocks/<代码>.html         # 每只新股明细页
├── payload.json                # 结构化数据（已剔除 SVG）
├── manifest.json               # 运行参数 + 采集事件清单
└── data/                       # 原始 CSV/JSON（体积大，不入库）
```

## 自动化

工作流 `.github/workflows/ashare-yearly.yml`：工作日 17:30（北京时间）自动运行，也可在 Actions 页手动 Run workflow。
超时上限 300 分钟；提交时只提交 `reports/ashare-yearly` 下的报告，`data/` 原始数据不入库，
完整结果（含 `data/`）在 artifact `ashare-yearly-report` 里下载。

## 离线自测

```bash
python -m pytest ashare_yearly/tests -q   # 全部不联网
```

## 已知限制

- 近一年新股约 200~300 只，逐只拉日线 + 画像 + 股东，受接口限速影响，完整跑一次通常需 1~3 小时。
- 分时/分钟数据只在近期可回溯（东财分时约 5 个交易日），较早上市的新股取不到首日分时，按约定标 `—` 并写明原因。
- 千股千评经常不覆盖新股，此时改为展示得分前列个股并注明。
- 新股在首份定期报告发布前没有十大流通股东明细，属于正常缺失。
- 第三方接口字段定义会变，市盈率（静/动/TTM）口径不可直接比较，使用前请与原站交叉校验。

## 免责声明

本项目仅做公开数据汇总与展示，不构成任何投资建议。第三方接口内容版权属原平台。
