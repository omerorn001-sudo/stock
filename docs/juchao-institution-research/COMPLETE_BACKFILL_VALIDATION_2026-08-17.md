# 机构调研完整采集与正式回填验收

验收日期：2026-08-17（Asia/Shanghai）

## 结论

正式 `main` 已完成完整采集修复，并对 2026-08-13 至 2026-08-16 执行真实 Google Drive 回填和第二次幂等复验。所有强制检查通过。

运行：<https://github.com/zencolab/stock/actions/runs/31997212264>

结果文件：

```text
artifacts/research-production-backfill/result.json
```

## 完整性结果

| 指标 | 结果 |
|---|---:|
| 原始明细行 | 1415 |
| 预期公司 | 47 |
| 匹配公司 | 47 |
| 缺失公司 | 0 |
| 公司覆盖率 | 100.0% |
| 预期活动 | 49 |
| 匹配活动 | 49 |
| 缺失活动 | 0 |
| 活动覆盖率 | 100.0% |
| 唯一原始附件 | 47 |
| 成功下载 | 47 |
| 流水线失败 | 0 |

## 文件格式

| 格式 | 数量 |
|---|---:|
| PDF | 38 |
| DOC | 2 |
| DOCX | 7 |
| 合计 | 47 |

格式由文件内容魔数确定；即使附件 URL 或响应头显示 PDF，只要文件内容是 DOC/DOCX，就保留真实格式。

## Drive 第一次回填

```text
drive_created  27
drive_updated  0
drive_skipped  20
drive_failed   0
```

20 份跳过是 2026-08-13 先前完整验收已写入的无 ID 文件；27 份是 2026-08-14 至 2026-08-16 等缺失日期新建的文件。

## Drive 第二次幂等复验

```text
drive_created  0
drive_updated  0
drive_skipped  47
drive_failed   0
failures       0
```

相同区间再次执行未创建重复文件。

## 文件名与隐藏去重键

正式规则：

```text
证券代码_公司简称_公告发布日期_活动时间与方式_投资者关系活动记录表.实际格式
```

示例：

```text
000530_冰山冷热_2026-08-13_2026年8月13日_分析师会议_投资者关系活动记录表.pdf
```

验收已检查 47 份本地与 Drive 上传文件名，公告唯一 ID 均未进入文件名。东方财富 `AN...` ID 只作为 Apps Script 元数据中的幂等键。

## 运行入口

- 手动：`.github/workflows/fetch.yml`
- 每日自动：同一工作流，北京时间每天 20:00，回看最近 7 天
- 历史回填：`.github/workflows/backfill.yml`
- 三者统一运行：`python -m src.research_complete`

## 部署影响

本次未修改已部署的 `apps-script/Code.gs`，所以不需要重新部署 Apps Script，也不需要重新配置现有 Secrets。
