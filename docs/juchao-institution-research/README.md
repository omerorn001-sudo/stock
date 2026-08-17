# A 股数据自动归档系统

已完成：

1. 沪深机构调研完整发现、PDF/DOC/DOCX 原件下载和 Google Drive 归档；
2. 东方财富汇总表作为预期清单、明细表作为原始附件来源，公司与活动双重对账；
3. 手动、每天北京时间 20:00 自动回看、历史回填共用 `src.research_complete`；
4. 北交所排除、失败不静默、SHA-256 与隐藏公告 ID 幂等去重；
5. 沪深 A 股每日龙虎榜 CSV、JSON、Markdown、HTML 及 Drive 幂等归档。

## GitHub Actions

- `juchao-research-pipeline`：手动日期运行；每天北京时间 20:00 回看最近 7 天。
- `juchao-history-backfill`：历史区间按自然月串行回填。
- `A股每日龙虎榜归档`：手动指定日期；每个交易日北京时间 20:10 自动上传。

机构调研手动运行默认值：

```text
market=all
upload_drive=true
max_files=0
```

历史回填默认值：

```text
market=all
upload_drive=true
max_files_per_month=0
```

## 完整性与文件格式

- `RPT_ORG_SURVEYNEW`：生成每日沪深公司及活动预期清单；
- `RPT_ORG_SURVEY`：分页读取全部机构对象明细并按公告 URL 去重；
- 公司、活动、附件任一无法解释地缺失，任务失败并写出缺失项；
- 依据文件魔数保留 PDF、DOC、DOCX，不以 URL 后缀强制判断格式；
- 北交所代码不进入预期清单，也不下载。

文件名规则：

```text
证券代码_公司简称_公告发布日期_活动时间与方式_投资者关系活动记录表.实际格式
```

公告唯一 ID（包括 `AN...` 和巨潮数字 ID）不进入可见文件名，只作为隐藏 Drive 幂等键。

示例：

```text
000530_冰山冷热_2026-08-13_2026年8月13日_分析师会议_投资者关系活动记录表.pdf
```

## 正式验收

- 报告：[`COMPLETE_BACKFILL_VALIDATION_2026-08-17.md`](./COMPLETE_BACKFILL_VALIDATION_2026-08-17.md)
- GitHub Actions：<https://github.com/zencolab/stock/actions/runs/31997212264>
- 区间：2026-08-13 至 2026-08-16
- 47/47 家公司、49/49 项活动、47 份唯一附件；
- 38 PDF、2 DOC、7 DOCX；
- 第一次 Drive 新建 27、跳过 20、失败 0；
- 第二次新建 0、更新 0、跳过 47、失败 0。

## 文档

- Apps Script 配置：[`APPS_SCRIPT_SETUP.md`](./APPS_SCRIPT_SETUP.md)
- 龙虎榜：[`DRAGON_TIGER.md`](./DRAGON_TIGER.md)
- 全部 Drive 认证方式：[`GOOGLE_DRIVE_SETUP.md`](./GOOGLE_DRIVE_SETUP.md)

默认目录：

```text
CNINFO/机构调研/
CNINFO/龙虎榜/
```
