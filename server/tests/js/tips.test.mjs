// Подсказки (S42): словарь покрывает подписи кнопок и вкладок из кода экранов; положение подсказки на экране
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { SCREENS } from "../../web/js/screens/index.js";
import { TIPS, tipFor } from "../../web/js/tips.js";
import { placeTip } from "../../web/js/tooltip.js";

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../web");

// ------------------------------------------------------------------ разбор кода: подписи кнопок

function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    return entry.isDirectory() ? walk(full) : entry.name.endsWith(".js") ? [full] : [];
  });
}

/** Аргументы вызова, который начинается с позиции `open` (индекс скобки «(»): разбор с учётом строк, шаблонов и комментариев. */
function callArgs(src, open) {
  const args = [];
  let depth = 0;
  let start = open + 1;
  for (let i = open; i < src.length; i += 1) {
    const ch = src[i];
    if (ch === '"' || ch === "'" || ch === "`") {
      const quote = ch;
      for (i += 1; i < src.length && src[i] !== quote; i += 1) {
        if (src[i] === "\\") i += 1;
        else if (quote === "`" && src[i] === "$" && src[i + 1] === "{") {   // вложенное выражение шаблона
          let nest = 0;
          for (; i < src.length; i += 1) {
            if (src[i] === "{") nest += 1;
            if (src[i] === "}" && (nest -= 1) === 0) break;
          }
        }
      }
    } else if (ch === "/" && src[i + 1] === "/") {
      while (i < src.length && src[i] !== "\n") i += 1;
    } else if (ch === "/" && src[i + 1] === "*") {
      i = src.indexOf("*/", i) + 1;
    } else if ("([{".includes(ch)) {
      depth += 1;
    } else if (")]}".includes(ch)) {
      depth -= 1;
      if (depth === 0) {
        args.push(src.slice(start, i).trim());
        return args;
      }
    } else if (ch === "," && depth === 1) {
      args.push(src.slice(start, i).trim());
      start = i + 1;
    }
  }
  return args;
}

const STRING = /^(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'|`([^`$]*)`)$/;
const LITERALS = /"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'/g;
const hasLetters = (text) => /[A-Za-zА-Яа-яЁё]/.test(text);

/** Кнопки в коде: {file, labels: [литералы], dynamic: bool, attrs}. */
function findButtons() {
  const found = [];
  for (const file of walk(path.join(WEB, "js")).filter((f) => !/[\\/](tips|tooltip)\.js$/.test(f))) {
    const src = fs.readFileSync(file, "utf8");
    for (const match of src.matchAll(/\bh\(\s*"button(?:\.[\w-]+)*"|\bactBtn\(/g)) {
      const open = src.indexOf("(", match.index);
      const args = callArgs(src, open);
      const isAct = match[0].startsWith("actBtn");
      const attrs = args.filter((a) => a.startsWith("{")).join(" ");
      const children = isAct ? [args[1]] : args.slice(1).filter((a) => !a.startsWith("{"));
      const labels = [];
      let dynamic = true;
      for (const arg of children) {
        const exact = STRING.exec(arg || "");
        if (exact) {
          const text = exact[1] ?? exact[2] ?? exact[3];
          if (hasLetters(text)) { labels.push(text); dynamic = false; }
        } else if (arg && !arg.startsWith("h(") && /\?/.test(arg)) {   // «cond ? "A" : "B"»
          for (const lit of arg.matchAll(LITERALS)) {
            const text = lit[1] ?? lit[2];
            if (hasLetters(text)) { labels.push(text); dynamic = false; }
          }
        }
      }
      found.push({ file: path.relative(WEB, file), labels, dynamic, attrs, line: src.slice(0, match.index).split("\n").length });
    }
  }
  return found;
}

// кнопки, у которых подпись берётся из данных (список тем, названия вакансий): подсказки по ним проверяются отдельно
const DYNAMIC_OK = new Set(["js/theme.js", "js/shell.js"]);

test("у каждой подписи кнопки в коде есть подсказка в словаре", () => {
  const missing = [];
  for (const button of findButtons()) {
    for (const label of button.labels) {
      if (!TIPS[label]) missing.push(`${button.file}:${button.line} «${label}»`);
    }
  }
  assert.deepEqual([...new Set(missing)], [], "добавьте эти подписи в web/js/tips.js");
});

test("кнопка с подписью из данных получает подсказку через data-tip", () => {
  const bad = findButtons().filter((b) => b.dynamic && !DYNAMIC_OK.has(b.file) && !/data-tip|dataset/.test(b.attrs))
    .map((b) => `${b.file}:${b.line}`);
  assert.deepEqual(bad, [], "кнопка без видимой подписи в коде: задайте data-tip");
});

test("вкладки, кнопки темы и входа из разметки — тоже в словаре", () => {
  for (const screen of Object.values(SCREENS)) assert.ok(TIPS[screen.title], `вкладка «${screen.title}»`);
  const theme = fs.readFileSync(path.join(WEB, "js/theme.js"), "utf8");
  for (const [, label] of theme.matchAll(/\["(?:light|dark|auto)", "([^"]+)"\]/g)) assert.ok(TIPS[label], `тема «${label}»`);
  const html = fs.readFileSync(path.join(WEB, "index.html"), "utf8");
  for (const [, label] of html.matchAll(/<button[^>]*>([^<]+)<\/button>/g)) {
    if (label.trim() !== "К содержимому") assert.ok(TIPS[label.trim()], `кнопка «${label.trim()}» в index.html`);
  }
});

test("значки без подписи (data-tip) в коде ссылаются на ключи словаря или дают готовый текст", () => {
  const keys = [];
  for (const file of walk(path.join(WEB, "js")).filter((f) => !/[\\/](tips|tooltip)\.js$/.test(f))) {
    const src = fs.readFileSync(file, "utf8");
    for (const [, key] of src.matchAll(/(?:"data-tip"|\btip):\s*"([^"]+)"/g)) keys.push([path.relative(WEB, file), key]);
  }
  assert.ok(keys.length >= 8, "ожидаются значки, фото, поля и заголовки с data-tip");
  for (const [file, key] of keys) assert.ok(key.length > 0, file);
  const used = new Set(keys.map(([, key]) => key));
  for (const key of ["face", "score", "search", "comment", "pill-where", "sort", "filter", "template"]) assert.ok(used.has(key), `data-tip="${key}" нигде не стоит`);
});

test("словарь: подсказки короткие, простыми словами, без разметки", () => {
  for (const [key, text] of Object.entries(TIPS)) {
    assert.ok(text.trim().length >= 10, key);
    assert.ok(text.length <= 330, `${key}: слишком длинная подсказка (${text.length})`);
    assert.ok(!/[<>]/.test(text), `${key}: разметка в тексте`);
  }
});

// ------------------------------------------------------------------ tipFor

const el = (attrs = {}, text = "") => ({ getAttribute: (name) => (name in attrs ? attrs[name] : null), textContent: text });

test("tipFor: data-tip → подпись → aria-label; нет в словаре — пусто", () => {
  assert.equal(tipFor(el({ "data-tip": "face" })), TIPS.face);
  assert.equal(tipFor(el({ "data-tip": "Свой текст подсказки" })), "Свой текст подсказки");
  assert.equal(tipFor(el({}, "  В работу ")), TIPS["В работу"]);
  assert.equal(tipFor(el({ "aria-label": "Сбросить" }, "×")), TIPS["Сбросить"]);
  assert.equal(tipFor(el({}, "Сегодня 8")), TIPS["Сегодня"]);   // вкладка с бейджем
  assert.equal(tipFor(el({}, "Какая-то неизвестная кнопка")), "");
  assert.equal(tipFor(null), "");
});

// ------------------------------------------------------------------ положение

const VIEW = { width: 1280, height: 800 };
const SIZE = { width: 300, height: 80 };
const overlaps = (a, b) => a.left < b.left + b.width && a.left + a.width > b.left && a.top < b.top + b.height && a.top + a.height > b.top;

test("подсказка по умолчанию ниже элемента и не перекрывает его", () => {
  const rect = { left: 100, top: 100, width: 120, height: 36 };
  const pos = placeTip(rect, SIZE, VIEW);
  assert.equal(pos.placement, "below");
  assert.ok(pos.top >= rect.top + rect.height);
  assert.equal(overlaps({ ...pos, ...SIZE }, rect), false);
});

test("внизу экрана подсказка уходит выше элемента", () => {
  const rect = { left: 100, top: 740, width: 120, height: 36 };
  const pos = placeTip(rect, SIZE, VIEW);
  assert.equal(pos.placement, "above");
  assert.ok(pos.top + SIZE.height <= rect.top);
});

test("у правого края подсказка не выходит за окно", () => {
  const rect = { left: 1240, top: 100, width: 30, height: 30 };
  const pos = placeTip(rect, SIZE, VIEW);
  assert.ok(pos.left + SIZE.width <= VIEW.width - 8);
  assert.ok(pos.left >= 8);
});

test("у левого края подсказка не выходит за окно", () => {
  const rect = { left: -20, top: 100, width: 60, height: 30 };
  assert.equal(placeTip(rect, SIZE, VIEW).left, 8);
});

test("очень высокий элемент: подсказка остаётся в окне", () => {
  const rect = { left: 100, top: 0, width: 400, height: 790 };
  const pos = placeTip(rect, SIZE, VIEW);
  assert.ok(pos.top >= 8 && pos.top + SIZE.height <= VIEW.height - 8);
});
