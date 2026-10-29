// Сборка DOM без разбора HTML из строк. Всё, что пришло из API (резюме, сообщения, ответы модели), выводится
// только через h() / textContent: строка всегда становится текстовым узлом и никогда — разметкой.
//
//   h("div.card.inset#id", {class: "x", "aria-label": "…", onClick: fn, style: {gap: "8px"}}, "текст", узел, [ещё])
//   h("button.b.fill", {type: "button", disabled: busy, onClick: save}, "Сохранить")
//   h("svg", {viewBox: "0 0 40 40"}, h("svg:circle", {cx: 20, cy: 15, r: 7}))
//
// Правила:
//  - второй аргумент — атрибуты, если это обычный объект; иначе он считается первым потомком;
//  - потомки: строка/число → текст, Node → как есть, массив → разворачивается, null/undefined/false/true → пропуск;
//  - обработчики — только функции в атрибутах onClick, onInput… (addEventListener); onclick="…" строкой запрещён;
//  - null/undefined/false-атрибут пропускается, true → пустой атрибут (для aria-* и data-* — "true"/"false");
//  - адреса (href, src …) — только http(s), относительные и якоря; javascript:, data: и прочее отбрасываются;
//  - ключи, которые разбирают строку как разметку, запрещены и бросают ошибку.

const SVG_NS = "http://www.w3.org/2000/svg";
const TAG = /^(svg:)?([a-zA-Z][\w-]*)((?:[.#][\w-]+)*)$/;
const MARKUP_KEYS = /^(?:inner|outer)html$|^srcdoc$/i;
const URL_ATTRS = new Set(["href", "src", "action", "formaction", "poster", "xlink:href"]);
const PROPS = new Set(["value", "checked", "selected", "indeterminate"]);
const BOOLEAN_PROPS = new Set(["disabled", "hidden", "required", "readonly", "multiple", "open"]);

let counter = 0;
/** Уникальный id для связки label/aria-describedby. */
export function uid(prefix = "id") {
  counter += 1;
  return `${prefix}-${counter}`;
}

/** Адрес можно вставлять в href/src: схема http/https или относительный путь. */
export function isSafeUrl(value) {
  const compact = String(value).replace(/[\u0000- \u007f-\u009f]/g, "");
  const scheme = /^([a-zA-Z][a-zA-Z0-9+.-]*):/.exec(compact);
  return scheme ? /^https?$/i.test(scheme[1]) : true;
}

function appendChild(parent, child) {
  if (child === null || child === undefined || child === false || child === true) return;
  if (Array.isArray(child)) {
    child.forEach((c) => appendChild(parent, c));
  } else if (child instanceof Node) {
    parent.appendChild(child);
  } else {
    parent.appendChild(document.createTextNode(String(child)));
  }
}

function setStyle(el, style) {
  if (typeof style === "string") {
    el.setAttribute("style", style);
    return;
  }
  for (const [key, value] of Object.entries(style)) {
    if (value === null || value === undefined || value === false) continue;
    const name = key.startsWith("--") ? key : key.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`);
    el.style.setProperty(name, String(value));
  }
}

function setAttr(el, key, value) {
  if (MARKUP_KEYS.test(key)) throw new Error(`h(): атрибут ${key} запрещён, данные выводятся только текстом`);
  if (/^on/i.test(key)) {
    if (typeof value !== "function") throw new TypeError(`h(): ${key} — только функция (addEventListener), не строка`);
    el.addEventListener(key.slice(2).toLowerCase(), value);
    return;
  }
  if (value === null || value === undefined) return;
  if (key === "class" || key === "className") {
    const text = Array.isArray(value) ? value.filter(Boolean).join(" ") : String(value);
    if (text) el.setAttribute("class", `${el.getAttribute("class") || ""} ${text}`.trim());
    return;
  }
  if (key === "style") return setStyle(el, value);
  if (key === "dataset") {
    for (const [name, v] of Object.entries(value)) if (v !== null && v !== undefined) el.dataset[name] = String(v);
    return;
  }
  if (PROPS.has(key)) {
    el[key] = value;
    return;
  }
  if (BOOLEAN_PROPS.has(key)) {
    if (value) el.setAttribute(key, "");
    return;
  }
  if (URL_ATTRS.has(key) && !isSafeUrl(value)) {
    console.warn(`h(): адрес в ${key} отброшен как небезопасный`);
    return;
  }
  if (typeof value === "boolean") {
    if (/^(aria|data)-/.test(key)) el.setAttribute(key, String(value));
    else if (value) el.setAttribute(key, "");
    return;
  }
  el.setAttribute(key, String(value));
}

export function h(tag, attrs, ...children) {
  const match = TAG.exec(tag);
  if (!match) throw new Error(`h(): непонятный тег «${tag}»`);
  const [, svgPrefix, name, suffix] = match;
  const el = svgPrefix || name === "svg" ? document.createElementNS(SVG_NS, name) : document.createElement(name);
  const classes = [];
  for (const part of suffix.match(/[.#][\w-]+/g) || []) {
    if (part[0] === "#") el.setAttribute("id", part.slice(1));
    else classes.push(part.slice(1));
  }
  if (classes.length) el.setAttribute("class", classes.join(" "));

  const isAttrs = attrs && typeof attrs === "object" && !Array.isArray(attrs) && !(attrs instanceof Node);
  if (isAttrs) {
    for (const [key, value] of Object.entries(attrs)) setAttr(el, key, value);
  } else {
    children.unshift(attrs);
  }
  children.forEach((child) => appendChild(el, child));

  if (el.localName === "a" && el.getAttribute("target") === "_blank") el.setAttribute("rel", "noopener noreferrer");
  return el;
}

/** Фрагмент из потомков (те же правила, что у h()). */
export function frag(...children) {
  const fragment = document.createDocumentFragment();
  children.forEach((child) => appendChild(fragment, child));
  return fragment;
}

/** Заменить содержимое узла: mount(box, a, b). Без потомков — очистить. */
export function mount(parent, ...children) {
  parent.replaceChildren(frag(...children));
  return parent;
}

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

/** Инициалы для кружка: «Иванова Мария» → «ИМ». Без букв — пустая строка. */
export function initials(name) {
  return String(name ?? "")
    .split(/\s+/)
    .map((word) => (word.replace(/[^\p{L}]/gu, "")[0] || ""))
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();
}
