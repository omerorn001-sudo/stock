# Google Drive 配置

系统支持两种认证方式，二选一。

## 个人 My Drive

配置 GitHub Secrets：`GDRIVE_OAUTH_CLIENT_ID`、`GDRIVE_OAUTH_CLIENT_SECRET`、`GDRIVE_OAUTH_REFRESH_TOKEN`，以及可选的 `GDRIVE_ROOT_FOLDER_ID`。

## Workspace Shared Drive

创建服务账号和 Workload Identity Provider，将服务账号加入 Shared Drive，然后配置：`GCP_WORKLOAD_IDENTITY_PROVIDER`、`GCP_SERVICE_ACCOUNT`、`GDRIVE_SHARED_DRIVE_ID`，以及可选的 `GDRIVE_ROOT_FOLDER_ID`。

工作流使用短期 ADC 凭据，不保存服务账号 JSON 私钥。

## 目录

```text
CNINFO/机构调研/
├── YYYY/YYYY-MM/YYYY-MM-DD/*.pdf|*.doc|*.docx
└── _runs/YYYY/YYYY-MM/<run-id>/
    ├── manifest.json
    ├── manifest.csv
    └── failures.json
```

同公告 ID、同 SHA-256 会跳过；同公告 ID 内容变化会更新同一 Drive 文件并递增版本。
