const ARTICLE_TYPE_LABELS = {
  research: "研究论文",
  review: "综述",
  editorial: "社论",
  correction: "更正",
  other: "其他",
};
const OA_LABELS = { open: "开放获取", closed: "非开放获取", unknown: "OA 未知" };
const SEARCH_DEBOUNCE_MS = 180;

export function safeExternalUrl(value) {
  if (typeof value !== "string" || !value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

export function parseAuthors(value) {
  if (typeof value !== "string" || !value) return [];
  try {
    const parsed = JSON.parse(value);
    if (!Array.isArray(parsed)) return [];
    return parsed.flatMap((author) => {
      if (typeof author === "string" && author.trim()) return [author.trim()];
      if (author && typeof author === "object" && typeof author.name === "string"
        && author.name.trim()) return [author.name.trim()];
      return [];
    });
  } catch {
    return [];
  }
}

export function paginationItems(page, pages) {
  const total = Number.isSafeInteger(pages) && pages > 0 ? pages : 1;
  const current = Math.min(Math.max(Number.isSafeInteger(page) ? page : 1, 1), total);
  if (total <= 9) return Array.from({ length: total }, (_, index) => index + 1);
  const selected = new Set([1, total]);
  for (let candidate = current - 2; candidate <= current + 2; candidate += 1) {
    if (candidate > 1 && candidate < total) selected.add(candidate);
  }
  const ordered = [...selected].sort((left, right) => left - right);
  const items = [];
  for (const candidate of ordered) {
    if (items.length && candidate - items.at(-1) > 1) items.push("ellipsis");
    items.push(candidate);
  }
  return items;
}

function required(documentRef, id) {
  const element = documentRef.getElementById(id);
  if (!element) throw new Error(`页面缺少必需控件：#${id}`);
  return element;
}

function addOption(documentRef, select, value, label) {
  const option = documentRef.createElement("option");
  option.value = String(value ?? "");
  option.textContent = String(label ?? value ?? "");
  select.append(option);
}

function replaceOptions(documentRef, select, firstLabel, items, labels = null) {
  select.replaceChildren();
  addOption(documentRef, select, "", firstLabel);
  for (const item of items) {
    const label = labels?.[item.id] ?? item.name ?? item.label ?? item.id;
    addOption(documentRef, select, item.id, label);
  }
}

function createDrawerController({ documentRef, windowRef, elements }) {
  const drawer = elements.filters;
  const openButton = elements.openFilters;
  const closeButton = elements.closeFilters;
  const overlay = elements.overlay;
  const mobileView = windowRef.matchMedia("(max-width: 820px)");
  const backgroundRegions = [...documentRef.querySelectorAll("[data-drawer-background]")];
  const abortController = new AbortController();
  let returnFocus = null;
  let backgroundState = null;

  function drawerControls() {
    return [...drawer.querySelectorAll(
      "button, input, select, [href], [tabindex]:not([tabindex='-1'])",
    )].filter((element) => !element.disabled);
  }

  function isolateBackground() {
    if (backgroundState) return;
    backgroundState = new Map(backgroundRegions.map((element) => [element, {
      inert: element.inert,
      ariaHidden: element.getAttribute("aria-hidden"),
    }]));
    for (const element of backgroundRegions) {
      element.inert = true;
      element.setAttribute("aria-hidden", "true");
    }
  }

  function restoreBackground() {
    if (!backgroundState) return;
    for (const [element, state] of backgroundState) {
      element.inert = state.inert;
      if (state.ariaHidden === null) element.removeAttribute("aria-hidden");
      else element.setAttribute("aria-hidden", state.ariaHidden);
    }
    backgroundState = null;
  }

  function clearModalState() {
    drawer.removeAttribute("role");
    drawer.removeAttribute("aria-modal");
    drawer.classList.remove("open");
    documentRef.body.classList.remove("drawer-open");
    overlay.hidden = true;
    openButton.setAttribute("aria-expanded", "false");
    restoreBackground();
  }

  function setClosedMobile() {
    if (drawer.contains(documentRef.activeElement)) openButton.focus();
    clearModalState();
    drawer.setAttribute("aria-hidden", "true");
    drawer.inert = true;
    returnFocus = null;
  }

  function setDesktop() {
    const focusNeedsRepair = drawer.classList.contains("open")
      || documentRef.activeElement === closeButton
      || documentRef.activeElement === overlay;
    clearModalState();
    drawer.inert = false;
    drawer.removeAttribute("aria-hidden");
    returnFocus = null;
    if (focusNeedsRepair) elements.dateFrom.focus();
  }

  function open() {
    if (!mobileView.matches) return;
    returnFocus = documentRef.activeElement;
    drawer.inert = false;
    drawer.removeAttribute("aria-hidden");
    drawer.setAttribute("role", "dialog");
    drawer.setAttribute("aria-modal", "true");
    drawer.classList.add("open");
    documentRef.body.classList.add("drawer-open");
    overlay.hidden = false;
    openButton.setAttribute("aria-expanded", "true");
    closeButton.focus();
    isolateBackground();
  }

  function close({ restoreFocus = true } = {}) {
    const focusTarget = returnFocus;
    clearModalState();
    if (restoreFocus && focusTarget instanceof windowRef.HTMLElement && focusTarget.isConnected) {
      focusTarget.focus();
    }
    if (mobileView.matches) {
      drawer.setAttribute("aria-hidden", "true");
      drawer.inert = true;
    } else {
      drawer.removeAttribute("aria-hidden");
      drawer.inert = false;
    }
    returnFocus = null;
  }

  function trapFocus(event) {
    if (event.key !== "Tab" || !drawer.classList.contains("open")) return;
    const controls = drawerControls();
    if (!controls.length) return;
    const first = controls[0];
    const last = controls.at(-1);
    if (event.shiftKey && documentRef.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && documentRef.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  const listenerOptions = { signal: abortController.signal };
  openButton.addEventListener("click", open, listenerOptions);
  closeButton.addEventListener("click", () => close(), listenerOptions);
  overlay.addEventListener("click", () => close(), listenerOptions);
  drawer.addEventListener("keydown", trapFocus, listenerOptions);
  documentRef.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && drawer.classList.contains("open")) close();
  }, listenerOptions);
  mobileView.addEventListener("change", () => {
    if (mobileView.matches) setClosedMobile();
    else setDesktop();
  }, listenerOptions);
  if (mobileView.matches) setClosedMobile();
  else setDesktop();

  return {
    destroy() {
      clearModalState();
      abortController.abort();
    },
  };
}

function createArticleCard(documentRef, row) {
  const article = documentRef.createElement("article");
  article.className = "article-card";
  article.dataset.articleUid = String(row.uid ?? "");
  const heading = documentRef.createElement("h2");
  const safeUrl = safeExternalUrl(row.article_url);
  const title = String(row.title || "无标题");
  if (safeUrl) {
    const link = documentRef.createElement("a");
    link.href = safeUrl;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = title;
    heading.append(link);
  } else {
    const text = documentRef.createElement("span");
    text.textContent = title;
    heading.append(text);
  }
  article.append(heading);

  const date = typeof row.published_at === "string" ? row.published_at.slice(0, 10) : "";
  const metadata = [row.journal_name, row.publisher, date, row.article_type];
  if (row.doi) metadata.push(`DOI ${row.doi}`);
  const presentMetadata = metadata.filter((value) => typeof value === "string" && value);
  if (presentMetadata.length) {
    const paragraph = documentRef.createElement("p");
    paragraph.className = "meta";
    paragraph.textContent = presentMetadata.join(" · ");
    article.append(paragraph);
  }

  const authors = parseAuthors(row.authors_json);
  if (authors.length) {
    const paragraph = documentRef.createElement("p");
    paragraph.className = "authors";
    paragraph.textContent = authors.join(" · ");
    article.append(paragraph);
  }
  if (typeof row.abstract === "string" && row.abstract.trim()) {
    const paragraph = documentRef.createElement("p");
    paragraph.className = "abstract";
    paragraph.textContent = row.abstract.trim();
    article.append(paragraph);
  }

  const chips = documentRef.createElement("div");
  chips.className = "chips";
  if (typeof row.oa_status === "string" && OA_LABELS[row.oa_status]) {
    const chip = documentRef.createElement("span");
    chip.className = `chip${row.oa_status === "open" ? " open" : ""}`;
    chip.textContent = OA_LABELS[row.oa_status];
    chips.append(chip);
  }
  if (typeof row.tag_labels === "string") {
    for (const label of row.tag_labels.split("|||").filter(Boolean)) {
      const chip = documentRef.createElement("span");
      chip.className = "chip";
      chip.textContent = label;
      chips.append(chip);
    }
  }
  if (chips.childElementCount) article.append(chips);
  return article;
}

export function createAppController({
  documentRef = globalThis.document,
  windowRef = globalThis.window,
  loadDatabaseFn,
  loadFilterOptionsFn,
  queryArticlesFn,
  defaultState,
  parseStateFn,
  serializeStateFn,
} = {}) {
  if (!documentRef || !windowRef) throw new Error("Paper Radar controller 需要浏览器 DOM。");
  for (const [name, dependency] of Object.entries({
    loadDatabaseFn, loadFilterOptionsFn, queryArticlesFn, parseStateFn, serializeStateFn,
  })) {
    if (typeof dependency !== "function") throw new TypeError(`缺少 controller 依赖：${name}`);
  }
  if (!defaultState || typeof defaultState !== "object") {
    throw new TypeError("缺少 controller 依赖：defaultState");
  }
  const elements = {
    openFilters: required(documentRef, "open-filters"),
    activeFilterCount: required(documentRef, "active-filter-count"),
    overlay: required(documentRef, "filter-overlay"),
    filters: required(documentRef, "filters"),
    closeFilters: required(documentRef, "close-filters"),
    dateFrom: required(documentRef, "date-from"),
    dateTo: required(documentRef, "date-to"),
    journal: required(documentRef, "journal"),
    publisher: required(documentRef, "publisher"),
    articleType: required(documentRef, "article-type"),
    oaStatus: required(documentRef, "oa-status"),
    tagOptions: required(documentRef, "tag-options"),
    clearFilters: required(documentRef, "clear-filters"),
    search: required(documentRef, "search"),
    sort: required(documentRef, "sort"),
    status: required(documentRef, "status"),
    resultCount: required(documentRef, "result-count"),
    articleList: required(documentRef, "article-list"),
    pagination: required(documentRef, "pagination"),
    databaseSummary: required(documentRef, "database-summary"),
  };
  const controls = {
    query: elements.search,
    from: elements.dateFrom,
    to: elements.dateTo,
    journal: elements.journal,
    publisher: elements.publisher,
    articleType: elements.articleType,
    oaStatus: elements.oaStatus,
    sort: elements.sort,
  };
  const abortController = new AbortController();
  const drawerController = createDrawerController({ documentRef, windowRef, elements });
  let state = parseStateFn(windowRef.location.search);
  let database = null;
  let options = null;
  let searchTimer = null;
  let startPromise = null;
  let bound = false;
  let destroyed = false;

  function setStatus(kind, message) {
    const messageElement = elements.status.querySelector("span:last-child") ?? elements.status;
    messageElement.textContent = message;
    elements.status.classList.toggle("error", kind === "error");
    elements.status.classList.toggle("empty", kind === "empty");
    elements.status.classList.toggle("status-sr-only", kind === "ready");
    elements.status.hidden = false;
  }

  function populateOptions(loaded) {
    replaceOptions(documentRef, elements.journal, "全部期刊", loaded.journals);
    replaceOptions(documentRef, elements.publisher, "全部出版社", loaded.publishers);
    const articleTypes = Object.keys(ARTICLE_TYPE_LABELS).map((id) => ({ id }));
    replaceOptions(
      documentRef,
      elements.articleType,
      "全部类型",
      articleTypes,
      ARTICLE_TYPE_LABELS,
    );
    const oaStatuses = Object.keys(OA_LABELS).map((id) => ({ id }));
    replaceOptions(documentRef, elements.oaStatus, "全部状态", oaStatuses, OA_LABELS);
    elements.tagOptions.replaceChildren();
    for (const item of loaded.tags) {
      const label = documentRef.createElement("label");
      const checkbox = documentRef.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = String(item.id);
      checkbox.dataset.tag = String(item.id);
      label.append(checkbox, documentRef.createTextNode(String(item.label)));
      elements.tagOptions.append(label);
    }
  }

  function reconcileState(candidate) {
    if (!options) return candidate;
    const available = (items) => new Set(items.map((item) => String(item.id)));
    const journals = available(options.journals);
    const publishers = available(options.publishers);
    const tags = available(options.tags);
    return {
      ...candidate,
      journal: journals.has(candidate.journal) ? candidate.journal : "",
      publisher: publishers.has(candidate.publisher) ? candidate.publisher : "",
      articleType: Object.hasOwn(ARTICLE_TYPE_LABELS, candidate.articleType)
        ? candidate.articleType : "",
      oaStatus: Object.hasOwn(OA_LABELS, candidate.oaStatus) ? candidate.oaStatus : "",
      tags: candidate.tags.filter((tag) => tags.has(tag)),
    };
  }

  function syncControls() {
    for (const [key, control] of Object.entries(controls)) control.value = state[key];
    for (const checkbox of elements.tagOptions.querySelectorAll("[data-tag]")) {
      checkbox.checked = state.tags.includes(checkbox.value);
    }
  }

  function readControls() {
    return {
      ...state,
      ...Object.fromEntries(Object.entries(controls).map(([key, control]) => [key, control.value])),
      tags: [...elements.tagOptions.querySelectorAll("[data-tag]:checked")]
        .map((checkbox) => checkbox.value),
      page: 1,
    };
  }

  function writeUrl() {
    const query = serializeStateFn(state);
    const url = new URL(windowRef.location.href);
    url.search = query;
    windowRef.history.replaceState(null, "", url);
  }

  function renderPagination(total, pageSize) {
    elements.pagination.replaceChildren();
    if (total === 0) return;
    const pages = Math.max(1, Math.ceil(total / pageSize));
    const addButton = (label, targetPage, optionsForButton = {}) => {
      const button = documentRef.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.dataset.page = String(targetPage);
      button.disabled = Boolean(optionsForButton.disabled);
      if (optionsForButton.current) button.setAttribute("aria-current", "page");
      elements.pagination.append(button);
    };
    addButton("← 上一页", state.page - 1, { disabled: state.page === 1 });
    for (const item of paginationItems(state.page, pages)) {
      if (item === "ellipsis") {
        const ellipsis = documentRef.createElement("span");
        ellipsis.className = "pagination-ellipsis";
        ellipsis.textContent = "…";
        ellipsis.setAttribute("aria-hidden", "true");
        elements.pagination.append(ellipsis);
      } else {
        addButton(String(item), item, { current: item === state.page });
      }
    }
    addButton("下一页 →", state.page + 1, { disabled: state.page === pages });
  }

  function updateFilterCount() {
    const scalarFilters = [
      state.query, state.from, state.to, state.journal, state.publisher,
      state.articleType, state.oaStatus,
    ];
    elements.activeFilterCount.textContent = String(
      scalarFilters.filter(Boolean).length + state.tags.length,
    );
  }

  function render({ focusPagination = false, throwOnError = false } = {}) {
    if (!database) return;
    elements.articleList.setAttribute("aria-busy", "true");
    try {
      const result = queryArticlesFn(database, state);
      state = { ...state, page: result.page };
      elements.resultCount.textContent = String(result.total);
      elements.articleList.replaceChildren();
      if (result.rows.length) {
        elements.articleList.append(...result.rows.map((row) => createArticleCard(documentRef, row)));
        setStatus("ready", `已显示 ${result.total} 篇论文，第 ${result.page} 页。`);
      } else {
        const empty = documentRef.createElement("p");
        empty.className = "empty-state";
        empty.textContent = "没有匹配的论文，请调整筛选条件。";
        elements.articleList.append(empty);
        setStatus("empty", "没有匹配的论文。");
      }
      renderPagination(result.total, result.pageSize);
      if (focusPagination) elements.pagination.querySelector('[aria-current="page"]')?.focus();
      updateFilterCount();
      writeUrl();
      return result;
    } catch (error) {
      setStatus("error", `查询失败：${error instanceof Error ? error.message : "未知错误"}`);
      if (throwOnError) throw error;
      return null;
    } finally {
      elements.articleList.setAttribute("aria-busy", "false");
    }
  }

  function cancelSearchTimer() {
    if (searchTimer !== null) windowRef.clearTimeout(searchTimer);
    searchTimer = null;
  }

  function updateFromControls() {
    cancelSearchTimer();
    state = reconcileState(readControls());
    render();
  }

  function scheduleSearch() {
    cancelSearchTimer();
    searchTimer = windowRef.setTimeout(() => {
      searchTimer = null;
      updateFromControls();
    }, SEARCH_DEBOUNCE_MS);
  }

  function scheduleImmediateSearch() {
    cancelSearchTimer();
    searchTimer = windowRef.setTimeout(() => {
      searchTimer = null;
      updateFromControls();
    }, 0);
  }

  function bind() {
    if (bound) return;
    bound = true;
    const listenerOptions = { signal: abortController.signal };
    elements.search.addEventListener("input", scheduleSearch, listenerOptions);
    elements.search.addEventListener("change", scheduleImmediateSearch, listenerOptions);
    for (const control of Object.values(controls)) {
      if (control !== elements.search) control.addEventListener("change", updateFromControls, listenerOptions);
    }
    elements.tagOptions.addEventListener("change", updateFromControls, listenerOptions);
    elements.pagination.addEventListener("click", (event) => {
      const button = event.target.closest?.("button[data-page]");
      if (!button || button.disabled) return;
      const page = Number(button.dataset.page);
      if (!Number.isSafeInteger(page) || page < 1) return;
      cancelSearchTimer();
      state = { ...reconcileState(readControls()), page };
      render({ focusPagination: true });
      windowRef.scrollTo({
        top: 0,
        behavior: windowRef.matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "auto" : "smooth",
      });
    }, listenerOptions);
    elements.clearFilters.addEventListener("click", () => {
      cancelSearchTimer();
      state = { ...defaultState, tags: [] };
      syncControls();
      render();
    }, listenerOptions);
    windowRef.addEventListener("popstate", () => {
      cancelSearchTimer();
      state = reconcileState(parseStateFn(windowRef.location.search));
      syncControls();
      render();
    }, listenerOptions);
  }

  function closeDatabaseQuietly() {
    const opened = database;
    database = null;
    try {
      opened?.close?.();
    } catch {
      // Cleanup must not replace the loading/query error shown to the user.
    }
  }

  async function startOnce() {
    elements.articleList.setAttribute("aria-busy", "true");
    setStatus("loading", "正在加载论文数据库…");
    try {
      database = await loadDatabaseFn();
      if (destroyed) {
        closeDatabaseQuietly();
        return;
      }
      options = loadFilterOptionsFn(database);
      populateOptions(options);
      state = reconcileState(state);
      syncControls();
      bind();
      const result = render({ throwOnError: true });
      elements.databaseSummary.textContent = `数据库已加载，当前视图共 ${result.total} 篇论文。`;
    } catch (error) {
      if (destroyed) return;
      closeDatabaseQuietly();
      elements.articleList.replaceChildren();
      elements.articleList.setAttribute("aria-busy", "false");
      elements.resultCount.textContent = "0";
      const message = error instanceof Error ? error.message : "未知错误";
      setStatus("error", `加载失败：${message}`);
      elements.databaseSummary.textContent = `数据库加载失败：${message}`;
    }
  }

  return {
    start() {
      if (!startPromise) startPromise = startOnce();
      return startPromise;
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      cancelSearchTimer();
      abortController.abort();
      drawerController.destroy();
      closeDatabaseQuietly();
    },
  };
}
