# 巨潮机构调研记录归档系统

已完成：

1. 巨潮数据发现与 PDF/DOC/DOCX 原件接收；
2. Google Drive OAuth/WIF、幂等上传和版本管理；
3. 每日自动运行、7 日回看、历史回填、Artifact 和失败告警。

仅覆盖沪市、深市 A 股，排除北交所。保存原始文件，不执行 Word 宏。

## GitHub Actions

- `juchao-research-pipeline`：Push 验证、手动日期运行、每天北京时间 21:37 回看 7 天。
- `juchao-history-backfill`：输入历史区间，按自然月串行回填。

Drive 配置见 [`GOOGLE_DRIVE_SETUP.md`](./GOOGLE_DRIVE_SETUP.md)。
