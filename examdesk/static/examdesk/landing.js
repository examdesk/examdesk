// The landing page (landing.html): the seat plan behind the headline, and the demos. Each demo is a GSAP timeline over
// frames of the app's own pages (demos.py), showing one at a time per device (`data-shown`).

const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const REPEAT_DELAY = 1.5;
const EASE = "power2.inOut";

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
  const register = (name, effect) => gsap.registerEffect({ name, effect, extendTimeline: true });

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
        .set(field.closest(".ts-control") ?? field, { attr: { "data-focus": 1 } })
        .to(field, { typeText: field.dataset.text, duration: field.dataset.text.length / speed, ease: "none" })
        .set(field.closest(".ts-control") ?? field, { attr: { "data-focus": 0 } }, "+=0.2"),
  );

  register("toast", ([toast]) =>
    gsap
      .timeline()
      .fromTo(toast, { autoAlpha: 0, y: 12 }, { autoAlpha: 1, y: 0, duration: 0.3, immediateRender: false })
      .to(toast, { autoAlpha: 0, y: 12, duration: 0.3 }, "+=2"),
  );
}

// Plays a demo's actions (demos.py) over its frames, each at its GSAP position: a pointer clicks on the window and a
// finger taps the phone, fields fill a letter at a time, and each device shows its frames in turn.
function playActions(timeline, stage, actions) {
  const devices = Object.fromEntries(
    [...stage.querySelectorAll("[data-device]")].map((device) => [
      device.dataset.device,
      { frames: [...device.querySelectorAll(".landing-frame")], index: 0 },
    ]),
  );
  const frame = (device) => devices[device].frames[devices[device].index];
  const find = ({ device, on }) =>
    frame(device).querySelector(on) ?? console.error(`No ${on} in the ${device}'s frame ${devices[device].index}`);
  for (const action of actions) {
    const { position } = action;
    switch (action.type) {
      case "step":
        timeline.addLabel(action.name, position);
        break;
      // The device shows its next frame at once, scrolled as the one before.
      case "next_frame": {
        const from = frame(action.device);
        devices[action.device].index += 1;
        const to = frame(action.device);
        timeline
          .set(to, { attr: { "data-shown": 1 } }, position)
          .set(to.querySelector("main"), { scrollTop: () => from.querySelector("main").scrollTop }, "<")
          .set(from, { attr: { "data-shown": 0 } }, "<");
        break;
      }
      case "tap":
        timeline[action.device === "phone" ? "tap" : "click"](find(action), {}, position);
        break;
      case "type": {
        const field = find(action);
        field.dataset.text = action.text;
        timeline.type(field, { speed: action.speed }, position);
        break;
      }
      case "set":
        timeline.set(frame(action.device).querySelectorAll(action.on), action.props, position);
        break;
      // Attributes set, or with null removed, before the demo plays: GSAP can't set and restore some, e.g. `src`.
      case "set_attributes":
        for (const element of frame(action.device).querySelectorAll(action.on)) {
          for (const [name, value] of Object.entries(action.attributes)) {
            if (value === null) element.removeAttribute(name);
            else element.setAttribute(name, value);
          }
        }
        break;
      case "scroll": {
        const main = frame(action.device).querySelector("main");
        const end = () => main.scrollHeight - main.clientHeight;
        timeline.to(main, { scrollTop: end, duration: 0.5, ease: EASE }, position);
        break;
      }
      case "toast":
        timeline.toast(stage.querySelector(`[data-toast="${action.index}"]`), {}, position);
        break;
      case "set_active":
        timeline.set(stage, { attr: { "data-active": action.on } }, position);
        break;
      case "pause":
        timeline.set({}, {}, position);
        break;
    }
  }
}

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
  // The pointer waits at the foot of the window.
  const windowDevice = stage.querySelector('[data-device="window"]');
  if (pointer) {
    const x = windowDevice.offsetLeft + windowDevice.offsetWidth - 120;
    gsap.set(pointer, { autoAlpha: 1, x, y: windowDevice.offsetTop + windowDevice.offsetHeight - 40 });
  }
  const steps = [...root.querySelectorAll("[data-step]")];
  const timeline = gsap.timeline({ paused: true, defaults: { duration: 0.35, ease: "power2.out" } });
  playActions(timeline, stage, JSON.parse(root.querySelector(".landing-stage-box > script").textContent));
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
