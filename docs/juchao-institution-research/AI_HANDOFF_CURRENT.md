# 巨潮机构调研归档系统｜当前交接状态

更新时间：2026-08-12（Asia/Shanghai）

## 1. 项目目标

从巨潮资讯网发现机构调研／投资者关系活动公告，只保留沪市、深市 A 股，排除北交所，下载 PDF、DOC、DOCX 原件，生成审计清单，并可归档到 Google Drive。

## 2. 已完成能力

- 按日期范围和关键词查询巨潮公告并处理完整分页；
- 沪深 A 股筛选，排除北交所代码；
- PDF、DOC、DOCX 真实格式检测；
- SHA-256、异常隔离、JSON/CSV/失败清单；
- GitHub Artifact；
- 手动日期运行；
- 每天北京时间 21:37 回看最近 7 天；
- 历史区间按自然月串行回填；
- Google Drive Apps Script、OAuth、WIF 三种后端；
- 公告 ID 与 SHA-256 去重；
- Apps Script URL/令牌预检；
- 失败重试与汇总。

## 3. Apps Script 正式文件

```text
apps-script/Code.gs
src/apps_script_storage.py
src/pipeline.py
.github/workflows/fetch.yml
.github/workflows/backfill.yml
tests/test_apps_script_storage.py
tests/test_pipeline.py
docs/juchao-institution-research/APPS_SCRIPT_SETUP.md
```

后端选择顺序：

1. `GDRIVE_APPS_SCRIPT_URL` + `GDRIVE_APPS_SCRIPT_TOKEN`；
2. OAuth 三项凭据；
3. WIF/ADC。

## 4. 已执行的 GitHub 托管验证

运行：<https://github.com/zencolab/stock/actions/runs/31599658796>

结果：

```text
install        success
compile        success
workflow YAML  success
ruff           success
pytest         28 passed
self-check     success
live probe     success
```

巨潮真实探针：

```text
原始公告 21
筛选保留 20
真实下载 3
PDF 3
隔离 0
失败 0
```

该运行未配置真实 Google Drive，因此不能把它描述为真实 Drive 上传验收。

## 5. 唯一尚需 Google 账号所有者完成的步骤

GitHub 仓库权限不能替代 Google 账号授权。真实 Drive 上传前，Google 账号所有者必须：

1. 在 <https://script.new> 创建脚本；
2. 粘贴 `apps-script/Code.gs`；
3. 运行 `initialize` 并授权 Drive；
4. 部署为“执行身份：我、访问者：任何人”的 Web App；
5. 得到 `/exec` URL 和 `uploadToken`；
6. 将它们分别保存为 GitHub Secrets：
   - `GDRIVE_APPS_SCRIPT_URL`
   - `GDRIVE_APPS_SCRIPT_TOKEN`

完整步骤：`docs/juchao-institution-research/APPS_SCRIPT_SETUP.md`。

不要让用户把 URL 或令牌粘贴到聊天中。

## 6. 真实 Drive 验收

配置两个 Secret 后，在 `juchao-research-pipeline` 手动运行：

```text
branch          main
start           2025-03-07
end             2025-03-07
market          all
upload_drive    true
max_files       3
```

第一次应满足：

```text
drive_backend  apps_script
drive_created  3
drive_failed   0
failures       0
```

使用相同参数再次运行，应满足：

```text
drive_skipped  3
drive_created  0
drive_failed   0
failures       0
```

## 7. 安全边界

- 不提交或打印任何 Secret；
- 不在 Fork PR 中使用 Drive Secrets；
- Apps Script 默认单文件安全上限为 35 MiB；
- 超限文件保留在 GitHub Artifact，并记录 Drive 上传失败；
- 真实 Drive 上传只能在 Google 授权完成后测试。
