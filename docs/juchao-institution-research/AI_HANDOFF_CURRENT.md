# 巨潮机构调研归档系统｜当前交接状态

更新时间：2026-08-12（Asia/Shanghai）

## 1. 项目目标

从巨潮资讯网发现机构调研／投资者关系活动公告，只保留沪市、深市 A 股，排除北交所，下载 PDF、DOC、DOCX 原件，生成审计清单，并归档到 Google Drive。

## 2. 已完成能力

- 按日期范围和关键词查询巨潮公告并处理完整分页；
- 沪深 A 股筛选，排除北交所代码；
- PDF、DOC、DOCX 真实格式检测；
- SHA-256、异常隔离、JSON/CSV/失败清单；
- GitHub Artifact；
- 手动日期运行；
- 每天北京时间 20:00 回看最近 7 天；
- 历史区间按自然月串行回填；
- Google Drive Apps Script、OAuth、WIF 三种后端；
- 公告 ID 与 SHA-256 去重；
- Apps Script URL/令牌预检；
- 失败重试与汇总；
- 个人 Google Drive 真实上传和重复运行验收。

## 3. 正式文件

```text
apps-script/Code.gs
src/apps_script_storage.py
src/pipeline.py
.github/workflows/fetch.yml
.github/workflows/backfill.yml
tests/test_apps_script_storage.py
tests/test_pipeline.py
docs/juchao-institution-research/APPS_SCRIPT_SETUP.md
docs/juchao-institution-research/DRIVE_VALIDATION_RESULT.md
```

Drive 后端选择顺序：

1. `GDRIVE_APPS_SCRIPT_URL` + `GDRIVE_APPS_SCRIPT_TOKEN`；
2. OAuth 三项凭据；
3. WIF/ADC。

## 4. GitHub 托管代码验证

运行：<https://github.com/zencolab/stock/actions/runs/31599658796>

```text
install        success
compile        success
workflow YAML  success
ruff           success
pytest         28 passed
self-check     success
live probe     success
```

巨潮真实探针：原始公告 21、筛选保留 20、真实下载 3 个 PDF、隔离 0、失败 0。

## 5. Apps Script Drive 真实验收

运行：<https://github.com/zencolab/stock/actions/runs/31603784249>

第一次运行：

```text
drive_backend  apps_script
downloaded     3
drive_created  3
drive_failed   0
failures       0
```

相同参数第二次运行：

```text
drive_backend  apps_script
downloaded     3
drive_created  0
drive_skipped  3
drive_failed   0
failures       0
```

结论：真实 Google Drive 连接、写入和跨运行幂等去重均已通过。

## 6. 当前部署状态

- Apps Script Web App 已部署；
- Google Drive 授权已完成；
- 两个 GitHub Secrets 已配置并通过真实调用；
- 手动上传工作流可用；
- 每日任务会在北京时间 20:00 自动回看最近 7 天并上传；
- 无需 Google Cloud OAuth Client、Client Secret 或 Refresh Token。

## 7. 后续运维观察项

这些不是当前阻塞项：

- 观察每日定时任务连续运行情况；
- 历史回填首次正式运行时先限制每月数量；
- 后续补充真实 DOC、DOCX 在线样本；
- 定期检查 Apps Script 和 Google Drive 配额；
- 如果更新 `Code.gs`，必须重新部署新版本；
- 如果轮换上传令牌，必须同步更新 GitHub Secret。

## 8. 安全边界

- 不提交或打印任何 Secret；
- 不在 Fork PR 中使用 Drive Secrets；
- Apps Script 默认单文件安全上限为 35 MiB；
- 超限文件保留在 GitHub Artifact，并记录 Drive 上传失败；
- 验收报告只记录 Secret 是否存在，不记录 Secret 值。
