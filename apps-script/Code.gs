/*
 * 巨潮机构调研归档：个人 Google Drive 简化网关
 *
 * 一次性设置：
 * 1. 在 script.new 中粘贴本文件；
 * 2. 运行 initialize() 并授权 Drive，复制执行日志中的 uploadToken；
 * 3. 部署为 Web App：Execute as Me / Who has access: Anyone；
 * 4. 将 /exec URL 与 token 保存到 GitHub Secrets。
 */

const DEFAULT_BASE_PATH = 'CNINFO/机构调研';
const DEFAULT_MAX_BYTES = 35 * 1024 * 1024;

function initialize() {
  const properties = PropertiesService.getScriptProperties();
  let token = properties.getProperty('UPLOAD_TOKEN');
  if (!token) {
    token = newToken_();
    properties.setProperty('UPLOAD_TOKEN', token);
  }
  if (!properties.getProperty('BASE_PATH')) {
    properties.setProperty('BASE_PATH', DEFAULT_BASE_PATH);
  }
  if (!properties.getProperty('MAX_BYTES')) {
    properties.setProperty('MAX_BYTES', String(DEFAULT_MAX_BYTES));
  }
  const info = setupInfo_(true);
  console.log(JSON.stringify(info, null, 2));
  return info;
}

function rotateUploadToken() {
  const token = newToken_();
  PropertiesService.getScriptProperties().setProperty('UPLOAD_TOKEN', token);
  const info = setupInfo_(true);
  console.log(JSON.stringify(info, null, 2));
  return info;
}

function configureArchive(rootFolderId, basePath) {
  const properties = PropertiesService.getScriptProperties();
  if (rootFolderId) {
    DriveApp.getFolderById(rootFolderId).getName();
    properties.setProperty('ROOT_FOLDER_ID', String(rootFolderId).trim());
  } else {
    properties.deleteProperty('ROOT_FOLDER_ID');
  }
  properties.setProperty('BASE_PATH', String(basePath || DEFAULT_BASE_PATH).trim());
  const info = setupInfo_(false);
  console.log(JSON.stringify(info, null, 2));
  return info;
}

function doGet() {
  return jsonOutput_({
    ok: true,
    service: 'CNINFO Drive Gateway',
    configured: Boolean(PropertiesService.getScriptProperties().getProperty('UPLOAD_TOKEN')),
    basePath: basePath_().join('/'),
  });
}

function doPost(event) {
  try {
    const text = event && event.postData ? event.postData.contents : '';
    if (!text) {
      return jsonOutput_({ok: false, code: 'empty_request', error: '请求正文为空'});
    }
    const payload = JSON.parse(text);
    verifyToken_(payload.token);
    if (payload.operation === 'ping') {
      return jsonOutput_({
        ok: true,
        result: {service: 'CNINFO Drive Gateway', base_path: basePath_().join('/')},
      });
    }

    const lock = LockService.getScriptLock();
    if (!lock.tryLock(30000)) {
      throw new Error('另一个上传任务正在运行，请稍后重试');
    }
    try {
      let result;
      if (payload.operation === 'upsert') {
        result = upsertAnnouncement_(payload);
      } else if (payload.operation === 'run_file') {
        result = upsertRunFile_(payload);
      } else {
        throw new Error('不支持的 operation: ' + payload.operation);
      }
      return jsonOutput_({ok: true, result: result});
    } finally {
      lock.releaseLock();
    }
  } catch (error) {
    return jsonOutput_({
      ok: false,
      code: 'request_failed',
      error: error && error.message ? error.message : String(error),
    });
  }
}

function upsertAnnouncement_(payload) {
  requireFields_(payload, [
    'announcement_id',
    'publish_date',
    'sha256',
    'file_name',
    'mime_type',
    'content_base64',
  ]);
  const publishDate = String(payload.publish_date);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(publishDate)) {
    throw new Error('publish_date 必须是 YYYY-MM-DD');
  }
  const segments = basePath_().concat([
    publishDate.slice(0, 4),
    publishDate.slice(0, 7),
    publishDate,
  ]);
  const metadata = {
    cninfo_announcement_id: String(payload.announcement_id),
    cninfo_sha256: String(payload.sha256),
    cninfo_stock_code: String(payload.stock_code || ''),
    cninfo_publish_date: publishDate,
    cninfo_format: String(payload.format_detected || ''),
  };
  return upsertFile_(segments, payload, metadata);
}

function upsertRunFile_(payload) {
  requireFields_(payload, [
    'run_id',
    'sha256',
    'file_name',
    'mime_type',
    'content_base64',
  ]);
  const runId = String(payload.run_id);
  const year = /^\d{6}/.test(runId) ? runId.slice(0, 4) : 'unknown';
  const yearMonth = /^\d{6}/.test(runId)
    ? runId.slice(0, 4) + '-' + runId.slice(4, 6)
    : 'unknown';
  const syntheticId = 'run:' + runId + ':' + String(payload.file_name);
  const segments = basePath_().concat(['_runs', year, yearMonth, runId]);
  const metadata = {
    cninfo_announcement_id: syntheticId,
    cninfo_sha256: String(payload.sha256),
    cninfo_run_id: runId,
    cninfo_format: String(payload.file_name).split('.').pop().toLowerCase(),
  };
  return upsertFile_(segments, payload, metadata);
}

function upsertFile_(segments, payload, metadata) {
  const folder = ensurePath_(rootFolder_(), segments);
  const existing = findByAnnouncementId_(folder, metadata.cninfo_announcement_id);
  const oldMetadata = existing ? readMetadata_(existing) : {};
  const oldVersion = Number(oldMetadata.cninfo_version || 0);
  const drivePath = segments.concat([String(payload.file_name)]).join('/');

  if (existing && oldMetadata.cninfo_sha256 === metadata.cninfo_sha256) {
    return {
      status: 'skipped',
      file_id: existing.getId(),
      drive_path: drivePath,
      version: oldVersion || 1,
      web_view_link: existing.getUrl(),
    };
  }

  const bytes = Utilities.base64Decode(String(payload.content_base64));
  const maxBytes = Number(
    PropertiesService.getScriptProperties().getProperty('MAX_BYTES') || DEFAULT_MAX_BYTES
  );
  if (bytes.length > maxBytes) {
    throw new Error('文件超过 Apps Script 上限：' + bytes.length + ' > ' + maxBytes);
  }

  const version = oldVersion + 1;
  const blob = Utilities.newBlob(
    bytes,
    String(payload.mime_type || 'application/octet-stream'),
    String(payload.file_name)
  );
  const created = folder.createFile(blob);
  metadata.cninfo_version = String(version);
  created.setDescription(JSON.stringify(metadata));
  if (existing) {
    existing.setTrashed(true);
  }
  return {
    status: existing ? 'updated' : 'created',
    file_id: created.getId(),
    drive_path: drivePath,
    version: version,
    web_view_link: created.getUrl(),
  };
}

function rootFolder_() {
  const rootId = PropertiesService.getScriptProperties().getProperty('ROOT_FOLDER_ID');
  return rootId ? DriveApp.getFolderById(rootId) : DriveApp.getRootFolder();
}

function basePath_() {
  const value =
    PropertiesService.getScriptProperties().getProperty('BASE_PATH') || DEFAULT_BASE_PATH;
  const segments = String(value)
    .split('/')
    .map(function (item) { return item.trim(); })
    .filter(function (item) { return Boolean(item); });
  if (!segments.length) {
    throw new Error('BASE_PATH 不能为空');
  }
  return segments;
}

function ensurePath_(root, segments) {
  let parent = root;
  segments.forEach(function (name) {
    const found = parent.getFoldersByName(String(name));
    parent = found.hasNext() ? found.next() : parent.createFolder(String(name));
  });
  return parent;
}

function findByAnnouncementId_(folder, announcementId) {
  const files = folder.getFiles();
  while (files.hasNext()) {
    const file = files.next();
    const metadata = readMetadata_(file);
    if (metadata.cninfo_announcement_id === String(announcementId)) {
      return file;
    }
  }
  return null;
}

function readMetadata_(file) {
  try {
    return JSON.parse(file.getDescription() || '{}');
  } catch (error) {
    return {};
  }
}

function verifyToken_(provided) {
  const expected = PropertiesService.getScriptProperties().getProperty('UPLOAD_TOKEN');
  if (!expected) {
    throw new Error('网关尚未初始化，请先运行 initialize()');
  }
  if (!provided || String(provided) !== expected) {
    throw new Error('上传令牌无效');
  }
}

function requireFields_(payload, fields) {
  fields.forEach(function (field) {
    if (payload[field] === undefined || payload[field] === null || payload[field] === '') {
      throw new Error('缺少字段：' + field);
    }
  });
}

function newToken_() {
  return (
    Utilities.getUuid().replace(/-/g, '') +
    Utilities.getUuid().replace(/-/g, '')
  );
}

function setupInfo_(includeToken) {
  const properties = PropertiesService.getScriptProperties();
  const result = {
    basePath: basePath_().join('/'),
    rootFolderId: properties.getProperty('ROOT_FOLDER_ID') || 'My Drive root',
    maxBytes: Number(properties.getProperty('MAX_BYTES') || DEFAULT_MAX_BYTES),
  };
  if (includeToken) {
    result.uploadToken = properties.getProperty('UPLOAD_TOKEN');
  }
  return result;
}

function jsonOutput_(payload) {
  return ContentService.createTextOutput(JSON.stringify(payload)).setMimeType(
    ContentService.MimeType.JSON
  );
}
