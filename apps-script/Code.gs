/* CNINFO Drive Gateway: institution research, market datasets and US stock news */

const DEFAULT_BASE_PATH = 'CNINFO/机构调研';
const DEFAULT_DRAGON_TIGER_PATH = 'CNINFO/龙虎榜';
const DEFAULT_US_STOCK_NEWS_PATH = '美股资讯/每小时新闻';
const DEFAULT_MAX_BYTES = 35 * 1024 * 1024;

/* 每个 GitHub Action 一个 Drive 目录；可用 configureDataset(dataset, path) 覆盖 */
const DATASET_DEFAULTS = {
  dragon_tiger: DEFAULT_DRAGON_TIGER_PATH,
  ashare_yearly: 'CNINFO/A股新股报告',
  daily_report: 'CNINFO/A股每日涨跌幅报告',
  fund_tracker: 'CNINFO/基金TOP50与持仓分析'
};

function initialize() {
  const p = PropertiesService.getScriptProperties();

  let token = p.getProperty('UPLOAD_TOKEN');
  if (!token) {
    token = newToken_();
    p.setProperty('UPLOAD_TOKEN', token);
  }

  if (!p.getProperty('BASE_PATH')) {
    p.setProperty('BASE_PATH', DEFAULT_BASE_PATH);
  }

  if (!p.getProperty('DATASET_DRAGON_TIGER_PATH')) {
    p.setProperty(
      'DATASET_DRAGON_TIGER_PATH',
      DEFAULT_DRAGON_TIGER_PATH
    );
  }

  if (!p.getProperty('US_STOCK_NEWS_PATH')) {
    p.setProperty(
      'US_STOCK_NEWS_PATH',
      DEFAULT_US_STOCK_NEWS_PATH
    );
  }

  if (!p.getProperty('MAX_BYTES')) {
    p.setProperty(
      'MAX_BYTES',
      String(DEFAULT_MAX_BYTES)
    );
  }

  const info = setupInfo_(true);
  console.log(JSON.stringify(info, null, 2));
  return info;
}

function rotateUploadToken() {
  const token = newToken_();

  PropertiesService
    .getScriptProperties()
    .setProperty('UPLOAD_TOKEN', token);

  const info = setupInfo_(true);
  console.log(JSON.stringify(info, null, 2));
  return info;
}

function configureArchive(rootFolderId, basePath) {
  const p = PropertiesService.getScriptProperties();

  if (rootFolderId) {
    DriveApp
      .getFolderById(String(rootFolderId).trim())
      .getName();

    p.setProperty(
      'ROOT_FOLDER_ID',
      String(rootFolderId).trim()
    );
  } else {
    p.deleteProperty('ROOT_FOLDER_ID');
  }

  p.setProperty(
    'BASE_PATH',
    String(basePath || DEFAULT_BASE_PATH).trim()
  );

  return setupInfo_(false);
}

function configureDataset(dataset, basePath) {
  const d = String(dataset);
  if (!Object.prototype.hasOwnProperty.call(DATASET_DEFAULTS, d)) {
    throw new Error('不支持的数据集：' + d + '；可选：' + Object.keys(DATASET_DEFAULTS).join(', '));
  }
  PropertiesService.getScriptProperties().setProperty(
    datasetKey_(d),
    String(basePath || DATASET_DEFAULTS[d]).trim()
  );
  return setupInfo_(false);
}

/**
 * 设置美股资讯专用目录。
 */
function configureUsStockNews(basePath) {
  const path = String(
    basePath || DEFAULT_US_STOCK_NEWS_PATH
  ).trim();

  if (!path) {
    throw new Error(
      'US_STOCK_NEWS_PATH 不能为空'
    );
  }

  PropertiesService
    .getScriptProperties()
    .setProperty(
      'US_STOCK_NEWS_PATH',
      path
    );

  return {
    usStockNewsPath:
      usStockNewsPath_().join('/')
  };
}

/**
 * 查看美股资讯目录配置。
 */
function getUsStockNewsInfo() {
  return {
    usStockNewsPath:
      usStockNewsPath_().join('/'),

    rootFolderId:
      PropertiesService
        .getScriptProperties()
        .getProperty('ROOT_FOLDER_ID') ||
      'My Drive root'
  };
}

function doGet() {
  const p = PropertiesService.getScriptProperties();

  return jsonOutput_({
    ok: true,

    service:
      'CNINFO Drive Gateway',

    configured:
      Boolean(
        p.getProperty('UPLOAD_TOKEN') ||
        p.getProperty('AGENT_UPLOAD_TOKEN')
      ),

    basePath:
      basePath_().join('/'),

    datasets: datasetPaths_(),

    agentUploadConfigured:
      Boolean(
        p.getProperty(
          'AGENT_UPLOAD_TOKEN'
        )
      )
  });
}

function doPost(event) {
  try {
    const text =
      event && event.postData
        ? event.postData.contents
        : '';

    if (!text) {
      return jsonOutput_({
        ok: false,
        code: 'empty_request',
        error: '请求正文为空'
      });
    }

    const payload = JSON.parse(text);

    verifyToken_(payload.token);

    if (payload.operation === 'ping') {
      return jsonOutput_({
        ok: true,
        result: {
          service:
            'CNINFO Drive Gateway',

          base_path:
            basePath_().join('/'),

          datasets: datasetPaths_(),

          us_stock_news_path:
            usStockNewsPath_().join('/')
        }
      });
    }

    const lock =
      LockService.getScriptLock();

    if (!lock.tryLock(30000)) {
      throw new Error(
        '另一个上传任务正在运行，请稍后重试'
      );
    }

    try {
      let result;

      if (payload.operation === 'upsert') {
        result =
          upsertAnnouncement_(payload);

      } else if (
        payload.operation === 'run_file'
      ) {
        result =
          upsertRunFile_(payload);

      } else if (
        payload.operation ===
        'us_stock_news_file'
      ) {
        result =
          upsertUsStockNewsFile_(payload);

      } else if (
        payload.operation ===
        'dataset_file'
      ) {
        /*
         * 保留原函数名，确保兼容旧程序。
         */
        result =
          upsertDatasetFile(payload);

      } else {
        throw new Error(
          '不支持的 operation: ' +
          payload.operation
        );
      }

      return jsonOutput_({
        ok: true,
        result: result
      });

    } finally {
      lock.releaseLock();
    }

  } catch (error) {
    return jsonOutput_({
      ok: false,
      code: 'request_failed',

      error:
        error && error.message
          ? error.message
          : String(error)
    });
  }
}

function upsertAnnouncement_(p) {
  requireFields_(p, [
    'announcement_id',
    'publish_date',
    'sha256',
    'file_name',
    'mime_type',
    'content_base64'
  ]);

  validateDate_(
    String(p.publish_date),
    'publish_date'
  );

  const d = String(p.publish_date);

  const segments = basePath_().concat([
    d.slice(0, 4),
    d.slice(0, 7),
    d
  ]);

  return upsertFile_(
    segments,
    p,
    {
      cninfo_announcement_id:
        String(p.announcement_id),

      cninfo_sha256:
        String(p.sha256),

      cninfo_stock_code:
        String(p.stock_code || ''),

      cninfo_publish_date:
        d,

      cninfo_format:
        String(p.format_detected || '')
    }
  );
}

function upsertRunFile_(p) {
  requireFields_(p, [
    'run_id',
    'sha256',
    'file_name',
    'mime_type',
    'content_base64'
  ]);

  const runId = String(p.run_id);
  const hasDatePrefix =
    /^\d{6}/.test(runId);

  const year = hasDatePrefix
    ? runId.slice(0, 4)
    : 'unknown';

  const ym = hasDatePrefix
    ? runId.slice(0, 4) +
      '-' +
      runId.slice(4, 6)
    : 'unknown';

  return upsertFile_(
    basePath_().concat([
      'runs',
      year,
      ym,
      runId
    ]),
    p,
    {
      cninfo_announcement_id:
        'run:' +
        runId +
        ':' +
        String(p.file_name),

      cninfo_sha256:
        String(p.sha256),

      cninfo_run_id:
        runId,

      cninfo_format:
        String(p.file_name)
          .split('.')
          .pop()
          .toLowerCase()
    }
  );
}

/**
 * 美股资讯专用上传。
 *
 * 目录：
 * 美股资讯/每小时新闻/runs/YYYY/YYYY-MM/run_id/
 */
function upsertUsStockNewsFile_(p) {
  requireFields_(p, [
    'run_id',
    'sha256',
    'file_name',
    'mime_type',
    'content_base64'
  ]);

  const runId = String(p.run_id);
  const hasDatePrefix =
    /^\d{6}/.test(runId);

  const year = hasDatePrefix
    ? runId.slice(0, 4)
    : 'unknown';

  const ym = hasDatePrefix
    ? runId.slice(0, 4) +
      '-' +
      runId.slice(4, 6)
    : 'unknown';

  return upsertFile_(
    usStockNewsPath_().concat([
      'runs',
      year,
      ym,
      runId
    ]),
    p,
    {
      cninfo_announcement_id:
        'us-stock-news:' +
        runId +
        ':' +
        String(p.file_name),

      cninfo_sha256:
        String(p.sha256),

      cninfo_run_id:
        runId,

      cninfo_format:
        String(p.file_name)
          .split('.')
          .pop()
          .toLowerCase()
    }
  );
}

/**
 * 保留原函数名，兼容旧程序。
 */
function upsertDatasetFile(p) {
  requireFields_(p, [
    'dataset',
    'data_date',
    'sha256',
    'file_name',
    'mime_type',
    'content_base64'
  ]);

  const dataset =
    String(p.dataset);

  const d =
    String(p.data_date);

  validateDate_(
    d,
    'data_date'
  );

  return upsertFile_(
    datasetPath_(dataset).concat([
      d.slice(0, 4),
      d.slice(0, 7),
      d
    ]),
    p,
    {
      cninfo_announcement_id:
        'dataset:' +
        dataset +
        ':' +
        d +
        ':' +
        String(p.file_name),

      cninfo_sha256:
        String(p.sha256),

      cninfo_dataset:
        dataset,

      cninfo_data_date:
        d,

      cninfo_format:
        String(p.file_name)
          .split('.')
          .pop()
          .toLowerCase()
    }
  );
}

function upsertFile_(
  segments,
  p,
  metadata
) {
  const name =
    String(p.file_name || '');

  if (
    !name ||
    /[\\/]/.test(name)
  ) {
    throw new Error(
      'file_name 不能为空且不能包含路径分隔符'
    );
  }

  const folder =
    ensurePath_(
      rootFolder_(),
      segments
    );

  const existing =
    findByAnnouncementId_(
      folder,
      metadata.cninfo_announcement_id
    );

  const old = existing
    ? readMetadata_(existing)
    : {};

  const oldVersion =
    Number(
      old.cninfo_version || 0
    );

  const drivePath =
    segments
      .concat([name])
      .join('/');

  if (
    existing &&
    old.cninfo_sha256 ===
      metadata.cninfo_sha256
  ) {
    return {
      status: 'skipped',
      file_id: existing.getId(),
      drive_path: drivePath,
      version: oldVersion || 1,
      web_view_link: existing.getUrl()
    };
  }

  const bytes =
    Utilities.base64Decode(
      String(p.content_base64)
    );

  const max =
    Number(
      PropertiesService
        .getScriptProperties()
        .getProperty('MAX_BYTES') ||
      DEFAULT_MAX_BYTES
    );

  if (bytes.length > max) {
    throw new Error(
      '文件超过 Apps Script 上限'
    );
  }

  const version =
    oldVersion + 1;

  const blob =
    Utilities.newBlob(
      bytes,

      String(
        p.mime_type ||
        'application/octet-stream'
      ),

      name
    );

  const created =
    folder.createFile(blob);

  metadata.cninfo_version =
    String(version);

  created.setDescription(
    JSON.stringify(metadata)
  );

  if (existing) {
    existing.setTrashed(true);
  }

  return {
    status:
      existing
        ? 'updated'
        : 'created',

    file_id:
      created.getId(),

    drive_path:
      drivePath,

    version:
      version,

    web_view_link:
      created.getUrl()
  };
}

function rootFolder_() {
  const id =
    PropertiesService
      .getScriptProperties()
      .getProperty(
        'ROOT_FOLDER_ID'
      );

  return id
    ? DriveApp.getFolderById(id)
    : DriveApp.getRootFolder();
}

function basePath_() {
  return splitPath_(
    PropertiesService
      .getScriptProperties()
      .getProperty('BASE_PATH') ||
    DEFAULT_BASE_PATH,

    'BASE_PATH'
  );
}

/**
 * 获取美股资讯独立路径。
 */
function usStockNewsPath_() {
  return splitPath_(
    PropertiesService
      .getScriptProperties()
      .getProperty(
        'US_STOCK_NEWS_PATH'
      ) ||
    DEFAULT_US_STOCK_NEWS_PATH,

    'US_STOCK_NEWS_PATH'
  );
}

function datasetKey_(dataset) {
  return 'DATASET_' + String(dataset).toUpperCase() + '_PATH';
}

function datasetPath_(dataset) {
  const d = String(dataset);
  if (!Object.prototype.hasOwnProperty.call(DATASET_DEFAULTS, d)) {
    throw new Error('不支持的数据集：' + dataset);
  }
  const key = datasetKey_(d);
  return splitPath_(
    PropertiesService.getScriptProperties().getProperty(key) || DATASET_DEFAULTS[d],
    key
  );
}

function datasetPaths_() {
  const result = {};
  Object.keys(DATASET_DEFAULTS).forEach(function (k) {
    result[k] = datasetPath_(k).join('/');
  });
  return result;
}

function splitPath_(
  value,
  label
) {
  const segments =
    String(value)
      .split('/')
      .map(function (item) {
        return item.trim();
      })
      .filter(Boolean);

  if (!segments.length) {
    throw new Error(
      label + ' 不能为空'
    );
  }

  return segments;
}

function ensurePath_(
  root,
  segments
) {
  let folder = root;

  segments.forEach(
    function (name) {
      const folderName =
        String(name);

      const found =
        folder.getFoldersByName(
          folderName
        );

      folder = found.hasNext()
        ? found.next()
        : folder.createFolder(
            folderName
          );
    }
  );

  return folder;
}

function findByAnnouncementId_(
  folder,
  id
) {
  const files =
    folder.getFiles();

  while (files.hasNext()) {
    const file =
      files.next();

    const metadata =
      readMetadata_(file);

    if (
      metadata
        .cninfo_announcement_id ===
      String(id)
    ) {
      return file;
    }
  }

  return null;
}

function readMetadata_(file) {
  try {
    return JSON.parse(
      file.getDescription() ||
      '{}'
    );
  } catch (error) {
    return {};
  }
}

/**
 * 保持原 Token 验证逻辑。
 */
function verifyToken_(provided) {
  const properties =
    PropertiesService
      .getScriptProperties();

  const acceptedTokens = [
    properties.getProperty(
      'UPLOAD_TOKEN'
    ),

    properties.getProperty(
      'AGENT_UPLOAD_TOKEN'
    )
  ].filter(Boolean);

  if (!acceptedTokens.length) {
    throw new Error(
      '网关尚未初始化，请先运行 initialize()'
    );
  }

  const receivedToken =
    String(provided || '');

  if (
    !receivedToken ||
    !acceptedTokens.includes(
      receivedToken
    )
  ) {
    throw new Error(
      '上传令牌无效'
    );
  }
}

function requireFields_(
  p,
  fields
) {
  fields.forEach(
    function (field) {
      if (
        p[field] === undefined ||
        p[field] === null ||
        p[field] === ''
      ) {
        throw new Error(
          '缺少字段：' +
          field
        );
      }
    }
  );
}

function validateDate_(
  value,
  field
) {
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(
      String(value)
    )
  ) {
    throw new Error(
      field +
      ' 必须是 YYYY-MM-DD'
    );
  }
}

function newToken_() {
  return (
    Utilities
      .getUuid()
      .replace(/-/g, '') +

    Utilities
      .getUuid()
      .replace(/-/g, '')
  );
}

function setupInfo_(
  includeToken
) {
  const p =
    PropertiesService
      .getScriptProperties();

  /*
   * 保留原有返回结构，
   * 避免影响旧程序。
   */
  const result = {
    basePath:
      basePath_().join('/'),

    datasets: datasetPaths_(),

    rootFolderId:
      p.getProperty(
        'ROOT_FOLDER_ID'
      ) ||
      'My Drive root',

    maxBytes:
      Number(
        p.getProperty(
          'MAX_BYTES'
        ) ||
        DEFAULT_MAX_BYTES
      ),

    agentUploadConfigured:
      Boolean(
        p.getProperty(
          'AGENT_UPLOAD_TOKEN'
        )
      )
  };

  if (includeToken) {
    result.uploadToken =
      p.getProperty(
        'UPLOAD_TOKEN'
      );
  }

  return result;
}

function jsonOutput_(payload) {
  return ContentService
    .createTextOutput(
      JSON.stringify(payload)
    )
    .setMimeType(
      ContentService
        .MimeType
        .JSON
    );
}
