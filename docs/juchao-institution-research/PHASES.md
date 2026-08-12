# 巨潮机构调研归档：三部分交付状态

| 部分 | 目标 | 状态 |
| --- | --- | --- |
| 第一部分 | 巨潮数据发现与 PDF/DOC/DOCX 原件接收 | **已完成** |
| 第二部分 | Google Drive 双认证、目录、去重和修订更新 | **已完成代码** |
| 第三部分 | 每日回看、历史回填、分支验证、Artifact 和告警 | **已完成代码** |

Drive 真实上传需配置 Google Secrets。

## 运行

```bash
python -m src.pipeline \
  --start 2026-08-01 --end 2026-08-07 --market all \
  --download-files --upload-drive --output artifacts/juchao-run
```

每日回看：

```bash
python -m src.pipeline --last-days 7 --download-files --upload-drive --fail-on-empty
```
