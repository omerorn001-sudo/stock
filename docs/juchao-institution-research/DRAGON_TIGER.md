# 每日龙虎榜 Drive 归档

本功能从东方财富公开龙虎榜接口获取指定交易日数据，只保留沪市、深市 A 股，排除北交所和可转债，然后把结构化文件归档到个人 Google Drive。

## 输出内容

每个交易日生成：

- `龙虎榜_YYYY-MM-DD.csv`：便于 Excel、表格软件和数据分析；
- `龙虎榜_YYYY-MM-DD.json`：保留完整结构化字段和数据源元信息；
- `龙虎榜摘要_YYYY-MM-DD.md`：记录数量、证券数量及净买额前 10 条；
- `manifest.json`：GitHub Artifact 内的本次运行审计清单，不上传 Drive。

Google Drive 目录：

```text
My Drive/CNINFO/龙虎榜/YYYY/YYYY-MM/YYYY-MM-DD/
```

相同日期和文件内容重复运行时返回 `skipped`，不会创建重复文件；数据变化时创建新版本并把旧版本移入回收站。

## 首次启用：更新 Apps Script 部署

已有 Apps Script 用户不需要更换 Web App URL、上传令牌或 GitHub Secrets。

1. 打开当前 Apps Script 项目 `CNINFO Drive Gateway`；
2. 用仓库最新的 [`apps-script/Code.gs`](../../apps-script/Code.gs) 替换全部代码并保存；
3. 点击 **部署 → 管理部署 → 编辑**；
4. 版本选择 **新版本**；
5. 点击 **部署**。

不需要再次运行 `initialize`，原上传令牌继续有效。

## 手动运行

打开 <https://github.com/zencolab/stock/actions/workflows/dragon-tiger.yml>，点击 **Run workflow**：

- `trade_date`：填写 `YYYY-MM-DD`；留空使用北京时间今日；
- `upload_drive`：开启时上传 Drive，关闭时只生成 GitHub Artifact。

## 自动运行

- 每个周一至周五北京时间 **20:10**；
- 节假日无数据时正常跳过 Drive 上传；
- 失败时创建或更新 GitHub Issue，恢复后自动关闭。

数据仅供归档和研究，不构成投资建议；如与交易所披露不一致，以交易所为准。
