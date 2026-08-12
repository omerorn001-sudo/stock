# Google Drive 配置

系统支持三种上传方式。个人 My Drive 推荐 Apps Script；原 OAuth 与 Workspace WIF 继续保留为高级选项。

## 方案 A：Apps Script（个人 My Drive，推荐）

只需部署一次仓库中的 `apps-script/Code.gs`，并配置两个 GitHub Secrets：

- `GDRIVE_APPS_SCRIPT_URL`
- `GDRIVE_APPS_SCRIPT_TOKEN`

不需要 Google Cloud 项目、OAuth Client、Client Secret 或 Refresh Token。

完整图文式步骤见 [`APPS_SCRIPT_SETUP.md`](./APPS_SCRIPT_SETUP.md)。

## 方案 B：OAuth（个人 My Drive，高级）

配置 GitHub Secrets：

- `GDRIVE_OAUTH_CLIENT_ID`
- `GDRIVE_OAUTH_CLIENT_SECRET`
- `GDRIVE_OAUTH_REFRESH_TOKEN`
- 可选：`GDRIVE_ROOT_FOLDER_ID`

此方案直接调用 Google Drive API，但初次配置步骤较多。

## 方案 C：WIF（Google Workspace Shared Drive）

创建服务账号和 Workload Identity Provider，将服务账号加入 Shared Drive，然后配置：

- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_SERVICE_ACCOUNT`
- `GDRIVE_SHARED_DRIVE_ID`
- 可选：`GDRIVE_ROOT_FOLDER_ID`

工作流使用短期 ADC 凭据，不保存服务账号 JSON 私钥。

## 后端选择优先级

程序按以下顺序选择：

1. Apps Script；
2. OAuth；
3. WIF/ADC。

不要只配置 Apps Script 的 URL 或只配置 Token；不完整配置会在上传前明确报错。

## 目录

```text
CNINFO/机构调研/
├── YYYY/YYYY-MM/YYYY-MM-DD/*.pdf|*.doc|*.docx
└── _runs/YYYY/YYYY-MM/<run-id>/
    ├── manifest.json
    ├── manifest.csv
    └── failures.json
```

同公告 ID、同 SHA-256 会跳过；同公告 ID 内容变化会上传新内容并递增版本号。
