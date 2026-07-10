const status = document.querySelector("#status span:last-child");
const drawer = document.querySelector("#filters");
const openButton = document.querySelector("#open-filters");
const closeButton = document.querySelector("#close-filters");
const overlay = document.querySelector("#filter-overlay");
const mobileView = window.matchMedia("(max-width: 820px)");

let returnFocus = null;

function drawerControls() {
  return [...drawer.querySelectorAll("button, input, select, [href], [tabindex]:not([tabindex='-1'])")]
    .filter((element) => !element.disabled);
}

function setClosedAccessibility() {
  if (mobileView.matches && !drawer.classList.contains("open")) {
    drawer.setAttribute("aria-hidden", "true");
    drawer.inert = true;
  } else {
    drawer.removeAttribute("aria-hidden");
    drawer.inert = false;
  }
}

function openDrawer() {
  if (!mobileView.matches) return;
  returnFocus = document.activeElement;
  drawer.inert = false;
  drawer.removeAttribute("aria-hidden");
  drawer.classList.add("open");
  document.body.classList.add("drawer-open");
  overlay.hidden = false;
  openButton.setAttribute("aria-expanded", "true");
  closeButton.focus();
}

function closeDrawer({ restoreFocus = true } = {}) {
  drawer.classList.remove("open");
  document.body.classList.remove("drawer-open");
  overlay.hidden = true;
  openButton.setAttribute("aria-expanded", "false");
  setClosedAccessibility();
  if (restoreFocus && returnFocus instanceof HTMLElement) returnFocus.focus();
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
  if (!mobileView.matches) closeDrawer({ restoreFocus: false });
  setClosedAccessibility();
});

setClosedAccessibility();
status.textContent = "界面已准备，等待数据库查询模块。";
