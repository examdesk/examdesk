// The parts around <main> that another page of the same exam and view morphs in.
const pageFrameSelectOOB = ["exam-status", "appbar", "exam-nav", "announcement-bar", "tab-bar", "venue-picker"]
  .map((id) => `#${id}:outerMorph`)
  .concat("#toasts:beforeend")
  .join(", ");
// Boosted responses. A full page for the current URL (a refresh, or a POST that redirects back)
// swaps in only its live parts ([data-live]), title and new toasts, and replaces the history entry.
// The same path with another query morphs the page: from a link (a sort, a page) at the top, from
// a form (a filter, a step) at the same scroll position. A POST that redirects back resets its form
// and closes the open dialog. Another page of the same exam and view swaps <main>; other pages
// replace <body>, attributes included. A link or form aimed at one part of the page (hx-target)
// swaps only that part.
document.addEventListener("htmx:after:request", (event) => {
  const { ctx } = event.detail;
  const url = new URL(ctx.response.raw.url);
  const isFullPage = ctx.text.startsWith("<!doctype html>") && ctx.response.status < 400;
  const isPost = ctx.request.method !== "GET";
  const isRedirectAfterPost = isPost && ctx.response.raw.redirected;
  const isPage = isFullPage && (!isPost || isRedirectAfterPost);
  const isHistoryRestore = Boolean(ctx.request.headers["HX-History-Restore-Request"]);
  if (isPage && url.pathname !== location.pathname && ctx.target === document.body && !isHistoryRestore) {
    const examView = ctx.text.match(/<body[^>]* data-exam-view="([^"]*)"/)?.[1];
    if (examView && examView === document.body.dataset.examView) {
      Object.assign(ctx, {
        target: document.querySelector("main"), select: "main", swap: "outerHTML show:top showTarget:body", selectOOB: pageFrameSelectOOB,
      });
    } else {
      ctx.swap = "outerHTML show:top"; // htmx syncs <body>'s attributes on an outer swap of it
    }
    return;
  }
  if (!isPage || url.pathname !== location.pathname || isHistoryRestore) return;
  if (ctx.target !== document.body && ctx.sourceElement.id !== "refresh") return; // aimed at one part
  if (url.search === location.search) {
    ctx.swap = "none";
    ctx.selectOOB = "[data-live]:outerMorph, #toasts:beforeend";
    ctx.replace = "true";
    document.title = new DOMParser().parseFromString(ctx.text, "text/html").title;
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
// A refused Save in Settings cancels the form's pending and in-flight preview requests: its
// previews keep the server's errors.
document.addEventListener("htmx:response:error", (event) => {
  const { ctx } = event.detail;
  if (!ctx.sourceElement.matches("#settings form")) return;
  for (const previewSource of ctx.sourceElement.querySelectorAll("[hx-get]")) {
    htmx.trigger(previewSource, "htmx:abort");
    htmx.process(previewSource, true);
  }
});
// Swapped-in live parts leave focus where it is, and Tom Select is rebuilt in them (a morph would
// strip its markup; htmx.onLoad below rebuilds it). A refresh keeps values not yet saved.
const unsavedValues = new Map(); // field id → Tom Select value
let focusedFieldId; // id of the Tom Select field focused when its part was swapped
document.addEventListener("htmx:before:swap", (event) => {
  const isRefresh = event.detail.ctx.sourceElement?.id === "refresh";
  unsavedValues.clear();
  focusedFieldId = undefined;
  for (const task of event.detail.tasks) {
    if (task.swapSpec.style === "none") continue;
    task.target.querySelectorAll(".tomselected").forEach((field) => {
      const { tomselect } = field;
      if (isRefresh && field.id && String(tomselect.items) !== tomselect.savedValue) unsavedValues.set(field.id, [...tomselect.items]);
      if (task.type === "oob" && tomselect.isFocused) focusedFieldId = field.id;
      tomselect.destroy();
    });
    if (task.type === "oob") task.fragment.querySelectorAll("[autofocus]").forEach((field) => field.removeAttribute("autofocus"));
  }
});
// A morph doesn't hide a part with unsent text in it, such as an answer being corrected.
htmx.registerExtension("keep-unsent-text-visible", {
  htmx_before_morph_attr: (element, { attrName }) =>
    attrName !== "hidden" || ![...element.querySelectorAll("input, textarea")].some((field) => field.value !== field.defaultValue),
});
// While a link or a GET form loads, a bar at the top runs and the part it replaces is marked busy
// (<main> for a whole page), which tailwind.css dims after 150 ms. A clarification or seat pane is
// also inert, since its replacement drops anything typed into it. So is the queue list: a row clicked
// in it would abort its load. A newer request aborts the load (`hx-sync` on <body>); the busy state
// clears when the latest navigation finishes or is aborted.
const isNavigation = ({ sourceEvent, request }) => ["click", "submit"].includes(sourceEvent?.type) && request.method === "GET";
const loadingTarget = ({ target }) => (!target || target === document.body ? document.querySelector("main") : target);
document.documentElement.insertAdjacentHTML("beforeend", '<div id="page-loading-bar" aria-hidden="true"></div>');
const loadingBar = document.getElementById("page-loading-bar");
let latestNavigation;
document.addEventListener("htmx:before:request", (event) => {
  const { ctx } = event.detail;
  if (event.defaultPrevented || !isNavigation(ctx)) return;
  latestNavigation = ctx;
  delete loadingBar.dataset.state;
  loadingBar.offsetWidth; // forces a reflow, so the transition restarts from 0
  loadingBar.dataset.state = "loading";
  const target = loadingTarget(ctx);
  target.setAttribute("aria-busy", "true");
  target.inert = target.matches("#clarification-pane, #seat-pane, #queue-list");
});
document.addEventListener("htmx:finally:request", (event) => {
  const { ctx } = event.detail;
  if (ctx !== latestNavigation) return;
  loadingBar.dataset.state = "done";
  document.querySelectorAll("[aria-busy=true]:not(.btn)").forEach((element) => {
    element.removeAttribute("aria-busy");
    element.inert = false;
  });
});
// A link that loads a clarification or a seat beside its list is marked current in the list at once.
const listItemSelectors = { "clarification-pane": ".queue-row", "seat-pane": ".seat-pill" };
document.addEventListener("htmx:before:request", (event) => {
  const { ctx } = event.detail;
  const itemSelector = listItemSelectors[ctx.target?.id];
  if (event.defaultPrevented || !itemSelector) return;
  const url = new URL(ctx.request.action, location.href).href;
  document.querySelectorAll(itemSelector).forEach((item) => {
    if (item.href === url) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  });
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
  if (event.key !== "Enter" || !(event.ctrlKey || event.metaKey) || event.target.localName !== "textarea" || event.target.readOnly) return;
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
// Each new exam version refreshes the live parts. With a dialog, a menu, an inline editor or a Tom
// Select menu open, the refresh waits until it closes, since a morph would reset it. So does a
// refresh already in flight when the dialog, menu or editor opens. The pages of an exam share its
// stream. A stream that hasn't opened or sent a message for `offlineAfter` has dropped: the page
// shows the offline banner and reconnects. A request that couldn't reach the server shows it too,
// until one does.
const offlineAfter = Number(document.body.dataset.offlineAfter) * 1000;
let eventSource;
let streamUrl;
let version;
let lastMessageAt = Date.now();
let connectedAt = 0;
let requestFailed = false;
let refreshPending = false;
const isEditing = () => document.querySelector("dialog[open], :popover-open, [data-editor]:not([hidden]), .ts-wrapper.dropdown-active");
const refresh = () => {
  if (isEditing()) refreshPending = true;
  else htmx.trigger("#refresh", "exam-changed");
};
const flushRefresh = () => {
  if (!refreshPending) return;
  refreshPending = false;
  refresh();
};
// An extension: htmx runs its hooks before the htmx:before:swap listeners.
htmx.registerExtension("defer-refresh-while-editing", {
  htmx_before_swap: (sourceElement) => {
    if (sourceElement.id !== "refresh" || !isEditing()) return;
    refreshPending = true;
    return false;
  },
});
const onStreamMessage = () => {
  lastMessageAt = Date.now();
  requestFailed = false;
};
const connect = () => {
  eventSource?.close();
  connectedAt = Date.now();
  eventSource = new EventSource(`${streamUrl}?version=${version}`);
  eventSource.onopen = onStreamMessage;
  eventSource.onmessage = onStreamMessage;
  eventSource.addEventListener("changed", (event) => {
    onStreamMessage();
    version = event.lastEventId;
    refresh();
  });
};
// Opens the stream of the page's exam, keeps it across that exam's pages, and closes it on a page
// without one. A new stream has `offlineAfter` to open before it counts as dropped.
const syncEventSource = () => {
  const refreshElement = document.getElementById("refresh");
  if (refreshElement?.dataset.stream === streamUrl) return;
  eventSource?.close();
  eventSource = undefined;
  streamUrl = refreshElement?.dataset.stream;
  version = refreshElement?.dataset.version;
  if (!refreshElement) return;
  lastMessageAt = Date.now();
  connect();
};
document.addEventListener("close", flushRefresh, true);
document.addEventListener("toggle", (event) => event.newState === "closed" && flushRefresh(), true);
// A tab shown again, or a page whose timers stopped (a laptop asleep), reconnects a dropped stream
// before showing the offline banner.
const reconnectIfDropped = () => {
  if (!eventSource || Date.now() - lastMessageAt <= offlineAfter) return;
  lastMessageAt = Date.now();
  connect();
};
document.addEventListener("visibilitychange", () => document.hidden || reconnectIfDropped());
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
let lastTickAt = Date.now();
setInterval(() => {
  if (Date.now() - lastTickAt > 3000) reconnectIfDropped();
  lastTickAt = Date.now();
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
// Wait times count up as the `wait` and `wait_level` template filters render them, except in the
// landing page's demos (inert).
const lateAfter = Number(document.body.dataset.lateAfter);
const veryLateAfter = Number(document.body.dataset.veryLateAfter);
setInterval(() => {
  document.querySelectorAll("[data-wait-since]:not([inert] *)").forEach((wait) => {
    const seconds = Date.now() / 1000 - Number(wait.dataset.waitSince);
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    wait.lastChild.textContent = minutes < 1 ? "<1 min" : hours ? `${hours} h ${String(minutes % 60).padStart(2, "0")} min` : `${minutes} min`;
    if (!("waitLevel" in wait.dataset)) return;
    wait.classList.toggle("very-late", seconds >= veryLateAfter);
    wait.classList.toggle("late", seconds >= lateAfter && seconds < veryLateAfter);
  });
}, 10000);
// A [data-select-all] checkbox checks or clears the enabled checkboxes of its form, and shows
// whether all, some or none of them are checked.
const checkboxesFor = (selectAll) => [...document.querySelectorAll(`input[type=checkbox][form="${selectAll.dataset.selectAll}"]:not(:disabled)`)];
document.addEventListener("change", (event) => {
  const field = event.target;
  if (field.matches("[data-select-all]")) return checkboxesFor(field).forEach((checkbox) => (checkbox.checked = field.checked));
  const selectAll = field.form && document.querySelector(`[data-select-all="${field.form.id}"]`);
  if (!selectAll || field.type !== "checkbox") return;
  const checkboxes = checkboxesFor(selectAll);
  const checkedCount = checkboxes.filter((checkbox) => checkbox.checked).length;
  selectAll.checked = checkedCount === checkboxes.length;
  selectAll.indeterminate = checkedCount > 0 && !selectAll.checked;
});
// The queue's filters stay on the open clarification, which may have changed since they rendered.
document.addEventListener("submit", (event) => {
  if (event.target.id === "queue-filters") event.target.action = location.pathname;
}, true);
// Block submits while offline: they would only fail, after a wait.
document.addEventListener("submit", (event) => {
  if (event.target.method === "dialog" || (offlineBanner().hidden && navigator.onLine)) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  showNotSent();
}, true);
// Before showing a dialog: validate the opener's form, fill the [data-quote] fields, set the
// form action and title from the opener's data-dialog-action and data-dialog-title, and reset a
// form with a [data-filter] box.
document.addEventListener("command", (event) => {
  if (event.command !== "show-modal") return;
  if (event.source.form && !event.source.form.reportValidity()) return event.preventDefault();
  const dialog = event.target;
  const form = dialog.querySelector("form");
  dialog.querySelectorAll("[data-quote]").forEach((quote) => {
    quote[quote.localName === "input" ? "value" : "textContent"] = document.getElementById(quote.dataset.quote).value;
  });
  const { dialogAction, dialogTitle } = event.source.dataset;
  if (dialogAction) form.action = dialogAction;
  if (dialogTitle) dialog.querySelector("h2").textContent = dialogTitle;
  const filter = dialog.querySelector("[data-filter]");
  if (filter) {
    form.reset();
    filterItems(filter);
  }
}, true);
// A [data-filter] box shows the [data-filter-item]s that have each of its words.
const filterItems = (filter) => {
  const words = filter.value.toLowerCase().split(/\s+/).filter(Boolean);
  const items = [...filter.form.querySelectorAll("[data-filter-item]")];
  items.forEach((item) => (item.hidden = !words.every((word) => item.textContent.toLowerCase().includes(word))));
  filter.form.querySelector("[data-filter-empty]").hidden = items.some((item) => !item.hidden);
};
document.addEventListener("input", (event) => event.target.matches("[data-filter]") && filterItems(event.target));
// data-draft, data-transcribe-wording, data-retry, data-copy, data-type-question,
// data-clear (the photo) and data-show/data-hide buttons.
const handleButtons = (event) => {
  const target = event.target;
  const copy = target.closest("[data-copy]");
  const toggle = target.closest("[data-show], [data-hide]");
  const draft = target.closest("[data-draft]");
  const transcribe = target.closest("[data-transcribe-wording]");
  const retry = target.closest("[data-retry]");
  if (draft) {
    draftAnswer(draft);
  } else if (transcribe) {
    fillWordingFromPhoto(transcribe);
  } else if (retry) {
    retry.hidden = true;
    readPages(retry.closest("[data-read-pages]"));
  } else if (copy) {
    const label = copy.querySelector("[data-copy-label]");
    navigator.clipboard.writeText(copy.dataset.copy).then(() => {
      label.textContent = "Copied";
      setTimeout(() => (label.textContent = label.dataset.copyLabel), 2000);
    });
  } else if (target.closest("[data-type-question]")) {
    const form = target.closest("form");
    showComposer(form, true);
    form.elements.text.focus();
  } else if (target.closest("[data-clear]")) {
    const input = target.closest("[data-photo-field]").querySelector("[data-photo]");
    input.value = "";
    input.dispatchEvent(new Event("change", { bubbles: true }));
    if (!input.form.elements.text.value.trim()) showComposer(input.form, false);
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
// While a photo is shrunk or read, Save and the photo's actions are hidden.
const setPhotoBusy = (field, busy) => {
  field.closest("form").querySelector("[data-save]").closest(".action-bar").hidden = busy;
  const photoActions = field.querySelector("[data-photo-actions]");
  photoActions.hidden = busy;
  if (busy) return;
  // A double tap on Cancel mustn't land on Remove, which takes its place.
  photoActions.inert = true;
  setTimeout(() => (photoActions.inert = false), 400);
};
// Block submits, Same question included, while a photo is shrunk or read.
document.addEventListener("submit", (event) => {
  if (!event.target.querySelector(".action-bar[hidden] [data-save]")) return;
  event.preventDefault();
  event.stopImmediatePropagation();
}, true);
// Shows New clarification's form, or the Type question / Take photo choice before it.
const showComposer = (form, shown) => {
  form.querySelector("[data-question-choice]").hidden = shown;
  form.querySelector("[data-composer]").hidden = !shown;
};
// Downscale and re-encode photos before upload; this also strips EXIF location.
const shrinkPhoto = async (input) => {
  const field = input.closest("[data-photo-field]");
  const preview = field.querySelector("[data-preview]");
  preview.hidden = !input.files.length;
  field.querySelector("[data-add-photo]").hidden = !preview.hidden;
  if (!input.files.length) return;
  const image = preview.querySelector("img");
  image.addEventListener("load", () => preview.scrollIntoView({ block: "nearest", behavior: "smooth" }), { once: true });
  image.src = URL.createObjectURL(input.files[0]);
  setPhotoBusy(field, true);
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
    setPhotoBusy(field, false);
    image.src = URL.createObjectURL(input.files[0]);
  }
};
// POST to one of the AI service views: its text, or null if it failed or was aborted.
const askAI = async (url, form, body, signal) => {
  const headers = { "X-CSRFToken": form.elements.csrfmiddlewaretoken.value };
  const response = await fetch(url, { method: "POST", body, headers, signal }).catch(() => null);
  return response?.ok ? await response.text().catch(() => null) : null;
};
// Fill in the question from the photo, unless the invigilator has typed one. Cancel shows after
// 3 s; Cancel, typing, another photo or leaving the page stops the read.
const transcribePhoto = async (input) => {
  const field = input.closest("[data-photo-field]");
  const question = input.form.elements.text;
  const errorMessage = field.querySelector("[data-transcribe-failed]");
  errorMessage.hidden = true;
  const url = field.querySelector("[data-transcribe]")?.dataset.transcribe;
  if (!url || !input.files.length || question.value.trim()) return;
  const status = field.querySelector("[data-transcribe-status]");
  const skeleton = input.form.querySelector("[data-text-skeleton]");
  const cancelButton = field.querySelector("[data-cancel-transcribe]");
  const placeholder = question.placeholder;
  const abortController = new AbortController();
  const { signal } = abortController;
  const endTranscription = () => abortController.abort();
  const cancelButtonTimer = setTimeout(() => (cancelButton.hidden = false), 3000);
  status.hidden = false;
  skeleton.hidden = false;
  question.placeholder = "";
  setPhotoBusy(field, true);
  cancelButton.addEventListener("click", endTranscription, { signal });
  question.addEventListener("input", endTranscription, { signal });
  input.addEventListener("change", endTranscription, { signal });
  document.addEventListener("htmx:after:swap", () => input.isConnected || endTranscription(), { signal });
  signal.addEventListener("abort", () => {
    clearTimeout(cancelButtonTimer);
    cancelButton.hidden = true;
    status.hidden = true;
    skeleton.hidden = true;
    question.placeholder = placeholder;
    setPhotoBusy(field, false);
  });
  const body = new FormData();
  body.append("photo", input.files[0]);
  const text = await askAI(url, input.form, body, signal);
  if (signal.aborted) return;
  endTranscription();
  errorMessage.hidden = Boolean(text);
  if (text) {
    question.value = text;
    question.dispatchEvent(new Event("input", { bubbles: true }));
  }
};
// Open the wording's editor and fill it in from the question's photo, unless the editor is closed
// by then.
const fillWordingFromPhoto = async (button) => {
  const form = button.form;
  const editor = form.querySelector("[data-editor]");
  const wording = form.elements.text;
  const save = editor.querySelector("button:not([type=button])");
  const failed = form.querySelector("[data-transcribe-failed]");
  failed.hidden = true;
  if (editor.hidden) toggleEditor(form, true);
  button.setAttribute("aria-busy", "true");
  button.disabled = true;
  save.disabled = true;
  wording.readOnly = true;
  wording.placeholder = "Reading the photo…";
  const text = await askAI(button.dataset.transcribeWording, form);
  button.removeAttribute("aria-busy");
  button.disabled = false;
  save.disabled = false;
  wording.readOnly = false;
  wording.placeholder = "";
  failed.hidden = Boolean(text);
  if (text && !editor.hidden) {
    wording.value = text;
    wording.focus();
  }
};
// Read a PDF's unread pages four at a time, counting them on its progress bar, then refresh. A
// morph keeps a row being read, and htmx.onLoad finds it again.
const rowsBeingRead = new WeakSet();
const readPages = async (row) => {
  if (rowsBeingRead.has(row)) return;
  rowsBeingRead.add(row);
  const urls = row.dataset.readPages.trim().split(/\s+/);
  const status = row.querySelector("[data-reading]");
  const progress = row.querySelector("[data-progress]");
  const pageCount = Number(progress.max);
  const pages = `${pageCount} page${pageCount === 1 ? "" : "s"}`;
  let readCount = pageCount - urls.length;
  const showProgress = (text = `Reading ${readCount} of ${pages}`) => {
    progress.value = readCount;
    status.textContent = text;
  };
  const failed = [];
  const readNext = async () => {
    for (let url; (url = urls.shift()); ) {
      if ((await askAI(url, row.querySelector("form"))) === null) failed.push(url);
      else readCount += 1;
      showProgress();
    }
  };
  showProgress();
  await Promise.all(Array.from({ length: 4 }, readNext));
  rowsBeingRead.delete(row);
  if (!failed.length) return refresh();
  row.dataset.readPages = failed.join(" ");
  showProgress(`Read ${readCount} of ${pages} · ${failed.length} couldn't be read`);
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
// Places as the server writes them: `hall 1` → `HALL1`.
const normalizePlace = (text) => text.replace(/\s+/g, "").toUpperCase();
// Text still typed when a click elsewhere takes the focus becomes an item after that click. Added
// earlier, the item could wrap the field and move the button under the pointer.
let pointerIsDown = false;
document.addEventListener("pointerdown", () => (pointerIsDown = true), true);
document.addEventListener("pointerup", () => (pointerIsDown = false), true);
// Tom Select for selects and datalist inputs. In a form[data-autosubmit], a multi-select
// submits when its menu closes rather than on every pick. A multi-select with
// [data-create-places] also takes typed places, added on Enter or a comma.
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
    onDropdownOpen() {
      // Tom Select can't scroll to the picked option before the menu has a height.
      if (!multi && this.activeOption) this.scrollToOption(this.activeOption);
    },
    onDropdownClose() {
      if (changed) apply();
      flushRefresh();
    },
  };
  if (multi) settings.plugins = ["remove_button"];
  // A single select in a filter bar (the sort) has a few options: no search box.
  else if (autosubmit && field.localName === "select") settings.controlInput = null;
  // A select with an empty option can be cleared.
  if (!multi && field.querySelector("option[value='']")) settings.plugins = { clear_button: { title: "Clear" } };
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
  if (field.hasAttribute("data-create-places")) {
    Object.assign(settings, {
      create(input, callback) {
        const item = { value: normalizePlace(input), text: normalizePlace(input) };
        if (this.isFocused || !pointerIsDown) return item;
        const listeners = new AbortController();
        const addItem = () => {
          listeners.abort();
          callback(item);
        };
        document.addEventListener("click", addItem, { capture: true, signal: listeners.signal });
        document.addEventListener("pointercancel", addItem, { capture: true, signal: listeners.signal });
      },
      createOnBlur: true,
      render: { option_create: (data, escape) => `<div class="create">Add <strong>${escape(normalizePlace(data.input))}</strong></div>`, no_results: null },
    });
  }
  const tom = new TomSelect(field, settings);
  tom.savedValue = String(tom.items);
  const unsavedValue = unsavedValues.get(field.id);
  if (unsavedValue) {
    if (settings.create) tom.addOption(unsavedValue.map((item) => ({ value: item, text: item })));
    tom.setValue(unsavedValue, true);
  }
  // The search box has taken over the label and focus.
  field.setAttribute("aria-hidden", "true");
  field.removeAttribute("aria-label");
  tom.control.addEventListener("click", () => setTimeout(() => tom.isFocused && tom.open()));
  for (const name of ["autocapitalize", "inputmode", "placeholder"]) {
    if (field.hasAttribute(name)) tom.control_input.setAttribute(name, field.getAttribute(name));
  }
  if (field.autofocus || field.id === focusedFieldId) tom.focus();
};
// An [autofocus] field hidden by Tom Select passes the focus to its search box.
document.addEventListener("focusin", (event) => event.target.tomselect?.focus());

// Reporting: the seat field lists one venue's seats (data-venue). It reloads when the form's venue
// changes, from the venue field or a refresh. While it reloads it is read-only, since the
// reloaded field drops a seat typed meanwhile.
const reloadSeatField = () => {
  const seatField = document.getElementById("report-seat");
  if (!seatField || seatField.closest("form").elements.venue.value === seatField.dataset.venue) return;
  document.getElementById("id_seat").tomselect?.setReadOnly(true);
  htmx.trigger(seatField, "venue-changed");
};
document.addEventListener("htmx:after:swap", reloadSeatField);
document.addEventListener("change", reloadSeatField);
// A reload that a newer one aborted leaves the field read-only.
let latestSeatReload;
document.addEventListener("htmx:before:request", (event) => {
  if (event.detail.ctx.sourceElement?.id === "report-seat") latestSeatReload = event.detail.ctx;
});
document.addEventListener("htmx:finally:request", (event) => {
  if (event.detail.ctx === latestSeatReload) document.getElementById("id_seat").tomselect?.setReadOnly(false);
});
// Reporting: once the seat and question find no clarification already asked, open New clarification.
// While the search runs, everything after Question is hidden, so Tab from Question focuses the tab bar.
document.addEventListener("htmx:after:swap", (event) => {
  const matches = document.querySelector("#matches[data-searched]");
  if (!matches) return;
  if (!matches.querySelector(".match")) document.getElementById("new-question").open = true;
  if (event.target.contains(matches) && document.activeElement.matches(".dock :focus-visible")) {
    [...matches.closest("form").querySelectorAll("a[href], button, summary, textarea")].find((control) => control.checkVisibility())?.focus();
  }
});
// On phones the exam's tabs scroll sideways; the current one comes into view with each page.
const showCurrentTab = () => document.querySelector("#exam-nav [aria-current=page]")?.scrollIntoView({ block: "nearest", inline: "nearest" });
// `element` and the elements in it that match `selector`.
const queryAllIncludingSelf = (element, selector) => [element, ...element.querySelectorAll(selector)].filter((found) => found.matches(selector));
htmx.onLoad((element) => {
  syncEventSource();
  if (element.matches("body, main")) showCurrentTab();
  queryAllIncludingSelf(element, "dialog[data-autoopen]").forEach((dialog) => dialog.showModal());
  queryAllIncludingSelf(element, "[data-read-pages]").forEach(readPages);
  queryAllIncludingSelf(element, "[data-browser-time-zone]").forEach((field) => (field.value = Intl.DateTimeFormat().resolvedOptions().timeZone));
  buzz();
  announceNewClarifications();
  if (queryAllIncludingSelf(element, "[data-refresh-in]").length) scheduleRefresh();
  queryAllIncludingSelf(element, "select:not(.tomselected, [hidden]), input[list]:not(.tomselected)").forEach(enhance);
});
document.addEventListener("click", (event) => {
  followRow(event);
  handleButtons(event);
  handleInlineEdit(event);
});
document.addEventListener("keydown", submitOnCtrlEnter);
// Inline editors ([data-inline-edit]): the text opens a text box in its place, filled from
// `data-saved-text` (kept current by refreshes, unlike a text box's value). ✓ or Ctrl/⌘ Enter
// saves; × or Escape closes it.
const toggleEditor = (form, open) => {
  form.querySelector("[data-edit]").hidden = open;
  form.querySelector("[data-editor]").hidden = !open;
  const textarea = form.querySelector("textarea");
  if (!open) {
    form.querySelector("[data-edit]").focus();
    return flushRefresh();
  }
  textarea.value = form.dataset.savedText;
  textarea.focus();
  textarea.setSelectionRange(textarea.value.length, textarea.value.length);
};
const handleInlineEdit = (event) => {
  const form = event.target.closest("[data-inline-edit]");
  if (form && event.target.closest("[data-edit]")) toggleEditor(form, true);
  else if (form && event.target.closest("[data-cancel-edit]")) toggleEditor(form, false);
};
// J and K open the next and previous clarification in the queue. Escape closes an inline editor,
// or leaves a text box first.
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && event.target.localName === "textarea" && !event.target.closest("dialog")) {
    const editor = event.target.closest("[data-inline-edit]");
    return editor ? toggleEditor(editor, false) : event.target.blur();
  }
  if (!["j", "k"].includes(event.key) || event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.target.closest("input, textarea, select, [contenteditable]") || document.querySelector("dialog[open]")) return;
  const rows = [...document.querySelectorAll(".queue-row:not([inert] *)")];
  if (!rows.length) return;
  const current = rows.findIndex((row) => row.getAttribute("aria-current") === "page");
  const next = current === -1 ? 0 : current + (event.key === "j" ? 1 : -1);
  rows[next]?.click();
});
document.addEventListener("change", (event) => {
  const field = event.target;
  if (field.matches("[data-photo]")) {
    if (field.files.length) showComposer(field.form, true);
    shrinkPhoto(field).then(() => transcribePhoto(field));
  } else if (field.form?.hasAttribute("data-autosubmit") && field.name && !field.classList.contains("tomselected")) field.form.requestSubmit();
});
