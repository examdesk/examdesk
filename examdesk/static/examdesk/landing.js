// The landing page (landing.html): the seat plan behind the headline, and the demos. Each demo is a GSAP timeline over
// a stage of the app's own markup (examdesk/landing/*.html), switching its parts on and off (`data-shown`).

const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const REPEAT_DELAY = 1.5;
const EASE = "power2.inOut";
const TYPING_SPEED = 28; // characters a second

// GSAP's build sets a global and can't be imported: in a module it fails to assign `self.window`.
function loadScript(src) {
  return new Promise((resolve, reject) => {
    const script = Object.assign(document.createElement("script"), { src });
    script.addEventListener("load", resolve);
    script.addEventListener("error", () => reject(new Error(`Couldn't load ${src}`)));
    document.head.append(script);
  });
}

// An element's centre on its stage, in the stage's own pixels however it's scaled.
function centerOnStage(element) {
  const stage = element.closest(".landing-stage");
  const outer = stage.getBoundingClientRect();
  const inner = element.getBoundingClientRect();
  const scale = outer.width / stage.offsetWidth;
  return {
    x: (inner.left + inner.width / 2 - outer.left) / scale,
    y: (inner.top + inner.height / 2 - outer.top) / scale,
  };
}

const isShown = (element) => element.getClientRects().length > 0;

// Text typed into a field a letter at a time, over what it held.
const TypeTextPlugin = {
  name: "typeText",
  init(target, text) {
    Object.assign(this, { target, text, start: target.value });
  },
  render(progress, { target, text, start }) {
    target.value = progress ? text.slice(0, Math.ceil(progress * text.length)) : start;
  },
};

function registerEffects() {
  const register = (name, effect, defaults = {}) =>
    gsap.registerEffect({ name, effect, defaults, extendTimeline: true });

  // Parts that start shown say so: a rewind restores the value an attribute had, and a missing one comes back as 0.
  const shownAtStart = (targets) => {
    for (const target of targets) target.dataset.shown ??= "1";
  };

  // A part shows, fading and rising in, or hides; or one part replaces another.
  register("show", (targets) =>
    gsap
      .timeline()
      .set(targets, { attr: { "data-shown": 1 } })
      .fromTo(targets, { autoAlpha: 0, y: 6 }, { autoAlpha: 1, y: 0, duration: 0.3, immediateRender: false }),
  );
  // A zero-duration tween rather than a timeline of one set: GSAP never renders a zero-duration timeline again once
  // past it, and a frame landing exactly on a set at a nested timeline's start can render it as not reached.
  register("hide", (targets) => {
    shownAtStart(targets);
    return gsap.to(targets, { attr: { "data-shown": 0 }, duration: 0, immediateRender: false });
  });
  register("swap", ([from, to]) => {
    shownAtStart([from]);
    return gsap
      .timeline()
      .set(from, { attr: { "data-shown": 0 } })
      .set(to, { attr: { "data-shown": 1 } })
      .fromTo(to, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.3, immediateRender: false });
  });

  // The pointer curves over to the target and clicks: a ripple, and the target dips. A target the stage hides at
  // this width is clicked where the pointer is.
  register("click", ([target]) => {
    const pointer = target.closest(".landing-stage").querySelector(".landing-pointer");
    const to = (axis) => () => (isShown(target) ? centerOnStage(target)[axis] : gsap.getProperty(pointer, axis));
    return gsap
      .timeline()
      .to(pointer, { x: to("x"), duration: 0.5, ease: EASE }, 0)
      .to(pointer, { y: to("y"), duration: 0.5, ease: "sine.inOut" }, 0)
      .fromTo(
        pointer.lastElementChild,
        { scale: 0.3, opacity: 0.8 },
        { scale: 1.6, opacity: 0, duration: 0.35, immediateRender: false },
        "+=0.08",
      )
      .to(target, { keyframes: { scale: [1, 0.96, 1] }, duration: 0.25 }, "<")
      .set({}, {}, "+=0.05");
  });

  // A finger taps the target.
  register("tap", ([target]) => {
    const tap = target.closest(".landing-stage").querySelector(".landing-tap");
    return gsap
      .timeline()
      .set(tap, { x: () => centerOnStage(target).x, y: () => centerOnStage(target).y }, "+=0.3")
      .fromTo(
        tap,
        { autoAlpha: 0.9, scale: 0.5 },
        { autoAlpha: 0, scale: 1.3, duration: 0.45, ease: "power1.out", immediateRender: false },
      )
      .to(target, { keyframes: { scale: [1, 0.97, 1] }, duration: 0.25 }, "<")
      .set({}, {}, "+=0.1");
  });

  // The field shows focus while its `data-text` appears a letter at a time.
  register(
    "type",
    ([field], { speed }) =>
      gsap
        .timeline()
        .set(field, { attr: { "data-focus": 1 } })
        .to(field, { typeText: field.dataset.text, duration: field.dataset.text.length / speed, ease: "none" })
        .set(field, { attr: { "data-focus": 0 } }, "+=0.2"),
    { speed: TYPING_SPEED },
  );

  register("toast", ([toast]) =>
    gsap
      .timeline()
      .fromTo(toast, { autoAlpha: 0, y: 12 }, { autoAlpha: 1, y: 0, duration: 0.3, immediateRender: false })
      .to(toast, { autoAlpha: 0, y: 12, duration: 0.3 }, "+=2"),
  );
}

// The hero (landing/hero.html): seat 14 reports a new question, the examiner opens and answers it, and the
// invigilator delivers the answer.
function heroTimeline(timeline, $, [report, answer, deliver]) {
  // The device not in use dims (`data-active`); both stay lit while one hands something to the other.
  const stage = $("report-page").closest(".landing-stage");

  timeline
    .addLabel(report)
    .tap($("seat"), {}, "+=0.6")
    .type($("seat"))
    .tap($("label"))
    .type($("label"))
    .show($("new-question"), {}, "+=0.3")
    .tap($("new-button"), {}, "+=0.6")
    .swap([$("new-button"), $("new-form")])
    .tap($("question"))
    .type($("question"))
    .tap($("save"), {}, "+=0.4")
    .swap([$("report-page"), $("reported-page")])
    .show($("row-3b"), {}, "+=0.4")
    .set([$("clarifications-count"), $("open-count")], { innerText: 3 }, "<")
    .set(stage, { attr: { "data-active": "both" } }, "<");

  timeline
    .addLabel(answer, "+=0.8")
    .set(stage, { attr: { "data-active": "window" } }, answer)
    .click($("row-3b"), {}, answer)
    .set($("row-2a"), { attr: { "aria-current": "false" } })
    .set($("row-3b"), { attr: { "aria-current": "page" } }, "<")
    .swap([$("pane-2a"), $("pane-3b")], {}, "<")
    .click($("answer-text"), {}, "+=0.6")
    .type($("answer-text"))
    .click($("send"), {}, "+=0.3")
    .hide($("composer"))
    .show($("answer"), {}, "<")
    .swap([$("state-open"), $("state-answered")], {}, "<")
    .hide([$("wait"), $("row-wait")], {}, "<")
    .show([$("row-answered"), $("row-to-deliver"), $("to-deliver")], {}, "<")
    .set([$("clarifications-count"), $("open-count")], { innerText: 2 }, "<")
    .set($("answered-count"), { innerText: 2 }, "<")
    .toast($("sent-toast"), {}, "<")
    .set(stage, { attr: { "data-active": "both" } }, "+=0.6")
    .swap([$("reported-open"), $("reported-to-deliver")], {}, "<")
    .swap([$("report-another"), $("report-another-after")], {}, "<")
    .show([$("show-answer"), $("deliver-count")], {}, "<");

  timeline
    .addLabel(deliver, "+=1")
    .set(stage, { attr: { "data-active": "phone" } }, deliver)
    .tap($("show-answer"), {}, deliver)
    .swap([$("reported-page"), $("seat-page")])
    .tap($("delivered-button"), {}, "+=1.6")
    .swap([$("to-show"), $("all-delivered")])
    .hide([$("deliver-count"), $("row-to-deliver")], {}, "<")
    .toast($("delivered-toast"), {}, "<")
    .swap([$("to-deliver"), $("delivered")], {}, "<")
    .set(stage, { attr: { "data-active": "both" } }, "<")
    .set({}, {}, "+=1.6");
}

// Reporting (landing/report.html): seat 27 joins a clarification about 3b, then seat 32 reports 3c from a photo.
function reportTimeline(timeline, $, [seatAndQuestion, alreadyAsked, newQuestion]) {
  timeline
    .addLabel(seatAndQuestion)
    .tap($("seat"), {}, "+=0.5")
    .type($("seat"))
    .tap($("label"))
    .type($("label"));

  timeline
    .addLabel(alreadyAsked, "+=0.5")
    .show($("matches"), {}, alreadyAsked)
    .tap($("match"), {}, "+=1.6")
    .swap([$("first-report-page"), $("first-reported-page")]);

  timeline
    .addLabel(newQuestion, "+=1.4")
    .tap($("report-another"), {}, newQuestion)
    .swap([$("first-reported-page"), $("second-report-page")])
    .tap($("second-seat"))
    .type($("second-seat"))
    .tap($("second-label"))
    .type($("second-label"))
    .show($("new-question"), {}, "+=0.3")
    .tap($("new-button"), {}, "+=0.6")
    .swap([$("new-button"), $("new-form")])
    .tap($("photo-button"), {}, "+=0.4")
    .show($("photo"))
    .hide($("reading"), {}, "+=1.2")
    .type($("second-question"), { speed: 90 })
    .tap($("save"), {}, "+=0.6")
    .swap([$("second-report-page"), $("second-reported-page")])
    .set({}, {}, "+=1.8");
}

// Answering (landing/answer.html): a duplicate of 3b arrives, both duplicates merge into it, an AI draft answers it,
// and 2a's answer is announced.
function answerTimeline(timeline, $, [longestWait, merge, draft, announce]) {
  const counts = (open, answered, closed) =>
    timeline
      .set([$("clarifications-count"), $("open-count")], { innerText: open }, "<")
      .set($("answered-count"), { innerText: answered }, "<")
      .set($("closed-count"), { innerText: closed }, "<");

  timeline
    .addLabel(longestWait)
    .show([$("row-duplicate-2"), $("duplicate-2")], {}, "+=0.8")
    .set($("duplicate-count"), { innerText: 2 }, "<");
  counts(5, 2, 1);
  timeline.swap([$("row-2a-wait"), $("row-2a-late")], {}, "+=1.4");

  timeline
    .addLabel(merge, "+=1")
    .set($("layout"), { attr: { "data-view": "clarification" } }, merge)
    .click($("duplicate-1-checkbox"), {}, merge)
    .set($("duplicate-1-checkbox"), { checked: true })
    .show($("merge"), {}, "<")
    .click($("duplicate-2-checkbox"))
    .set($("duplicate-2-checkbox"), { checked: true })
    .click($("merge"), {}, "+=0.3")
    .hide([$("duplicates"), $("row-duplicate-1"), $("row-duplicate-2")])
    .show([...$.all("merged-seat"), $("row-3b-seats")], {}, "<")
    .set($("3b-seat-count"), { innerText: 3 }, "<")
    .toast($("merged-toast"), {}, "<");
  counts(3, 2, 1);

  timeline
    .addLabel(draft, "+=1.2")
    .click($("draft"), {}, draft)
    .type($("3b-text"), { speed: 90 }, "+=0.4")
    .click($("3b-send"), {}, "+=0.6")
    .hide($("3b-composer"))
    .show($("3b-answer"), {}, "<")
    .swap([$("3b-open"), $("3b-answered")], {}, "<")
    .hide([$("3b-wait"), $("row-3b-wait")], {}, "<")
    .show([$("row-3b-answered"), $("row-3b-to-deliver"), ...$.all("3b-to-deliver")], {}, "<")
    .toast($("sent-toast"), {}, "<");
  counts(2, 3, 1);

  timeline
    .addLabel(announce, "+=1.2")
    .set($("layout"), { attr: { "data-view": "queue" } }, announce)
    .click($("row-2a"), {}, announce)
    .set($("layout"), { attr: { "data-view": "clarification" } })
    .set($("row-3b"), { attr: { "aria-current": "false" } }, "<")
    .set($("row-2a"), { attr: { "aria-current": "page" } }, "<")
    .swap([$("pane-3b"), $("pane-2a")], {}, "<")
    .click($("2a-text"), {}, "+=0.6")
    .type($("2a-text"))
    .click($("announce"), {}, "+=0.3")
    .fromTo($("announce-dialog"), { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.2, immediateRender: false })
    .click($("announce-confirm"), {}, "+=0.9")
    .to($("announce-dialog"), { autoAlpha: 0, duration: 0.2 })
    .hide($("2a-composer"))
    .show([$("2a-answer"), $("row-2a-announced"), ...$.all("2a-to-read-out")], {}, "<")
    .swap([$("2a-open"), $("2a-announced")], {}, "<")
    .hide([$("2a-wait"), $("row-2a-late")], {}, "<")
    .toast($("announced-toast"), {}, "<");
  counts(1, 3, 2);
  timeline.set({}, {}, "+=2");
}

// Delivering (landing/deliver.html): seat 27 is with Ben, so seat 14 is delivered, then comes back with a corrected
// answer.
function deliverTimeline(timeline, $, [seats, oneInvigilator, delivered, corrected]) {
  const rows = $("list-page").querySelectorAll(".deliver-row");
  const subtitle = $("corrected-page").querySelector(".page-header p");
  timeline
    .addLabel(seats)
    .fromTo(rows, { autoAlpha: 0, y: 10 }, { autoAlpha: 1, y: 0, stagger: 0.15, immediateRender: false }, seats)
    .set({}, {}, "+=1");

  timeline
    .addLabel(oneInvigilator, "+=0.4")
    .show($("claim"), {}, oneInvigilator)
    .set($("row-27"), { attr: { class: "deliver-row claimed" } }, "<")
    .tap($("row-14"), {}, "+=1.4")
    .swap([$("list-page"), $("seat-page")]);

  timeline
    .addLabel(delivered, "+=1.6")
    .tap($("delivered-button"), {}, delivered)
    .swap([$("to-show"), $("all-delivered")])
    .set($("deliver-count"), { innerText: 2 }, "<")
    .toast($("delivered-toast"), {}, "<");

  timeline
    .addLabel(corrected, "+=1.4")
    .tap($("deliver-tab"), {}, corrected)
    .swap([$("seat-page"), $("corrected-page")])
    .show($("row-corrected"), {}, "+=0.9")
    .set(subtitle, { innerText: "3 seats to deliver" }, "<")
    .set($("deliver-count"), { innerText: 3 }, "<")
    .set({}, {}, "+=2");
}

const TIMELINES = {
  hero: heroTimeline,
  report: reportTimeline,
  answer: answerTimeline,
  deliver: deliverTimeline,
};

// Scales the stage to its box.
function fitStage(stage) {
  const box = stage.parentElement;
  new ResizeObserver(() => stage.style.setProperty("--stage-scale", box.clientWidth / stage.offsetWidth)).observe(box);
}

// The steps fill with the timeline: each step's track over its part of it.
function fillSteps(steps, timeline) {
  const starts = [...steps.map((step) => timeline.labels[step.dataset.step]), timeline.duration()];
  steps.forEach((step, index) => {
    timeline
      .set(step, { attr: { "aria-current": "true" } }, starts[index])
      .fromTo(
        step.querySelector(".landing-step-track > span"),
        { scaleX: 0 },
        { scaleX: 1, duration: starts[index + 1] - starts[index], ease: "none", immediateRender: false },
        starts[index],
      )
      .set(step, { attr: { "aria-current": "false" } }, starts[index + 1]);
  });
  return starts;
}

// Builds the demo at `root` and plays it, over and over, while it's on screen. A step plays from its start when
// clicked. With reduced motion, the demo shows its end, and a step shows its own end.
function playDemo(root) {
  const stage = root.querySelector(".landing-stage");
  fitStage(stage);
  const pointer = stage.querySelector(".landing-pointer");
  if (pointer) gsap.set(pointer, { autoAlpha: 1, x: stage.offsetWidth - 120, y: stage.offsetHeight - 40 });
  const steps = [...root.querySelectorAll("[data-step]")];
  const timeline = gsap.timeline({ paused: true, defaults: { duration: 0.35, ease: "power2.out" } });
  // The demo's parts by `data-el`: the first, or with `$.all` every one.
  const $ = (name) => root.querySelector(`[data-el="${name}"]`);
  $.all = (name) => [...root.querySelectorAll(`[data-el="${name}"]`)];
  TIMELINES[root.dataset.demo](
    timeline,
    $,
    steps.map((step) => step.dataset.step),
  );
  const starts = fillSteps(steps, timeline);
  steps.forEach((step, index) =>
    step.addEventListener("click", () => {
      if (reducedMotion) timeline.pause(starts[index + 1] - 0.001);
      else timeline.play(starts[index]);
    }),
  );
  if (reducedMotion) {
    if (pointer) gsap.set(pointer, { autoAlpha: 0 });
    timeline.progress(1);
    return;
  }
  timeline.repeat(-1).repeatDelay(REPEAT_DELAY);
  new IntersectionObserver(([entry]) => (entry.isIntersecting ? timeline.play() : timeline.pause())).observe(stage);
}

// The seat plan behind the headline, drawn from the logo: now and then a seat asks, is answered, and settles. The
// stylesheet's mask hides the seats behind the headline.
function drawSeatPlan(canvas) {
  const CELL = 44;
  const SEAT = 28;
  const bubble = new Path2D(
    "M21.5 1.5h5a4 4 0 0 1 4 4v5a4 4 0 0 1-4 4h-5q-1 0-1.96.3l-2.24.69q-1.14.35-.79-.79l.69-2.24q.3-.96.3-1.96v-5a4 4 0 0 1 4-4z",
  );
  const tick = new Path2D("M20.6 8.3l2.3 2.3 4.5-4.7");
  const context = canvas.getContext("2d");
  let seats = [];

  const draw = (seat) => {
    context.clearRect(seat.x, seat.y, CELL, CELL);
    context.save();
    context.translate(seat.x + (CELL - SEAT) / 2, seat.y + (CELL - SEAT) / 2);
    context.scale(SEAT / 16, SEAT / 16);
    context.translate(-16, 0);
    context.globalAlpha = 0.32 * (1 - seat.asking);
    context.fillStyle = "#bccbf7";
    context.beginPath();
    context.roundRect(17.5, 1.5, 13, 13, 4);
    context.fill();
    context.globalAlpha = seat.asking;
    context.fillStyle = "#1d4ed8";
    context.fill(bubble);
    context.globalAlpha = seat.asking * seat.answered;
    Object.assign(context, { strokeStyle: "#fff", lineWidth: 2.3, lineCap: "round", lineJoin: "round" });
    context.stroke(tick);
    context.restore();
  };

  const layout = () => {
    const ratio = devicePixelRatio || 1;
    const { clientWidth: width, clientHeight: height } = canvas;
    Object.assign(canvas, { width: width * ratio, height: height * ratio });
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    const columns = Math.ceil(width / CELL);
    const left = (width - columns * CELL) / 2;
    seats = [];
    for (let y = 8; y < height; y += CELL) {
      for (let column = 0; column < columns; column++) seats.push({ x: left + column * CELL, y, asking: 0, answered: 0 });
    }
    if (reducedMotion) {
      for (const seat of gsap.utils.shuffle([...seats]).slice(0, 30)) {
        Object.assign(seat, { asking: 1, answered: Math.round(Math.random()) });
      }
    }
    for (const seat of seats) draw(seat);
  };

  let onScreen = true;
  const ask = () => {
    const idle = seats.filter((seat) => !seat.asking);
    if (onScreen && idle.length) {
      const seat = gsap.utils.random(idle);
      const redraw = () => draw(seat);
      gsap
        .timeline({ onUpdate: redraw })
        .to(seat, { asking: 1, duration: 0.5, ease: "power2.out" })
        .to(seat, { answered: 1, duration: 0.3 }, `+=${gsap.utils.random(1.8, 3.2)}`)
        .to(seat, { asking: 0, answered: 0, duration: 0.8 }, `+=${gsap.utils.random(2, 4)}`);
    }
    gsap.delayedCall(0.3, ask);
  };

  new ResizeObserver(layout).observe(canvas);
  if (reducedMotion) return;
  new IntersectionObserver(([entry]) => (onScreen = entry.isIntersecting)).observe(canvas);
  ask();
}

await loadScript(document.querySelector("[data-gsap]").dataset.gsap);
gsap.registerPlugin(TypeTextPlugin);
// Tweens write when they first render rather than at the end of the tick: later tweens measure the stage.
gsap.defaults({ lazy: false });
registerEffects();
drawSeatPlan(document.querySelector("[data-seats]"));
for (const root of document.querySelectorAll("[data-demo]")) playDemo(root);
