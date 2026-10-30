// Личное обучение без DOM (S14–S16, S21): поправки к баллу (плюс, минус, стоп-слова, веса), предложения агента, разница для сохранения.
// Данные — GET /api/tweaks: {items: [{id, kind, value, state, source, in_digest}], weights: общие веса}. Сервер проверяет всё сам (белый список).
import { countChars } from "../decisions-model.js";
import { FEATURES, featureLabel } from "./profiles-model.js";

export const MIN_WORD = 2;
export const MAX_WORD = 60;

export const CHAT_CAPTION = "Ваши замечания видите вы, агент и руководитель — в недельной сводке";
export const CHAT_INTRO = "Сюда каждый пишет замечания, рекомендации и критику. Агент учится под вас: предлагает личные поправки к вашему баллу. "
  + "Раз в неделю все замечания уходят руководителю сводкой; общими поправки становятся только после его решения.";
export const SAVE_HINT = "Меняет только ваш балл и порядок ваших списков";
export const RESET_HINT = "Сбросить к общим: личные поправки выключатся, ваш балл снова совпадёт с общим";

export const WORD_KINDS = [["plus", "Плюс", "слова, которые поднимают балл"], ["minus", "Минус", "слова, которые перестают считаться"], ["stop", "Стоп-слова", "слова, при которых балл падает"]];

const norm = (text) => String(text ?? "").replace(/\s+/g, " ").trim();
const wordKey = (text) => norm(text).toLowerCase().replaceAll("ё", "е");

/** Слово или фраза поправки: 2–60 знаков, без ссылок и разметки, без повтора в своём списке. */
export function validateWord(text, existing = []) {
  const value = norm(text);
  const length = countChars(value);
  if (length < MIN_WORD || length > MAX_WORD) return { ok: false, error: `Слово или фраза — от ${MIN_WORD} до ${MAX_WORD} знаков` };
  if (/https?:\/\/|www\.|[<>]/i.test(value)) return { ok: false, error: "Без ссылок и разметки" };
  if (existing.some((x) => wordKey(x) === wordKey(value))) return { ok: false, error: "Это слово уже есть в списке" };
  return { ok: true, value };
}

/** Поправки по видам: действующие — по спискам, предложения агента — отдельно; выключенные не показываются. */
export function groupTweaks(items) {
  const out = { plus: [], minus: [], stop: [], weights: {}, proposed: [] };
  for (const t of items || []) {
    if (t.state === "proposed") out.proposed.push(t);
    else if (t.state === "active") {
      if (t.kind === "weight") out.weights[t.value.feature] = t.value.weight;
      else if (out[t.kind]) out[t.kind].push(t);
    }
  }
  return out;
}

export function tweakText(tweak) {
  const v = tweak.value || {};
  switch (tweak.kind) {
    case "plus": return `плюс «${v.text}»`;
    case "minus": return `минус «${v.text}»`;
    case "stop": return `стоп-слово «${v.text}»`;
    case "weight": return `вес «${featureLabel(v.feature)}» → ${v.weight}`;
    default: return String(v.text ?? "");
  }
}

export const proposalText = (tweak) => `По вашим замечаниям: ${tweakText(tweak)}`;

/** Строки блока «Веса признаков»: общий вес рядом, свой поверх; без своей поправки свой равен общему. */
export function weightRows(common, mine) {
  return FEATURES.map(([key, label]) => {
    const shared = Number(common && common[key]) || 0;
    const own = mine && Number.isFinite(mine[key]) ? mine[key] : shared;
    return { key, label, common: shared, value: own, changed: own !== shared };
  });
}

/** Что сейчас в форме «Мои поправки»: слова по видам и свои веса (общие там, где своих нет). */
export function draftFrom(items, common) {
  const groups = groupTweaks(items);
  const weights = Object.fromEntries(weightRows(common, groups.weights).map((r) => [r.key, r.value]));
  return { plus: groups.plus.map((t) => t.value.text), minus: groups.minus.map((t) => t.value.text), stop: groups.stop.map((t) => t.value.text), weights };
}

/**
 * Разница между сохранённым и формой: что добавить (POST /api/tweaks) и какие поправки выключить (POST /api/tweak/{id}/off).
 * Вес, вернувшийся к общему, — выключение своей поправки; другой вес — новая поправка (прежнюю по этому признаку сервер выключит сам).
 */
export function diffTweaks(items, draft, common) {
  const groups = groupTweaks(items);
  const add = [];
  const off = [];
  for (const kind of ["plus", "minus", "stop"]) {
    const wanted = (draft[kind] || []).map(norm);
    const have = groups[kind];
    for (const word of wanted) if (!have.some((t) => wordKey(t.value.text) === wordKey(word))) add.push({ kind, value: { text: word } });
    for (const t of have) if (!wanted.some((w) => wordKey(w) === wordKey(t.value.text))) off.push(t.id);
  }
  for (const row of weightRows(common, groups.weights)) {
    const target = Number(draft.weights && draft.weights[row.key]);
    if (!Number.isInteger(target) || target === row.value) continue;
    if (target === row.common) {
      const own = (items || []).find((t) => t.state === "active" && t.kind === "weight" && t.value.feature === row.key);
      if (own) off.push(own.id);
    } else {
      add.push({ kind: "weight", value: { feature: row.key, weight: target } });
    }
  }
  return { add, off };
}

export function isDirty(items, draft, common) {
  const diff = diffTweaks(items, draft, common);
  return diff.add.length > 0 || diff.off.length > 0;
}
