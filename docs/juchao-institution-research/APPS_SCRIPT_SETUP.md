# 使用 Apps Script 上传到个人 Google Drive（推荐）

这是个人 My Drive 最简单的配置方式：不需要创建 Google Cloud 项目，不需要 OAuth Client、Client Secret、Refresh Token，也不需要服务账号。

你只需要完成一次 Google 授权，并在 GitHub 中保存两个 Secret：

- `GDRIVE_APPS_SCRIPT_URL`
- `GDRIVE_APPS_SCRIPT_TOKEN`

## 1. 创建 Apps Script

1. 使用最终保存文件的 Google 账号登录。
2. 打开 <https://script.new>。
3. 删除编辑器中的默认代码。
4. 打开仓库文件 [`apps-script/Code.gs`](../../apps-script/Code.gs)，复制全部内容并粘贴到编辑器。
5. 将项目命名为 `CNINFO Drive Gateway`，然后保存。

## 2. 初始化并授权 Drive

1. 在编辑器顶部的函数下拉框选择 `initialize`。
2. 点击 **运行**。
3. Google 首次运行时会要求授权。选择你的账号，并允许脚本访问 Google Drive。
4. 打开底部的 **执行日志**。
5. 复制日志 JSON 中的 `uploadToken` 值。

`uploadToken` 等同密码，不要发到聊天、Issue、日志或代码中。

默认归档目录会自动创建为：

```text
My Drive/CNINFO/机构调研/
```

## 3. 部署为 Web 应用

1. 点击右上角 **部署 → 新部署**。
2. 类型选择 **Web 应用**。
3. **执行身份**选择“我”。
4. **谁有权访问**选择“任何人（Anyone）”。
5. 点击 **部署**。
6. 复制以 `/exec` 结尾的 Web App URL。

不要使用测试部署的 `/dev` 地址。

虽然 Web App 可被访问，但上传操作仍必须携带随机 `uploadToken`；脚本会拒绝错误令牌。

## 4. 添加 GitHub Secrets

打开：

<https://github.com/zencolab/stock/settings/secrets/actions>

依次添加两个 Repository secret：

| Secret 名称 | Value |
| --- | --- |
| `GDRIVE_APPS_SCRIPT_URL` | 上一步复制的 `/exec` URL |
| `GDRIVE_APPS_SCRIPT_TOKEN` | `initialize` 日志中的 `uploadToken` |

不要添加引号，不要粘贴整段 JSON。

可选 Repository variable：

| Variable 名称 | 默认值 | 用途 |
| --- | ---: | --- |
| `GDRIVE_APPS_SCRIPT_MAX_BYTES` | `36700160` | 单文件安全上限，默认 35 MiB |

通常不需要添加这个 Variable。

## 5. 首次上传测试

打开：

<https://github.com/zencolab/stock/actions/workflows/fetch.yml>

点击 **Run workflow**，填写：

| 参数 | 建议值 |
| --- | --- |
| Branch | `main` |
| 开始日期 | `2025-03-07` |
| 结束日期 | `2025-03-07` |
| 市场 | `all` |
| 上传 Google Drive | 开启 |
| 最多处理数量 | `3` |

运行中会先执行 `Check Apps Script Drive gateway`，确认 URL 和令牌有效，然后才开始下载和上传。

第一次预期：

```text
drive_backend: apps_script
drive_created: 3
drive_failed: 0
```

使用完全相同的参数再运行一次，预期：

```text
drive_skipped: 3
drive_created: 0
drive_failed: 0
```

这表示公告 ID 与 SHA-256 去重生效。

## 6. 自动任务

两个 Secret 配置完整后，每天北京时间 21:37 的任务会自动上传最近 7 天记录。

如果未配置 Drive，定时任务仍会下载并生成 GitHub Artifact，但不会上传 Drive。

## 7. 更新脚本或更换令牌

### 更新 Apps Script 代码

修改代码后，需要进入 **部署 → 管理部署 → 编辑 → 新版本 → 部署**。只保存代码不会自动更新现有 Web App 版本。

### 更换上传令牌

1. 在 Apps Script 编辑器中运行 `rotateUploadToken`。
2. 从执行日志复制新的 `uploadToken`。
3. 更新 GitHub Secret `GDRIVE_APPS_SCRIPT_TOKEN`。

旧令牌会立即失效。

## 8. 可选：指定父文件夹或目录名

默认设置最省事。如果确实需要指定父文件夹，可在脚本中临时添加并运行：

```javascript
function setupCustomArchive() {
  configureArchive('你的Drive文件夹ID', 'CNINFO/机构调研');
}
```

运行成功后可以删除这个临时函数。

## 9. 限制与安全说明

- 适合个人、低频归档；大量并发请使用 Shared Drive + WIF。
- 单文件默认不超过 35 MiB。超限文件仍保留在 GitHub Artifact，但 Drive 上传会失败并记录原因。
- Apps Script 与 Google Drive 都有每日配额；本项目的低频日更通常足够。
- 不要把 Web App URL 和令牌写入仓库。
- 不要在来自不受信任 Fork 的工作流中使用仓库 Secrets。
- 本方案已完成客户端、脚本语法和模拟上传测试；真实 Drive 上传必须在你部署 Web App 后才能验证。
