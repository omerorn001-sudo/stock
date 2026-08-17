# 机构调研归档系统｜当前交接状态

更新时间：2026-08-17（Asia/Shanghai）

## 1. 当前目标与数据口径

按公告日期完整归档沪深机构调研／投资者关系活动原始附件，排除北交所。东方财富汇总报表 `RPT_ORG_SURVEYNEW` 是每日预期公司和活动清单，明细报表 `RPT_ORG_SURVEY` 提供原始公告 URL。两份清单必须完成公司级和活动级对账；不可解释缺失会让任务失败。

## 2. 正式实现

正式生产入口：

```text
src/research_complete.py
```

所有运行方式均调用：

```bash
python -m src.research_complete
```

相关文件：

```text
src/research_complete.py
src/apps_script_storage.py
src/pipeline.py
tests/test_research_complete.py
.github/workflows/fetch.yml
.github/workflows/backfill.yml
.github/workflows/research-production-backfill.yml
```

主要规则：

- 东方财富明细接口使用 `columns=ALL`、`pageSize=50` 稳定分页；
- 沪市、深市纳入，北交所排除；
- 公司和活动覆盖率必须为 100%；
- 附件根据文件魔数保留 PDF、DOC、DOCX；
- 公告 ID 只作为隐藏去重元数据；
- 可见文件名不得包含东方财富 `AN...` 或巨潮数字公告 ID；
- 同名不同内容仅追加 `_2`、`_3`；
- 手动默认 `max_files=0`、`upload_drive=true`；
- 历史回填默认 `max_files_per_month=0`、`upload_drive=true`；
- 每天北京时间 20:00 自动回看最近 7 天。

## 3. 正式 main 提交

```text
13855ca0f090667e59af1591dc2676f32a45573b  完整采集实现与测试
5b55f42fa6e19458fe240bac3c9f056cefd0fdfe  手动、自动、回填统一入口
b3b07b11e906ed29f08dff3e84b966091ded82ad  正式回填与幂等验收结果
```

## 4. 真实生产验收

运行：<https://github.com/zencolab/stock/actions/runs/31997212264>

区间：2026-08-13 至 2026-08-16。

第一次运行：

```text
raw                         1415
expected_companies          47
matched_expected_companies  47
missing_expected_companies  0
coverage_pct                100.0
expected_records            49
matched_expected_records    49
missing_expected_records    0
record_coverage_pct         100.0
accepted/downloaded         47/47
pdf/doc/docx                 38/2/7
drive_created               27
drive_updated               0
drive_skipped               20
drive_failed                0
failures                    0
```

第二次相同参数运行：

```text
downloaded      47
drive_created   0
drive_updated   0
drive_skipped   47
drive_failed    0
failures        0
```

结论：完整性、原始格式、无 ID 文件名、真实 Drive 上传和跨运行幂等均通过。

文件名示例：

```text
000530_冰山冷热_2026-08-13_2026年8月13日_分析师会议_投资者关系活动记录表.pdf
```

## 5. 部署状态

- Apps Script Web App 已部署且现有 URL、Token 继续有效；
- 本次只修改 GitHub 采集程序与工作流，不需要重新部署 Apps Script；
- 无需重新配置 GitHub Secrets；
- 当前日期 2026-08-17 尚未结束，由北京时间 20:00 的自动任务继续完整回看。

## 6. 旧文件处理

早期程序生成的少量带巨潮数字 ID 的文件可能仍保留在 Drive，作为 legacy 文件。本次没有自动删除或重命名它们，原因是现有 Apps Script 网关只提供幂等写入，不提供删除接口；程序从本次起只创建无 ID 文件名。若要清理 legacy 文件，优先在 Drive 手动删除，避免为一次性清理重新部署 Apps Script。

## 7. 安全与运维

- 不提交或打印 Secret；
- Apps Script 默认单文件安全上限为 35 MiB；
- 如果后续修改 `apps-script/Code.gs`，才需要部署新版本；
- 如果轮换上传令牌，才需要同步更新 GitHub Secret；
- 每日运行若出现公司、活动或附件缺失，应按失败处理，不得忽略。
