"use strict";
// Runs the inline scripts of one motion block against a stub document and a recording gsap, then prints what
// happened as JSON: the error the script threw (or null), the tweens it put on the timeline (target, from, to, start,
// duration, ease) and the elements it touched. It is the test double of tests/motion/block_harness.py: no browser, no
// layout, no real GSAP; the block's own timing logic runs for real.
//
// usage: node block_harness.js BLOCK.html < CONFIG.json
//   CONFIG: {"variables": {...overrides...}, "css": {"--custom-property": "value", ...}}
const fs = require("fs");
const vm = require("vm");

const htmlPath = process.argv[2];
const html = fs.readFileSync(htmlPath, "utf8");
const config = JSON.parse(fs.readFileSync(0, "utf8"));

const markup = html.slice(html.indexOf("<body")).replace(/<script[\s\S]*?<\/script>/g, "");
const classesInMarkup = [...markup.matchAll(/class="([^"]*)"/g)].flatMap((m) => m[1].split(/\s+/));
const idsInMarkup = [...markup.matchAll(/\bid="([^"]*)"/g)].map((m) => m[1]);
const elements = [];
let nextId = 1;

class El {
  constructor(selector) {
    this.id = nextId++;
    this.selector = selector;
    this.parent = null;
    this.attrs = {};
    this.style = {};
    this.className = "";
    this.textContent = "";
    elements.push(this);
  }
  setAttribute(name, value) {
    this.attrs[name] = String(value);
  }
  appendChild(child) {
    child.parent = this.id;
    return child;
  }
}

const queried = new Map();
const document = {
  documentElement: {
    getAttribute(name) {
      const m = html.match(new RegExp(name + "='([^']*)'"));
      return m ? m[1] : null;
    },
  },
  querySelector(selector) {
    if (!queried.has(selector)) {
      const m = selector.match(/^([.#])([\w-]+)$/);
      if (!m || !(m[1] === "." ? classesInMarkup : idsInMarkup).includes(m[2])) {
        throw new Error("the markup has no element for the selector " + selector);
      }
      queried.set(selector, new El(selector));
    }
    return queried.get(selector);
  },
  createElement() {
    return new El(null);
  },
  createTextNode(text) {
    const node = new El(null);
    node.textContent = text;
    return node;
  },
};

const tweens = [];
const targetsOf = (target) => (Array.isArray(target) ? target : [target]).map((el) => el.id);
const gsap = {
  timeline(options) {
    const tl = {};
    tl.fromTo = (target, from, to, position) => {
      if (typeof position !== "number") throw new Error("fromTo needs an explicit numeric position");
      tweens.push({ kind: "fromTo", targets: targetsOf(target), from, to, start: position, duration: to.duration, ease: to.ease });
      return tl;
    };
    tl.set = (target, vars, position) => {
      tweens.push({ kind: "set", targets: targetsOf(target), from: {}, to: vars, start: position || 0, duration: 0, ease: null });
      return tl;
    };
    tl.paused = options && options.paused === true;
    return tl;
  },
};

const window = {
  __timelines: {},
  __hyperframes: { getVariables: () => config.variables },
};
const getComputedStyle = () => ({
  getPropertyValue: (name) => (name in config.css ? config.css[name] : ""),
});

let error = null;
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
try {
  const sandbox = vm.createContext({ window, document, gsap, getComputedStyle });
  for (const code of scripts) vm.runInContext(code, sandbox);
} catch (e) {
  error = String(e && e.message ? e.message : e);
}

process.stdout.write(
  JSON.stringify({
    error,
    registered: Object.keys(window.__timelines),
    paused: Object.values(window.__timelines).map((tl) => tl.paused),
    tweens,
    elements: elements.map((el) => ({
      id: el.id, selector: el.selector, parent: el.parent, attrs: el.attrs, style: el.style, className: el.className, text: el.textContent,
    })),
  }),
);
