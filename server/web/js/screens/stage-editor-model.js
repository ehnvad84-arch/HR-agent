// Редактор набора этапов вакансии без DOM (S3, S5): черновик набора, ↑/↓ внутри строки отчёта, «убрать», что уходит на сервер.
// Черновик — массив {key, slot, stage_id, title, report_row, label, standard}: у этапа действующего набора key = «s<slot>»,
// у нового — «n<номер>». Правила набора проверяет сервер (hr/stages.py); здесь — только подсказки и блокировка лишних кнопок.
import { ROW_TITLES, peopleText } from "./stages-model.js";

/** Строки отчёта, которые из набора не убираются (S5). */
export const FIXED_ROWS = ["selected", "first_day"];
export const NOT_IN_SET = "в отчёте — «нет в наборе»";
export const FIXED_REASON = "Эту строку отчёта нельзя убрать из набора";

const ROW_ORDER = Object.keys(ROW_TITLES);
const rowOrd = (code) => ROW_ORDER.indexOf(code);
const labelOf = (value) => (value ? String(value).trim() : "");

/** Черновик из набора (действующего, отклонённого или отозванного): копия, исходный набор не меняется. */
export function toDraft(items) {
  return (items || []).map((i) => ({
    key: `s${i.slot}`, slot: i.slot, stage_id: i.stage_id, title: i.title, report_row: i.report_row,
    label: i.label || "", standard: Boolean(i.is_standard),
  }));
}

/**
 * Черновик из чужого набора (отозванного, отклонённого) поверх действующего: у этапов, которых в действующем наборе нет,
 * slot пустеет — сервер выдал им номер при предложении, а при новом предложении выдаст заново.
 */
export function rebaseDraft(items, current) {
  const known = new Set((current || []).map((i) => i.slot));
  let counter = 0;
  return toDraft(items).map((d) => {
    if (known.has(d.slot)) return d;
    counter += 1;
    return { ...d, key: `r${counter}`, slot: null };
  });
}

/** Новый этап из справочника в конец своей строки отчёта; нет строки — на место по порядку воронки (S3, S5). */
export function addItem(draft, stage, key) {
  const item = { key, slot: null, stage_id: stage.id, title: stage.title, report_row: stage.report_row, label: "", standard: Boolean(stage.is_standard) };
  const next = [...draft];
  let at = -1;
  for (let i = 0; i < next.length; i += 1) if (next[i].report_row === stage.report_row) at = i;
  if (at < 0) {
    at = next.findIndex((d) => rowOrd(d.report_row) > rowOrd(stage.report_row));
    if (at < 0) next.push(item);
    else next.splice(at, 0, item);
  } else {
    next.splice(at + 1, 0, item);
  }
  return next;
}

export const removeItem = (draft, key) => draft.filter((d) => d.key !== key);

function neighbour(draft, index, step) {
  const other = draft[index + step];
  return other && other.report_row === draft[index].report_row ? index + step : -1;
}

/** ↑/↓ (step −1 / +1) — только внутри своей строки отчёта; на границе строки ничего не меняется. */
export function moveItem(draft, key, step) {
  const index = draft.findIndex((d) => d.key === key);
  if (index < 0) return draft;
  const to = neighbour(draft, index, step);
  if (to < 0) return draft;
  const next = [...draft];
  [next[index], next[to]] = [next[to], next[index]];
  return next;
}

/** «На этапе 3 человека — сначала переведите их» (S5): склонение как у ошибки сервера. */
export function busyText(count) {
  const tail = count % 100;
  const word = tail % 10 >= 2 && tail % 10 <= 4 && Math.floor(tail / 10) !== 1 ? "человека" : "человек";
  return `На этапе ${count} ${word} — сначала переведите их`;
}

/** Можно ли убрать этап: нет людей, не последний этап обязательной строки; последний этап другой строки — можно, строка уйдёт из набора. */
function removeRule(draft, item, count) {
  const inRow = draft.filter((d) => d.report_row === item.report_row).length;
  if (count > 0) return { disabled: true, reason: busyText(count), lastInRow: inRow === 1, note: "" };
  if (inRow === 1 && FIXED_ROWS.includes(item.report_row)) return { disabled: true, reason: FIXED_REASON, lastInRow: true, note: "" };
  return { disabled: false, reason: "", lastInRow: inRow === 1, note: inRow === 1 ? NOT_IN_SET : "" };
}

/** Этапы черновика по строкам отчёта; people — {slot: человек на этапе} действующего набора. */
export function groupDraft(draft, people = {}) {
  const groups = [];
  draft.forEach((item, index) => {
    let group = groups[groups.length - 1];
    if (!group || group.code !== item.report_row) {
      group = { code: item.report_row, title: ROW_TITLES[item.report_row] || String(item.report_row), items: [] };
      groups.push(group);
    }
    const count = item.slot ? Number((people || {})[item.slot]) || 0 : 0;
    group.items.push({
      key: item.key, slot: item.slot, title: item.label || item.title, stageTitle: item.title, label: item.label, count,
      peopleText: peopleText(count), up: neighbour(draft, index, -1) >= 0, down: neighbour(draft, index, 1) >= 0,
      remove: removeRule(draft, item, count),
    });
  });
  return groups;
}

/** Строки отчёта, которых в черновике нет: в отчёте они «нет в наборе» (S5). rows — строки из GET /api/stages. */
export function removedRows(draft, rows) {
  const present = new Set(draft.map((d) => d.report_row));
  return (rows || []).filter((r) => !present.has(r.code));
}

/** «„Тестовое задание“ — 2-й в строке „Интервью“»: объявление после ↑/↓. */
export function movePlace(draft, key, rows = []) {
  const item = draft.find((d) => d.key === key);
  if (!item) return "";
  const same = draft.filter((d) => d.report_row === item.report_row);
  const row = (rows.find((r) => r.code === item.report_row) || {}).title || ROW_TITLES[item.report_row] || "";
  return `«${item.label || item.title}» — ${same.indexOf(item) + 1}-й в строке «${row}»`;
}

/** Отличается ли черновик от действующего набора: состав, порядок или подпись. «Отправить на утверждение» активна только тогда. */
export function isChanged(draft, current) {
  if (draft.length !== (current || []).length) return true;
  return draft.some((d, i) => d.slot !== current[i].slot || d.stage_id !== current[i].stage_id || labelOf(d.label) !== labelOf(current[i].label));
}

/** Тело POST /api/vacancy/{id}/stage-sets: у прежних этапов — slot, у новых — только stage_id; пустая подпись и причина не уходят. */
export function proposalPayload(draft, note) {
  const items = draft.map((d) => {
    const item = d.slot ? { slot: d.slot, stage_id: d.stage_id } : { stage_id: d.stage_id };
    if (labelOf(d.label)) item.label = labelOf(d.label);
    return item;
  });
  const text = labelOf(note);
  return text ? { items, note: text } : { items };
}

/** «Добавить этап»: действующие этапы справочника по строкам отчёта (optgroup); library — ответ GET /api/stages. */
export function addOptions(library) {
  const active = (library.stages || []).filter((s) => s.is_active);
  return (library.rows || []).map((r) => ({
    code: r.code, title: r.title, items: active.filter((s) => s.report_row === r.code).map((s) => ({ id: s.id, title: s.title, report_row: s.report_row, is_standard: s.is_standard })),
  })).filter((g) => g.items.length);
}
