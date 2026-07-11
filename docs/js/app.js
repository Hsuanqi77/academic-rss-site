import { createAppController } from "./controller.js";
import { loadDatabase, loadFilterOptions, queryArticles } from "./db.js";
import { DEFAULT_STATE, parseState, serializeState } from "./state.js";

const controller = createAppController({
  documentRef: document,
  windowRef: window,
  loadDatabaseFn: loadDatabase,
  loadFilterOptionsFn: loadFilterOptions,
  queryArticlesFn: queryArticles,
  defaultState: DEFAULT_STATE,
  parseStateFn: parseState,
  serializeStateFn: serializeState,
});

controller.start();
window.addEventListener("pagehide", (event) => {
  if (!event.persisted) controller.destroy();
});
