// A morph would strip Tom Select's markup; htmx.onLoad below rebuilds it.
document.addEventListener("htmx:before:swap", (event) => {
  event.target.querySelectorAll(".tomselected").forEach((field) => field.tomselect.destroy());
});
// Open clarification count in the tab title, and a screen reader announcement for new ones.
const news = document.getElementById("news");
const baseTitle = document.title;
let openCount = null;
const countOpen = () => {
  const badge = document.querySelector("[data-open-count]");
  if (!badge) return;
  const count = Number(badge.dataset.openCount);
  document.title = count ? `(${count}) ${baseTitle}` : baseTitle;
  if (openCount !== null && count > openCount) news.textContent = `New clarification. ${count} open.`;
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
// closes, since a morph would remove it. A stream silent for longer than `offlineAfter` has
// dropped: the page shows the offline banner and reconnects.
const refreshElement = document.getElementById("refresh");
const offline = document.getElementById("offline");
const offlineAfter = Number(document.body.dataset.offlineAfter) * 1000;
let eventSource;
let version = refreshElement?.dataset.version;
let lastMessageAt = Date.now();
let connectedAt = 0;
let refreshPending = false;
const refresh = () => {
  if (document.querySelector("dialog[open]")) refreshPending = true;
  else htmx.trigger(refreshElement, "exam-changed");
};
const connect = () => {
  eventSource?.close();
  connectedAt = Date.now();
  eventSource = new EventSource(`${refreshElement.dataset.stream}?version=${version}`);
  eventSource.onmessage = () => (lastMessageAt = Date.now());
  eventSource.addEventListener("changed", (event) => {
    lastMessageAt = Date.now();
    version = event.lastEventId;
    refresh();
  });
};
if (refreshElement) connect();
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
const showOffline = () => {
  offline.querySelector("time").textContent = new Date(lastMessageAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  offline.hidden = false;
};
setInterval(() => {
  const heartbeatMissed = eventSource && Date.now() - lastMessageAt > offlineAfter;
  if (heartbeatMissed && Date.now() - connectedAt > offlineAfter) connect();
  const isOffline = heartbeatMissed || !navigator.onLine;
  if (isOffline && offline.hidden) showOffline();
  if (!isOffline) offline.querySelector("[data-unsent]").hidden = true;
  offline.hidden = !isOffline;
}, 1000);
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
// Block submits while offline so the browser's error page doesn't eat the input.
document.addEventListener("submit", (event) => {
  if (event.target.method === "dialog" || (offline.hidden && navigator.onLine)) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  if (offline.hidden) showOffline();
  offline.querySelector("[data-unsent]").hidden = false;
  offline.scrollIntoView({ block: "nearest" });
}, true);
// Validate the opener's form before showing a dialog, and fill its [data-quote] fields.
document.addEventListener("command", (event) => {
  if (event.command !== "show-modal") return;
  if (event.source.form && !event.source.form.reportValidity()) return event.preventDefault();
  event.target.querySelectorAll("[data-quote]").forEach((quote) => {
    quote[quote.localName === "input" ? "value" : "textContent"] = document.getElementById(quote.dataset.quote).value;
  });
}, true);
document.querySelectorAll("dialog[data-autoopen]").forEach((dialog) => dialog.showModal());
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
// Read a PDF's unread pages four at a time, then reload.
const readPages = async (row) => {
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
  if (!failed.length) return location.reload();
  row.dataset.readPages = failed.join(" ");
  status.lastChild.textContent = `Couldn't read ${failed.length} page${failed.length === 1 ? "" : "s"}.`;
  row.querySelector("[data-retry]").hidden = false;
};
document.querySelectorAll("[data-read-pages]").forEach(readPages);
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

htmx.onLoad((element) => {
  buzz();
  countOpen();
  if (element.matches("[data-refresh-in]") || element.querySelector("[data-refresh-in]")) scheduleRefresh();
  document.querySelectorAll("select:not(.tomselected, [hidden]), input[list]:not(.tomselected)").forEach(enhance);
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
