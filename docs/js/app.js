const status = document.querySelector("#status span:last-child");
const drawer = document.querySelector("#filters");
const openButton = document.querySelector("#open-filters");
const closeButton = document.querySelector("#close-filters");
const overlay = document.querySelector("#filter-overlay");
const mobileView = window.matchMedia("(max-width: 820px)");
const backgroundRegions = [...document.querySelectorAll("[data-drawer-background]")];
const desktopFocusTarget = document.querySelector("#date-from");

let returnFocus = null;
let backgroundState = null;

function drawerControls() {
  return [...drawer.querySelectorAll("button, input, select, [href], [tabindex]:not([tabindex='-1'])")]
    .filter((element) => !element.disabled);
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
  document.body.classList.remove("drawer-open");
  overlay.hidden = true;
  openButton.setAttribute("aria-expanded", "false");
  restoreBackground();
}

function setClosedMobile() {
  if (drawer.contains(document.activeElement)) openButton.focus();
  clearModalState();
  drawer.setAttribute("aria-hidden", "true");
  drawer.inert = true;
  returnFocus = null;
}

function setDesktop() {
  const focusNeedsRepair = drawer.classList.contains("open")
    || document.activeElement === closeButton
    || document.activeElement === overlay;
  clearModalState();
  drawer.inert = false;
  drawer.removeAttribute("aria-hidden");
  returnFocus = null;
  if (focusNeedsRepair) desktopFocusTarget.focus();
}

function openDrawer() {
  if (!mobileView.matches) return;
  returnFocus = document.activeElement;
  drawer.inert = false;
  drawer.removeAttribute("aria-hidden");
  drawer.setAttribute("role", "dialog");
  drawer.setAttribute("aria-modal", "true");
  drawer.classList.add("open");
  document.body.classList.add("drawer-open");
  overlay.hidden = false;
  openButton.setAttribute("aria-expanded", "true");
  closeButton.focus();
  isolateBackground();
}

function closeDrawer({ restoreFocus = true } = {}) {
  const focusTarget = returnFocus;
  clearModalState();
  if (restoreFocus && focusTarget instanceof HTMLElement && focusTarget.isConnected) focusTarget.focus();
  if (mobileView.matches) {
    drawer.setAttribute("aria-hidden", "true");
    drawer.inert = true;
  } else {
    drawer.removeAttribute("aria-hidden");
    drawer.inert = false;
  }
  returnFocus = null;
}

function trapDrawerFocus(event) {
  if (event.key !== "Tab" || !drawer.classList.contains("open")) return;
  const controls = drawerControls();
  if (!controls.length) return;
  const first = controls[0];
  const last = controls[controls.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

openButton.addEventListener("click", openDrawer);
closeButton.addEventListener("click", () => closeDrawer());
overlay.addEventListener("click", () => closeDrawer());
drawer.addEventListener("keydown", trapDrawerFocus);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && drawer.classList.contains("open")) closeDrawer();
});
mobileView.addEventListener("change", () => {
  if (mobileView.matches) setClosedMobile();
  else setDesktop();
});

if (mobileView.matches) setClosedMobile();
else setDesktop();
status.textContent = "界面已准备，等待数据库查询模块。";
