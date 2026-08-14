# 每日龙虎榜 Drive 归档

本功能从东方财富公开龙虎榜接口获取指定交易日数据，只保留沪市、深市 A 股，排除北交所和可转债，并逐只股票获取买入、卖出前五席位明细，然后归档到个人 Google Drive。

## 输出内容

每个交易日生成：

- `龙虎榜报告_YYYY-MM-DD.html`：优先阅读版本；支持搜索股票代码、名称、上榜原因和营业部，支持按股票折叠/展开，响应式适配电脑和手机；
- `龙虎榜_YYYY-MM-DD.csv`：龙虎榜汇总；证券代码使用六位文本格式，Google Sheets/Excel 不再删除前导零；
- `龙虎榜席位明细_YYYY-MM-DD.csv`：逐只股票、逐个上榜原因列出买入前五和卖出前五席位、买卖金额及净额；
- `龙虎榜_YYYY-MM-DD.json`：每条龙虎榜记录内嵌 `buy_seats` 和 `sell_seats`；
- `龙虎榜摘要_YYYY-MM-DD.md`：完整列出每只股票、每个上榜原因及对应买卖席位；
- `manifest.json`：GitHub Artifact 内的本次运行审计清单，不上传 Drive。

Google Drive 目录：

```text
My Drive/CNINFO/龙虎榜/YYYY/YYYY-MM/YYYY-MM-DD/
```

相同日期和文件内容重复运行时返回 `skipped`，不会创建重复文件；数据变化时创建新版本并把旧版本移入回收站。

## HTML 阅读报告

HTML 是面向日常阅读的默认文件，全部 CSS 和交互脚本都包含在单个文件中，不依赖外部网站。功能包括：

- 顶部显示上榜证券数、榜单记录数和买卖席位数；
- 输入证券代码、证券名称、上榜原因或营业部即可搜索；
- 每只股票可单独展开或折叠，也可一键全部展开/折叠；
- 每个上榜原因分别展示收盘价、涨跌幅、买入额、卖出额、净额和换手率；
- 买入前五、卖出前五并排显示，手机端自动改为上下排列；
- 支持浏览器打印或另存为 PDF。

在 Google Drive 中打开时，如果 Drive 仅显示预览，可选择 **打开方式 → 浏览器**，或下载后直接双击打开。

## 证券代码格式

CSV 中的证券代码写成表格软件可识别的六位文本公式，例如 `="000779"`。在 Google Sheets 和 Excel 中显示为 `000779`，而不是 `779`；`市场代码` 列同时保留 `000779.SZ` 或 `600000.SH`。JSON 中始终保存原始六位字符串。

## 席位口径

- 买入席位数据：`RPT_BILLBOARD_DAILYDETAILSBUY`；
- 卖出席位数据：`RPT_BILLBOARD_DAILYDETAILSSELL`；
- 按证券代码和上榜原因分别匹配；
- 每个方向最多保留金额排序前五席位；
- 记录营业部/机构名称、买入额、卖出额、净额及占总成交比例。

## Apps Script

已经部署过支持 `dataset_file` 的 `CNINFO Drive Gateway` 后，不需要为 HTML 格式再次部署 Apps Script，也不需要修改 URL、上传令牌或 GitHub Secrets。

## 手动运行

打开 <https://github.com/zencolab/stock/actions/workflows/dragon-tiger.yml>，点击 **Run workflow**：

- `trade_date`：填写 `YYYY-MM-DD`；留空使用北京时间今日；
- `upload_drive`：开启时上传 Drive，关闭时只生成 GitHub Artifact。

## 自动运行

- 每个周一至周五北京时间 **20:10**；
- 节假日无数据时正常跳过 Drive 上传；
- 失败时创建或更新 GitHub Issue，恢复后自动关闭。

数据仅供归档和研究，不构成投资建议；如与交易所披露不一致，以交易所为准。
