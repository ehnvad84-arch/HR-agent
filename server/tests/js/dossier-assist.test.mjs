// S12: флаг llm_assist_enabled в досье — при false шаблон сразу и ни одного запроса к модели (screens/person.js)
import assert from "node:assert/strict";
import test from "node:test";

// Минимальная подмена DOM: хватает h()/mount() и чтения текста узлов.
class FakeNode {
  constructor(name) { this.localName = name; this.children = []; this.attrs = new Map(); this.style = {}; this.listeners = {}; }
  setAttribute(k, v) { this.attrs.set(k, String(v)); }
  getAttribute(k) { return this.attrs.has(k) ? this.attrs.get(k) : null; }
  removeAttribute(k) { this.attrs.delete(k); }
  addEventListener() {}
  append(...c) { this.children.push(...c); }
  appendChild(c) { this.children.push(c); return c; }
  replaceChildren(...c) { this.children = c.flatMap((x) => (x instanceof FakeFragment ? x.children : [x])); }
  get textContent() { return this.children.map((c) => (typeof c === "string" ? c : c.textContent)).join(" "); }
  set textContent(v) { this.children = [String(v)]; }
}
class FakeFragment extends FakeNode {}
class FakeText { constructor(t) { this.t = t; } get textContent() { return this.t; } }
globalThis.Node = class {};
for (const C of [FakeNode, FakeText]) Object.setPrototypeOf(C.prototype, globalThis.Node.prototype);
globalThis.document = {
  createElement: (n) => new FakeNode(n),
  createElementNS: (_ns, n) => new FakeNode(n),
  createTextNode: (t) => new FakeText(t),
  createDocumentFragment: () => new FakeFragment("#frag"),
};

const { view } = await import("../../web/js/screens/person.js");

const TEMPLATE = "Подходит по профилю: шаблонный разбор из досье";
const person = { person_id: 7, watch: "none", label: "Инженер, Краснодар" };
const match = { role_profile_id: 11, role_title: "Инженер-электронщик", profile_version: "v1", score: 80, features: [],
  model: "template", explanation: TEMPLATE };
const data = (extra) => ({ person, matches: [match], observations: [], social: [], candidate: null, events: [], decisions: [],
  ...extra });
const env = () => ({ explained: new Map(), session: { user: { role: "recruiter" }, can: () => false }, api: {}, signal: null });

test("S12: llm_assist_enabled=false — разбор сразу шаблоном, без «Агент пишет разбор…»", () => {
  const built = view(data({ llm_assist_enabled: false }), env());
  const text = built.slots.get(11).el.textContent;
  assert.match(text, /шаблонный разбор из досье/);
  assert.doesNotMatch(text, /Агент пишет разбор/);
  assert.equal(built.slots.get(11).el.getAttribute("aria-busy"), null);
});

test("S12: llm_assist_enabled=true — шаблон ждёт разбор моделью", () => {
  const built = view(data({ llm_assist_enabled: true }), env());
  assert.match(built.slots.get(11).el.textContent, /Агент пишет разбор/);
  assert.equal(built.slots.get(11).el.getAttribute("aria-busy"), "true");
});

const { render } = await import("../../web/js/screens/person.js");

async function open(flag) {
  const posts = [];
  const api = {
    get: async () => data({ llm_assist_enabled: flag }),
    post: async (url, body) => { posts.push([url, body]); return { text: "Разбор модели", by: "model" }; },
  };
  const ac = new AbortController();
  globalThis.window = { addEventListener() {}, removeEventListener() {} };
  const box = new FakeNode("div");
  box.querySelectorAll = () => [];
  await render(box, { api, signal: ac.signal, params: { id: "7" }, session: env().session });
  await new Promise((resolve) => setImmediate(resolve));   // queueMicrotask с explainAll отработал
  return { posts, box };
}

test("S12: при llm_assist_enabled=false досье не шлёт запросов к модели", async () => {
  const { posts, box } = await open(false);
  assert.deepEqual(posts, []);
  assert.match(box.textContent, /шаблонный разбор из досье/);
});

test("S12: при llm_assist_enabled=true досье просит разбор моделью (контроль)", async () => {
  const { posts } = await open(true);
  assert.deepEqual(posts, [["/api/person/7/explain", { role_profile_id: 11 }]]);
});
