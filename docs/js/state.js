const MAX_SEARCH_LENGTH = 8_192;
const MAX_QUERY_LENGTH = 512;
const MAX_ID_LENGTH = 128;
const MAX_TAGS = 32;
const MAX_PAGE = 1_000_000;
const ARTICLE_TYPES = new Set(["research", "review", "editorial", "correction", "other"]);
const OA_STATUSES = new Set(["open", "closed", "unknown"]);
const SORTS = new Set(["latest", "oldest"]);
const EMPTY_TAGS = Object.freeze([]);

export const DEFAULT_STATE = Object.freeze({
  query: "",
  from: "",
  to: "",
  journal: "",
  publisher: "",
  articleType: "",
  oaStatus: "",
  tags: EMPTY_TAGS,
  sort: "latest",
  page: 1,
});

function defaults() {
  return { ...DEFAULT_STATE, tags: [] };
}

function safeText(value, maximum, { trim = false } = {}) {
  if (typeof value !== "string") return "";
  const candidate = trim ? value.trim() : value;
  if (candidate.length > maximum || /[\u0000-\u001f\u007f]/u.test(candidate)) return "";
  return candidate;
}

function safeId(value) {
  const candidate = safeText(value, MAX_ID_LENGTH, { trim: true });
  return candidate && /^[\p{L}\p{N}._:-]+$/u.test(candidate) ? candidate : "";
}

function validDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/u.test(value)) return "";
  const [year, month, day] = value.split("-").map(Number);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return parsed.getUTCFullYear() === year
    && parsed.getUTCMonth() === month - 1
    && parsed.getUTCDate() === day
    ? value
    : "";
}

function validPage(value) {
  const raw = typeof value === "number" ? String(value) : value;
  if (typeof raw !== "string" || !/^[1-9]\d*$/u.test(raw)) return 1;
  const page = Number(raw);
  return Number.isSafeInteger(page) && page <= MAX_PAGE ? page : 1;
}

function uniqueTags(values) {
  if (!Array.isArray(values)) return [];
  const tags = [];
  const seen = new Set();
  for (const value of values) {
    const tag = safeId(value);
    if (!tag || seen.has(tag)) continue;
    seen.add(tag);
    tags.push(tag);
    if (tags.length === MAX_TAGS) break;
  }
  return tags;
}

function normalizeState(state) {
  const source = state && typeof state === "object" ? state : {};
  const articleType = safeId(source.articleType);
  const oaStatus = safeId(source.oaStatus);
  const sort = safeId(source.sort);
  return {
    query: safeText(source.query, MAX_QUERY_LENGTH),
    from: validDate(safeText(source.from, 10)),
    to: validDate(safeText(source.to, 10)),
    journal: safeId(source.journal),
    publisher: safeId(source.publisher),
    articleType: ARTICLE_TYPES.has(articleType) ? articleType : "",
    oaStatus: OA_STATUSES.has(oaStatus) ? oaStatus : "",
    tags: uniqueTags(source.tags),
    sort: SORTS.has(sort) ? sort : "latest",
    page: validPage(source.page),
  };
}

export function parseState(search = "") {
  if (typeof search !== "string" || search.length > MAX_SEARCH_LENGTH) return defaults();
  const params = new URLSearchParams(search);
  return normalizeState({
    query: params.get("q") ?? "",
    from: params.get("from") ?? "",
    to: params.get("to") ?? "",
    journal: params.get("journal") ?? "",
    publisher: params.get("publisher") ?? "",
    articleType: params.get("type") ?? "",
    oaStatus: params.get("oa") ?? "",
    tags: params.getAll("tag"),
    sort: params.get("sort") ?? "latest",
    page: params.get("page") ?? "1",
  });
}

export function serializeState(state = DEFAULT_STATE) {
  const normalized = normalizeState(state);
  const params = new URLSearchParams();
  const pairs = [
    ["q", normalized.query],
    ["from", normalized.from],
    ["to", normalized.to],
    ["journal", normalized.journal],
    ["publisher", normalized.publisher],
    ["type", normalized.articleType],
    ["oa", normalized.oaStatus],
  ];
  for (const [key, value] of pairs) if (value) params.set(key, value);
  for (const tag of normalized.tags) params.append("tag", tag);
  if (normalized.sort !== DEFAULT_STATE.sort) params.set("sort", normalized.sort);
  if (normalized.page !== DEFAULT_STATE.page) params.set("page", String(normalized.page));
  return params.toString();
}
