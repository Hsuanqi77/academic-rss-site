export const DEFAULT_PAGE_SIZE = 20;
const MAX_PAGE_SIZE = 100;
const SQLITE_HEADER = new TextEncoder().encode("SQLite format 3\0");

function rowsFromStatement(statement, params = null) {
  const rows = [];
  try {
    if (params !== null) statement.bind(params);
    while (statement.step()) rows.push({ ...statement.getAsObject() });
    return rows;
  } finally {
    statement.free();
  }
}

function selectRows(db, sql, params = null) {
  const statement = db.prepare(sql);
  return rowsFromStatement(statement, params);
}

function isSQLite(bytes) {
  return bytes.length >= 100 && SQLITE_HEADER.every((value, index) => bytes[index] === value);
}

export async function loadDatabase(options = {}) {
  const initialize = options.initSqlJs ?? globalThis.initSqlJs;
  const fetchDatabase = options.fetch ?? globalThis.fetch;
  const now = options.now ?? Date.now;
  if (typeof initialize !== "function") throw new Error("sql.js 初始化器不可用。请确认 sql-wasm.js 已加载。");
  if (typeof fetchDatabase !== "function") throw new Error("当前浏览器不支持数据库下载。");

  const SQL = await initialize({
    locateFile: (file) => (file === "sql-wasm.wasm" ? "sql-wasm.wasm" : file),
  });
  const response = await fetchDatabase(`data/papers.db?v=${now()}`, { cache: "no-store" });
  if (!response?.ok) throw new Error(`数据库下载失败：HTTP ${response?.status ?? "未知"}`);
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.length === 0) throw new Error("数据库下载结果为空。");
  if (!isSQLite(bytes)) throw new Error("下载内容不是有效的 SQLite 数据库。");
  try {
    return new SQL.Database(bytes);
  } catch (error) {
    throw new Error("SQLite 数据库无法打开，文件可能已损坏。", { cause: error });
  }
}

export function loadFilterOptions(db) {
  return {
    journals: selectRows(
      db,
      "SELECT id, name FROM journals WHERE enabled = 1 ORDER BY name, id",
    ),
    publishers: selectRows(
      db,
      `SELECT DISTINCT publisher AS id, publisher AS name
       FROM journals WHERE enabled = 1 ORDER BY publisher`,
    ),
    tags: selectRows(db, "SELECT id, label FROM tags ORDER BY label, id"),
    articleTypes: selectRows(
      db,
      `SELECT DISTINCT article_type AS id, article_type AS name
       FROM articles ORDER BY article_type`,
    ),
    oaStatuses: selectRows(
      db,
      `SELECT DISTINCT oa_status AS id, oa_status AS name
       FROM articles ORDER BY oa_status`,
    ),
  };
}

function escapeLike(value) {
  return value.replaceAll("\\", "\\\\").replaceAll("%", "\\%").replaceAll("_", "\\_");
}

function selectedTags(tags) {
  if (!Array.isArray(tags)) return [];
  return [...new Set(tags.filter((tag) => typeof tag === "string" && tag.length > 0))];
}

export function buildArticleQuery(state = {}) {
  const clauses = [];
  const params = {};
  if (typeof state.query === "string" && state.query) {
    clauses.push(
      `LOWER(COALESCE(a.title, '') || ' ' || COALESCE(a.abstract, '') || ' '
       || COALESCE(a.doi, '')) LIKE :search ESCAPE '\\'`,
    );
    params[":search"] = `%${escapeLike(state.query.toLowerCase())}%`;
  }
  if (state.from) {
    clauses.push("DATE(a.published_at) >= :from");
    params[":from"] = state.from;
  }
  if (state.to) {
    clauses.push("DATE(a.published_at) <= :to");
    params[":to"] = state.to;
  }
  if (state.journal) {
    clauses.push("a.journal_id = :journal");
    params[":journal"] = state.journal;
  }
  if (state.publisher) {
    clauses.push("j.publisher = :publisher");
    params[":publisher"] = state.publisher;
  }
  if (state.articleType) {
    clauses.push("a.article_type = :articleType");
    params[":articleType"] = state.articleType;
  }
  if (state.oaStatus) {
    clauses.push("a.oa_status = :oaStatus");
    params[":oaStatus"] = state.oaStatus;
  }
  selectedTags(state.tags).forEach((tag, index) => {
    const key = `:tag${index}`;
    clauses.push(
      `EXISTS (
        SELECT 1 FROM article_tags AS wanted${index}
        WHERE wanted${index}.article_uid = a.uid AND wanted${index}.tag_id = ${key}
      )`,
    );
    params[key] = tag;
  });
  const orderBy = state.sort === "oldest"
    ? "a.published_at ASC, a.uid ASC"
    : "a.published_at DESC, a.uid DESC";
  return {
    where: clauses.length ? `WHERE ${clauses.join(" AND ")}` : "",
    params,
    orderBy,
  };
}

function normalizePageSize(value) {
  if (!Number.isFinite(value)) return DEFAULT_PAGE_SIZE;
  return Math.min(MAX_PAGE_SIZE, Math.max(1, Math.trunc(value)));
}

function requestedPage(value) {
  return Number.isSafeInteger(value) && value > 0 ? value : 1;
}

function countArticles(db, where, params) {
  const statement = db.prepare(
    `SELECT COUNT(*) AS total
     FROM articles AS a
     JOIN journals AS j ON j.id = a.journal_id
     ${where}`,
  );
  try {
    statement.bind(params);
    if (!statement.step()) throw new Error("数据库未返回论文总数。");
    const total = Number(statement.getAsObject().total);
    if (!Number.isSafeInteger(total) || total < 0) throw new Error("数据库返回了无效的论文总数。");
    return total;
  } finally {
    statement.free();
  }
}

export function queryArticles(db, state = {}, options = {}) {
  const { where, params, orderBy } = buildArticleQuery(state);
  const pageSize = normalizePageSize(options.pageSize ?? DEFAULT_PAGE_SIZE);
  const total = countArticles(db, where, params);
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const page = Math.min(requestedPage(state.page), pages);
  const rows = selectRows(
    db,
    `SELECT a.*, j.name AS journal_name, j.publisher,
       (SELECT GROUP_CONCAT(t.id, '|||')
        FROM article_tags AS linked_ids JOIN tags AS t ON t.id = linked_ids.tag_id
        WHERE linked_ids.article_uid = a.uid) AS tag_ids,
       (SELECT GROUP_CONCAT(t.label, '|||')
        FROM article_tags AS linked_labels JOIN tags AS t ON t.id = linked_labels.tag_id
        WHERE linked_labels.article_uid = a.uid) AS tag_labels
     FROM articles AS a
     JOIN journals AS j ON j.id = a.journal_id
     ${where}
     ORDER BY ${orderBy}
     LIMIT :limit OFFSET :offset`,
    { ...params, ":limit": pageSize, ":offset": pageSize * (page - 1) },
  );
  return { rows, total, pageSize, page };
}
