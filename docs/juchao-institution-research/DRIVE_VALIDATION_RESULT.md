# Apps Script Google Drive 真实验收结果

验收时间：2026-08-12（Asia/Shanghai）

## 结果

总体：**通过**

GitHub Actions：<https://github.com/zencolab/stock/actions/runs/31603784249>

已确认：

- `GDRIVE_APPS_SCRIPT_URL` Secret 可用；
- `GDRIVE_APPS_SCRIPT_TOKEN` Secret 可用；
- Apps Script 网关连接成功；
- 网关目录为 `CNINFO/机构调研`；
- 巨潮真实查询成功；
- 真实 PDF 下载成功；
- Google Drive 真实创建成功；
- 相同公告第二次运行全部跳过；
- Drive 上传失败为 0；
- 流水线失败为 0。

## 第一次运行

```text
原始公告             21
筛选保留             20
实际下载              3
PDF                   3
Drive 后端            apps_script
Drive 新建            3
Drive 跳过            0
Drive 失败            0
流水线失败            0
```

## 第二次运行

```text
原始公告             21
筛选保留             20
实际下载              3
PDF                   3
Drive 后端            apps_script
Drive 新建            0
Drive 跳过            3
Drive 失败            0
流水线失败            0
```

第二次运行的 `drive_skipped=3` 与 `downloaded=3` 一致，证明公告 ID 与 SHA-256 幂等去重有效。

## 安全说明

验收报告只记录 Secret 是否存在，不包含 Web App URL、上传令牌或其他凭据值。
