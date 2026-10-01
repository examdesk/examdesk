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
const filterSeats = () => {
  const field = document.querySelector("[data-seat-filter]");
  if (!field) return;
  const typed = field.value.replace(/\s/g, "").toUpperCase();
  document.querySelectorAll("[data-seat]").forEach((card) => (card.hidden = !card.dataset.seat.includes(typed)));
  document.querySelectorAll("[data-venue]").forEach((venue) => (venue.hidden = !venue.querySelector("[data-seat]:not([hidden])")));
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
// Offline banner. Pages that aren't polling (hidden, dialog open, or no poller) never go stale.
const offline = document.getElementById("offline");
const offlineAfter = Number(document.body.dataset.offlineAfter) * 1000;
let lastOk = Date.now();
const showOffline = () => {
  offline.querySelector("time").textContent = new Date(lastOk).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  offline.hidden = false;
};
document.addEventListener("htmx:after:request", (event) => {
  if (event.detail.ctx?.response?.status < 400) lastOk = Date.now();
});
setInterval(() => {
  if (document.hidden || document.querySelector("dialog[open]") || !document.querySelector('[hx-trigger^="every"]')) lastOk = Date.now();
  const stale = Date.now() - lastOk > offlineAfter || !navigator.onLine;
  if (stale && offline.hidden) showOffline();
  if (!stale) offline.querySelector("[data-unsent]").hidden = true;
  offline.hidden = !stale;
}, 1000);
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
// data-copy, data-clear (the photo) and data-show/data-hide buttons.
const handleButtons = (event) => {
  const target = event.target;
  const copy = target.closest("[data-copy]");
  const toggle = target.closest("[data-show], [data-hide]");
  if (copy) {
    navigator.clipboard.writeText(copy.dataset.copy).then(() => (copy.lastChild.textContent = "Copied"));
  } else if (target.closest("[data-clear]")) {
    const input = target.closest("form").querySelector("[data-photo]");
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
  const preview = input.form.querySelector("[data-preview]");
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

htmx.onLoad(() => {
  buzz();
  countOpen();
  filterSeats();
  document.querySelectorAll("select:not(.tomselected, [hidden]), input[list]:not(.tomselected)").forEach(enhance);
});
document.addEventListener("input", (event) => event.target.matches("[data-seat-filter]") && filterSeats());
document.addEventListener("click", (event) => {
  followRow(event);
  handleButtons(event);
});
document.addEventListener("keydown", submitOnCtrlEnter);
document.addEventListener("change", (event) => {
  const field = event.target;
  if (field.matches("[data-photo]")) shrinkPhoto(field);
  else if (field.form?.hasAttribute("data-autosubmit") && field.name && !field.classList.contains("tomselected")) field.form.requestSubmit();
});
