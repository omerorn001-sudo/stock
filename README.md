# A股每日涨跌幅 TOP100 报告

每个交易日收盘后自动生成沪深两市A股（**不含北交所**）**涨幅前100 + 跌幅前100** 的明细 HTML 报告。

## 报告内容（每股）

- 股票代码、名称、所属板块（沪主板 / 深主板含原中小板 / 创业板 / 科创板）
- 涨跌幅、收盘价、开盘价、成交额
- 当日换手率、五日换手率（近5个交易日单日换手率之和）
- 流通市值、流通股/总股本占比、总市值
- 最新披露前十大流通股东（含个人/基金/QFII 等类型标注）
- 近半年前复权收盘价走势图（内嵌 SVG）

## 数据来源与诚实性

- 行情快照、K线、流通股东：**东方财富**公开行情接口
- K线兜底：东方财富限流时自动改用**腾讯财经**公开接口（该源换手率由 成交量÷流通股本 推算，报告中以 `*` 标注）
- 无法取得的数据以 `—` 标示并注明原因，**不编造、不臆测**
- 新上市股票尚未披露定期报告时，流通股东一栏会注明"暂无披露"

## 自动运行（GitHub Actions）

工作流：`.github/workflows/daily-report.yml`

- **定时**：每周一至周五 UTC 08:30（北京时间 16:30，收盘后）自动运行
- **非交易日**：脚本检查上证指数最新K线日期，非交易日自动跳过，不产出文件
- **产出**：报告提交回本仓库 `reports/YYYY-MM-DD.html`，并同步更新 `reports/latest.html`
- 也可在 Actions 页面手动点 **Run workflow** 立即执行

> 注意：GitHub 定时任务可能延迟数分钟到数十分钟，属正常现象。

## 部署步骤

1. 在 GitHub 新建仓库（公开或私有均可），将本目录全部文件推送上去：
   ```bash
   git init && git add -A && git commit -m "init"
   git branch -M main
   git remote add origin https://github.com/<你的用户名>/<仓库名>.git
   git push -u origin main
   ```
2. 进入仓库 **Settings → Actions → General → Workflow permissions**，勾选 **Read and write permissions**（否则无法把报告提交回仓库）。
3. 进入 **Actions** 页签，若提示启用工作流则点击启用；可先手动 **Run workflow** 验证一次。
4. （可选）开启 **GitHub Pages**（Settings → Pages → Deploy from branch → main → 根目录），即可通过
   `https://<用户名>.github.io/<仓库名>/reports/latest.html` 在线查看最新报告。

## 本地运行

需要 [Bun](https://bun.sh)（≥1.0）：

```bash
bun run src/main.ts
# 补跑指定交易日（仅测试用，历史榜单快照不可回溯，只建议当日使用）：
REPORT_DATE=2026-07-29 bun run src/main.ts
```

产出在 `reports/` 目录。

## 注意事项

- 涨跌幅榜快照必须在**收盘后、当日采集**——接口只提供实时/延时快照，无法回溯历史某日的完整榜单，因此错过的交易日无法补跑。
- 数据接口为第三方公开接口，若其变更或对 GitHub 服务器限流，工作流会失败并在 Actions 页面显示日志；脚本已内置重试与腾讯源兜底。
- 报告仅供参考，不构成投资建议。

## 目录结构

```
├── .github/workflows/daily-report.yml   # 定时任务
├── src/main.ts                          # 抓取 + 生成报告（单文件，无第三方依赖）
├── reports/                             # 自动生成的每日报告
└── README.md
```
