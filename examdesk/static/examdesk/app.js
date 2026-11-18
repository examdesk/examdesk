// Boosted responses. A full page for the current URL (a refresh, or a POST that redirects back)
// swaps in only its live parts ([data-live]) and new toasts, and replaces the history entry. The
// same path with another query morphs the page: from a link (a sort, a page) at the top, from a
// form (a filter, a step) at the same scroll position. A POST that redirects back resets its form
// and closes the open dialog. Other pages replace <body>.
document.addEventListener("htmx:after:request", (event) => {
  const { ctx } = event.detail;
  const url = new URL(ctx.response.raw.url);
  const isFullPage = ctx.text.startsWith("<!doctype html>") && ctx.response.status < 400;
  const isPost = ctx.request.method !== "GET";
  const isRedirectAfterPost = isPost && ctx.response.raw.redirected;
  if (!isFullPage || (isPost && !isRedirectAfterPost) || url.pathname !== location.pathname) return;
  if (ctx.request.headers["HX-History-Restore-Request"]) return;
  if (url.search === location.search) {
    ctx.swap = "none";
    ctx.selectOOB = "[data-live]:outerMorph, #toasts:beforeend";
    ctx.replace = "true";
  } else if (ctx.request.headers["HX-Request-Type"] === "full") {
    ctx.swap = ctx.sourceElement.localName === "a" ? "innerMorph show:top" : "innerMorph show:none";
  } else {
    return; // a refresh of the page's previous query
  }
  if (!isRedirectAfterPost) return;
  ctx.sourceElement.reset();
  document.querySelector("dialog[open]")?.close();
});
// After a POST, the element it came from (the submit button, a dialog's opener, else its nearest
// visible ancestor) stays at the same screen position.
const scrollAnchors = new WeakMap(); // POST request → [element, top] for the anchor and its visible ancestors
document.addEventListener("htmx:before:request", (event) => {
  const { ctx } = event.detail;
  if (event.defaultPrevented || ctx.request.method === "GET") return;
  const submitter = ctx.sourceEvent?.submitter ?? ctx.sourceElement;
  const dialog = submitter.closest("dialog");
  const anchorTops = [];
  let element = dialog ? document.querySelector(`[commandfor="${dialog.id}"]`) : submitter;
  for (; element && element !== document.body; element = element.parentElement) {
    if (element.getClientRects().length) anchorTops.push([element, element.getBoundingClientRect().top]);
  }
  scrollAnchors.set(ctx, anchorTops);
});
document.addEventListener("htmx:after:swap", (event) => {
  const [element, top] = scrollAnchors.get(event.detail.ctx)?.find(([element]) => element.isConnected && element.getClientRects().length) ?? [];
  if (element) scrollBy(0, element.getBoundingClientRect().top - top);
});
// A form re-rendered by a POST (its errors, a prompt) morphs into the page at the same scroll
// position. Another error page replaces <body>, as a browser would show it.
document.addEventListener("htmx:before:swap", (event) => {
  const { ctx, tasks } = event.detail;
  const isFormRerender = ctx.request.method !== "GET" && !ctx.response.raw.redirected;
  if (!ctx.request.headers["HX-Boosted"] || !(isFormRerender || ctx.response.status >= 400)) return;
  for (const task of tasks.filter(({ type }) => type === "main")) {
    task.swapSpec.style = isFormRerender ? "innerMorph" : "innerHTML";
    if (isFormRerender) delete task.swapSpec.show;
  }
});
// Swapped-in live parts leave focus where it is, and Tom Select is rebuilt in them (a morph would
// strip its markup; htmx.onLoad below rebuilds it).
document.addEventListener("htmx:before:swap", (event) => {
  for (const task of event.detail.tasks) {
    if (task.swapSpec.style === "none") continue;
    task.target.querySelectorAll(".tomselected").forEach((field) => field.tomselect.destroy());
    if (task.type === "oob") task.fragment.querySelectorAll("[autofocus]").forEach((field) => field.removeAttribute("autofocus"));
  }
});
// A morph doesn't hide a part with unsent text in it, such as an answer being corrected.
htmx.registerExtension("keep-unsent-text-visible", {
  htmx_before_morph_attr: (element, { attrName }) =>
    attrName !== "hidden" || ![...element.querySelectorAll("input, textarea")].some((field) => field.value !== field.defaultValue),
});
// While a link's page loads, a bar at the top runs and <main> is marked busy, which tailwind.css
// dims after 100 ms. A newer page load aborts it (`hx-sync` on <body>).
const isLinkNavigation = ({ sourceEvent, request }) => sourceEvent?.type === "click" && request.headers["HX-Boosted"];
document.documentElement.insertAdjacentHTML("beforeend", '<div id="page-loading-bar" aria-hidden="true"></div>');
const loadingBar = document.getElementById("page-loading-bar");
document.addEventListener("htmx:before:request", (event) => {
  if (event.defaultPrevented || !isLinkNavigation(event.detail.ctx)) return;
  delete loadingBar.dataset.state;
  loadingBar.offsetWidth; // forces a reflow, so the transition restarts from 0
  loadingBar.dataset.state = "loading";
  document.querySelector("main").setAttribute("aria-busy", "true");
});
document.addEventListener("htmx:finally:request", (event) => {
  const { ctx } = event.detail;
  if (!isLinkNavigation(ctx) || ctx.request.signal.aborted) return;
  loadingBar.dataset.state = "done";
  document.querySelector("main").removeAttribute("aria-busy");
});
document.addEventListener("animationend", (event) => event.animationName === "fade-out" && event.target.remove());
// A screen reader announcement for new clarifications.
let openCount = null;
const announceNewClarifications = () => {
  const badge = document.querySelector("[data-open-count]");
  if (!badge) return;
  const count = Number(badge.dataset.openCount);
  if (openCount !== null && count > openCount) document.getElementById("news").textContent = `New clarification. ${count} open.`;
  openCount = count;
};
const submitOnCtrlEnter = (event) => {
  if (event.key !== "Enter" || !(event.ctrlKey || event.metaKey) || event.target.localName !== "textarea") return;
  event.preventDefault();
  event.target.form?.requestSubmit();
};
// Vibrate once per new announcement alert.
let alerted;
try { alerted = sessionStorage.getItem("alerted"); } catch {}
const buzz = () => {
  const id = document.querySelector("[data-alert]")?.dataset.alert;
  if (!id || id === alerted) return;
  alerted = id;
  try { sessionStorage.setItem("alerted", id); } catch {}
  navigator.vibrate?.(200);
};
// Each new exam version refreshes the live parts. With a dialog open, the refresh waits until it
// closes, since a morph would remove it. The pages of an exam share its stream. A stream silent for
// longer than `offlineAfter` has dropped: the page shows the offline banner and reconnects. A
// request that couldn't reach the server shows it too, until one does.
const offlineAfter = Number(document.body.dataset.offlineAfter) * 1000;
let eventSource;
let streamUrl;
let version;
let lastMessageAt = Date.now();
let connectedAt = 0;
let requestFailed = false;
let refreshPending = false;
const refresh = () => {
  if (document.querySelector("dialog[open]")) refreshPending = true;
  else htmx.trigger("#refresh", "exam-changed");
};
const onStreamMessage = () => {
  lastMessageAt = Date.now();
  requestFailed = false;
};
const connect = () => {
  eventSource?.close();
  connectedAt = Date.now();
  eventSource = new EventSource(`${streamUrl}?version=${version}`);
  eventSource.onmessage = onStreamMessage;
  eventSource.addEventListener("changed", (event) => {
    onStreamMessage();
    version = event.lastEventId;
    refresh();
  });
};
// Opens the stream of the page's exam, keeps it across that exam's pages, and closes it on a page
// without one.
const syncEventSource = () => {
  const refreshElement = document.getElementById("refresh");
  if (refreshElement?.dataset.stream === streamUrl) return;
  eventSource?.close();
  eventSource = undefined;
  streamUrl = refreshElement?.dataset.stream;
  version = refreshElement?.dataset.version;
  if (refreshElement) connect();
};
document.addEventListener("close", () => {
  if (!refreshPending) return;
  refreshPending = false;
  refresh();
}, true);
// A tab shown again after its stream dropped reconnects before showing the offline banner.
document.addEventListener("visibilitychange", () => {
  if (document.hidden || !eventSource || Date.now() - lastMessageAt <= offlineAfter) return;
  lastMessageAt = Date.now();
  connect();
});
const offlineBanner = () => document.getElementById("offline");
const showOffline = () => {
  const offline = offlineBanner();
  offline.querySelector("time").textContent = new Date(lastMessageAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  offline.hidden = false;
};
const showNotSent = () => {
  const offline = offlineBanner();
  if (offline.hidden) showOffline();
  offline.querySelector("[data-unsent]").hidden = false;
  offline.scrollIntoView({ block: "nearest" });
};
setInterval(() => {
  const heartbeatMissed = eventSource && Date.now() - lastMessageAt > offlineAfter;
  if (heartbeatMissed && Date.now() - connectedAt > offlineAfter) connect();
  const offline = offlineBanner();
  if (!offline) return; // an error page without the app's layout
  const isOffline = heartbeatMissed || requestFailed || !navigator.onLine;
  if (isOffline && offline.hidden) showOffline();
  if (!isOffline) offline.querySelector("[data-unsent]").hidden = true;
  offline.hidden = !isOffline;
}, 1000);
document.addEventListener("htmx:after:request", (event) => {
  if (event.detail.ctx.response) requestFailed = false;
});
// On a network error the page stays as it was, typed text included; a form shows "Not sent."
document.addEventListener("htmx:error", (event) => {
  const { ctx, error } = event.detail;
  if (!ctx || ctx.response || error?.name === "AbortError") return;
  requestFailed = true;
  if (ctx.sourceElement.localName === "form") showNotSent();
  else showOffline();
});
// Refresh after the shortest [data-refresh-in], for time-based changes: snoozes ending, claims
// expiring, presence. Counted from when its part last rendered.
let refreshTimer;
const scheduleRefresh = () => {
  clearTimeout(refreshTimer);
  const delays = [...document.querySelectorAll("[data-refresh-in]")].map((part) => Number(part.dataset.refreshIn));
  if (delays.length) refreshTimer = setTimeout(refresh, Math.min(...delays) * 1000);
};
// Wait times count up as the `wait` and `wait_level` template filters render them.
const lateAfter = Number(document.body.dataset.lateAfter);
const veryLateAfter = Number(document.body.dataset.veryLateAfter);
setInterval(() => {
  document.querySelectorAll("[data-wait-since]").forEach((wait) => {
    const seconds = Date.now() / 1000 - Number(wait.dataset.waitSince);
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    wait.lastChild.textContent = minutes < 1 ? "<1 min" : hours ? `${hours} h ${String(minutes % 60).padStart(2, "0")} min` : `${minutes} min`;
    if (!("waitLevel" in wait.dataset)) return;
    wait.classList.toggle("very-late", seconds >= veryLateAfter);
    wait.classList.toggle("late", seconds >= lateAfter && seconds < veryLateAfter);
  });
}, 10000);
// Block submits while offline: they would only fail, after a wait.
document.addEventListener("submit", (event) => {
  if (event.target.method === "dialog" || (offlineBanner().hidden && navigator.onLine)) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  showNotSent();
}, true);
// Validate the opener's form before showing a dialog, and fill its [data-quote] fields.
document.addEventListener("command", (event) => {
  if (event.command !== "show-modal") return;
  if (event.source.form && !event.source.form.reportValidity()) return event.preventDefault();
  event.target.querySelectorAll("[data-quote]").forEach((quote) => {
    quote[quote.localName === "input" ? "value" : "textContent"] = document.getElementById(quote.dataset.quote).value;
  });
}, true);
// data-draft, data-retry, data-copy, data-clear (the photo) and data-show/data-hide buttons.
const handleButtons = (event) => {
  const target = event.target;
  const copy = target.closest("[data-copy]");
  const toggle = target.closest("[data-show], [data-hide]");
  const draft = target.closest("[data-draft]");
  const retry = target.closest("[data-retry]");
  if (draft) {
    draftAnswer(draft);
  } else if (retry) {
    retry.hidden = true;
    readPages(retry.closest("[data-read-pages]"));
  } else if (copy) {
    navigator.clipboard.writeText(copy.dataset.copy).then(() => (copy.lastChild.textContent = "Copied"));
  } else if (target.closest("[data-clear]")) {
    const input = target.closest("[data-photo-field]").querySelector("[data-photo]");
    input.value = "";
    input.dispatchEvent(new Event("change", { bubbles: true }));
  } else if (toggle) {
    const show = "show" in toggle.dataset;
    const id = show ? toggle.dataset.show : toggle.dataset.hide;
    const card = document.getElementById(id);
    card.hidden = !show;
    document.querySelector(`[data-show="${id}"]`).hidden = show;
    if (show) card.querySelector("textarea")?.focus();
  }
};
// Clickable table rows, unless the click was on a control or selected text.
const followRow = (event) => {
  const row = event.target.closest("tr.row-link");
  if (!row || event.target.closest("a, button, input, select, textarea, label") || getSelection().toString()) return;
  const link = row.querySelector(".row-target");
  if (event.ctrlKey || event.metaKey) window.open(link.href, "_blank");
  else link.click();
};
// Downscale and re-encode photos before upload; this also strips EXIF location.
const shrinkPhoto = async (input) => {
  const preview = input.closest("[data-photo-field]").querySelector("[data-preview]");
  preview.hidden = !input.files.length;
  if (!input.files.length) return;
  const submit = input.form.querySelector("button:not([type=button])");
  submit.setAttribute("aria-busy", "true");
  submit.disabled = true;
  try {
    const bitmap = await createImageBitmap(input.files[0]);
    const scale = Math.min(1, Number(document.body.dataset.photoMaxSide) / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    let blob;
    for (let quality = 0.8; quality > 0.2; quality -= 0.1) {
      blob = await new Promise((done) => canvas.toBlob(done, "image/jpeg", quality));
      if (blob.size <= Number(document.body.dataset.photoMaxBytes)) break;
    }
    const files = new DataTransfer();
    files.items.add(new File([blob], "photo.jpg", { type: "image/jpeg" }));
    input.files = files.files;
  } finally {
    submit.removeAttribute("aria-busy");
    submit.disabled = false;
    preview.querySelector("img").src = URL.createObjectURL(input.files[0]);
  }
};
// POST to one of the AI service views: its text, or null if it failed.
const askAI = async (url, form, body) => {
  const headers = { "X-CSRFToken": form.elements.csrfmiddlewaretoken.value };
  const response = await fetch(url, { method: "POST", body, headers }).catch(() => null);
  return response?.ok ? await response.text() : null;
};
// Fill in the question from the photo, unless the invigilator has typed one.
const transcribePhoto = async (input) => {
  const field = input.closest("[data-photo-field]");
  const question = input.form.elements.text;
  const failed = field.querySelector("[data-transcribe-failed]");
  failed.hidden = true;
  if (!field.dataset.transcribe || !input.files.length || question.value.trim()) return;
  const button = input.closest("label");
  const body = new FormData();
  body.append("photo", input.files[0]);
  const placeholder = question.placeholder;
  button.setAttribute("aria-busy", "true");
  question.placeholder = "Reading the photo…";
  const text = await askAI(field.dataset.transcribe, input.form, body);
  button.removeAttribute("aria-busy");
  question.placeholder = placeholder;
  failed.hidden = Boolean(text);
  if (text && !question.value.trim()) {
    question.value = text;
    question.dispatchEvent(new Event("input", { bubbles: true }));
  }
};
// Read a PDF's unread pages four at a time, then refresh. A morph keeps a row being read, and
// htmx.onLoad finds it again.
const rowsBeingRead = new WeakSet();
const readPages = async (row) => {
  if (rowsBeingRead.has(row)) return;
  rowsBeingRead.add(row);
  const urls = row.dataset.readPages.trim().split(/\s+/);
  const status = row.querySelector("[data-reading]");
  const failed = [];
  let left = urls.length;
  const readNext = async () => {
    for (let url; (url = urls.shift()); ) {
      if ((await askAI(url, row.querySelector("form"))) === null) failed.push(url);
      else status.lastChild.textContent = `Reading · ${--left} page${left === 1 ? "" : "s"} left`;
    }
  };
  await Promise.all(Array.from({ length: 4 }, readNext));
  rowsBeingRead.delete(row);
  if (!failed.length) return refresh();
  row.dataset.readPages = failed.join(" ");
  status.lastChild.textContent = `Couldn't read ${failed.length} page${failed.length === 1 ? "" : "s"}.`;
  row.querySelector("[data-retry]").hidden = false;
};
// Replace the answer with the AI's draft.
const draftAnswer = async (button) => {
  const answer = button.form.elements.text;
  const failed = button.form.querySelector("[data-draft-failed]");
  failed.hidden = true;
  button.setAttribute("aria-busy", "true");
  button.disabled = true;
  const text = await askAI(button.dataset.draft, button.form);
  button.removeAttribute("aria-busy");
  button.disabled = false;
  failed.hidden = Boolean(text);
  if (text) {
    answer.value = text;
    answer.focus();
  }
};
// Tom Select for selects and datalist inputs. In a form[data-autosubmit], a multi-select
// submits when its menu closes rather than on every pick.
const enhance = (field) => {
  const multi = field.multiple;
  const autosubmit = field.form?.hasAttribute("data-autosubmit");
  let changed = false;
  const apply = () => {
    changed = false;
    field.form.requestSubmit();
  };
  const settings = {
    maxOptions: null,
    openOnFocus: false,  // dialogs and autofocus focus fields; don't pop the menu then
    refreshThrottle: 0,  // so a quick Enter picks the current match
    hidePlaceholder: true,
    onItemAdd() {
      this.setTextboxValue("");
      if (multi) this.refreshOptions();
    },
    onChange() {
      if (!autosubmit) return;
      changed = true;
      if (!multi || !tom.isOpen) apply();
    },
    onDropdownClose() {
      if (changed) apply();
    },
  };
  if (multi) settings.plugins = ["remove_button"];
  if (field.localName === "input") {
    Object.assign(settings, {
      maxItems: 1,
      create: true,
      createOnBlur: true,
      persist: false,
      render: { no_results: null, option_create: null },
      // The suggestions, plus a typed value that isn't one, e.g. after a refused submit.
      options: [...new Set([...field.list.options].map((option) => option.value).concat(field.value || []))].map((value) => ({ value, text: value })),
    });
  }
  const tom = new TomSelect(field, settings);
  // The search box has taken over the label and focus.
  field.setAttribute("aria-hidden", "true");
  field.removeAttribute("aria-label");
  tom.control.addEventListener("click", () => setTimeout(() => tom.isFocused && tom.open()));
  for (const name of ["autocapitalize", "placeholder"]) {
    if (field.hasAttribute(name)) tom.control_input.setAttribute(name, field.getAttribute(name));
  }
  if (field.autofocus) tom.focus();
};

// `element` and the elements in it that match `selector`.
const queryAllIncludingSelf = (element, selector) => [element, ...element.querySelectorAll(selector)].filter((found) => found.matches(selector));
htmx.onLoad((element) => {
  syncEventSource();
  queryAllIncludingSelf(element, "dialog[data-autoopen]").forEach((dialog) => dialog.showModal());
  queryAllIncludingSelf(element, "[data-read-pages]").forEach(readPages);
  buzz();
  announceNewClarifications();
  if (queryAllIncludingSelf(element, "[data-refresh-in]").length) scheduleRefresh();
  queryAllIncludingSelf(element, "select:not(.tomselected, [hidden]), input[list]:not(.tomselected)").forEach(enhance);
});
document.addEventListener("click", (event) => {
  followRow(event);
  handleButtons(event);
});
document.addEventListener("keydown", submitOnCtrlEnter);
document.addEventListener("change", (event) => {
  const field = event.target;
  if (field.matches("[data-photo]")) shrinkPhoto(field).then(() => transcribePhoto(field));
  else if (field.form?.hasAttribute("data-autosubmit") && field.name && !field.classList.contains("tomselected")) {
    field.closest(".btn")?.setAttribute("aria-busy", "true");
    field.form.requestSubmit();
  }
});
