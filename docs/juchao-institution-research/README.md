# A 股数据自动归档系统

已完成：

1. 巨潮机构调研公告发现与 PDF/DOC/DOCX 原件接收；
2. Google Drive Apps Script、OAuth/WIF、幂等上传和版本记录；
3. 机构调研每日自动运行、7 日回看、历史回填、Artifact 和失败告警；
4. 沪深 A 股每日龙虎榜 CSV、JSON、Markdown 摘要及 Drive 幂等归档。

## GitHub Actions

- `juchao-research-pipeline`：手动日期运行、每天北京时间 20:00 回看 7 天。
- `juchao-history-backfill`：历史区间按自然月串行回填。
- `A股每日龙虎榜归档`：手动指定日期；每个交易日北京时间 20:10 自动上传。

## 文档

- Apps Script 配置：[`APPS_SCRIPT_SETUP.md`](./APPS_SCRIPT_SETUP.md)
- 龙虎榜：[`DRAGON_TIGER.md`](./DRAGON_TIGER.md)
- 全部 Drive 认证方式：[`GOOGLE_DRIVE_SETUP.md`](./GOOGLE_DRIVE_SETUP.md)

默认目录：

```text
CNINFO/机构调研/
CNINFO/龙虎榜/
```
