// Карточка «Синонимы ключей» в «Базе должностей» (S1–S4): чистая логика без DOM.
// Черновик правки — копия синонимов с экрана; сервер проверяет всё повторно (hr/synonyms.py), здесь только то, что видно человеку.
import { plural } from "../format.js";

export const HEADER_NOTE = "Действуют сразу, без утверждения. Каждая правка — в журнале, её можно откатить.";
export const READONLY_NOTE = "Синонимы правит рекрутер";
export const NO_KEYS_TIP = "Сначала добавьте ключевые слова и утвердите версию профиля";
export const THINKING_TEXT = "Модель думает до минуты. Вписывать свои синонимы можно и сейчас.";
export const EMPTY_SUGGEST = "Модель ничего не предложила — впишите синонимы сами";
export const SAVED_TEXT = "Синонимы сохранены. Профиль пересчитывается в фоне";
export const SCHEMA_NOTE = "Ход пересчёта — в «Схеме»";
export const ROLLBACK_HINT = "Синонимы станут как до неё. Откат тоже попадёт в журнал, профиль пересчитается в фоне";

/** Ключ для сравнения: как на сервере (hr/synonyms.py norm) — нижний регистр, «ё» → «е», лишние пробелы убраны. */
export const normKey = (text) => String(text ?? "").trim().toLowerCase().replaceAll("ё", "е").replace(/\s+/g, " ");

const same = (a, b) => normKey(a) === normKey(b);

// ------------------------------------------------------------------ черновик

/** Ответ GET …/synonyms → черновик: у ключа текущие термины, сохранённые и «новые» (от модели, ещё не сохранённые). */
export function draftFromView(view) {
  return {
    base: view && view.history && view.history.length ? view.history[0].id : null,   // последняя запись: по ней сервер увидит чужую правку (409)
    rows: ((view && view.items) || []).map((item) => ({
      keyword: item.keyword, saved: [...(item.synonyms || [])], terms: [...(item.synonyms || [])], fresh: [],
    })),
  };
}

/** Термин, который показывать нельзя: сам ключ или уже есть у любого ключа (повторы не показываются). */
function taken(draft, term) {
  return draft.rows.some((row) => same(row.keyword, term) || row.terms.some((t) => same(t, term)));
}

/** Предложение модели {ключ: [термины]} → в черновик. Возвращает {draft, added}: добавлено терминов (после отсева повторов). */
export function applySuggestions(draft, suggestions) {
  let added = 0;
  const rows = draft.rows.map((row) => ({ ...row, terms: [...row.terms], fresh: [...row.fresh] }));
  const next = { ...draft, rows };
  const byKey = new Map(Object.entries(suggestions || {}).map(([key, list]) => [normKey(key), Array.isArray(list) ? list : []]));
  for (const row of rows) {
    for (const raw of byKey.get(normKey(row.keyword)) || []) {
      const term = String(raw ?? "").trim();
      if (!term || taken(next, term)) continue;
      row.terms.push(term);
      row.fresh.push(term);
      added += 1;
    }
  }
  return { draft: next, added };
}

/** Чипсы ключа изменились (ввод, ×): новые термины (нет среди сохранённых и не от модели) остаются «своими», пометка «новое» — только у предложенных. */
export function setTerms(draft, keyword, terms) {
  return {
    ...draft,
    rows: draft.rows.map((row) => (row.keyword !== keyword ? row
      : { ...row, terms: [...terms], fresh: row.fresh.filter((f) => terms.some((t) => same(t, f))) })),
  };
}

/** Что добавлено и убрано относительно сохранённого. */
export function changeCount(draft) {
  let plus = 0;
  let minus = 0;
  for (const row of draft.rows) {
    plus += row.terms.filter((t) => !row.saved.some((s) => same(s, t))).length;
    minus += row.saved.filter((s) => !row.terms.some((t) => same(s, t))).length;
  }
  return { plus, minus };
}

export const isDirty = (draft) => { const { plus, minus } = changeCount(draft); return plus + minus > 0; };

/** «Не сохранено: +3 −1»; без правок — пустая строка. */
export function dirtyText(draft) {
  const { plus, minus } = changeCount(draft);
  if (!plus && !minus) return "";
  return `Не сохранено: ${[plus ? `+${plus}` : "", minus ? `−${minus}` : ""].filter(Boolean).join(" ")}`;
}

/** Тело PUT: все ключи со всеми синонимами (сервер сравнивает со снимком) и номер записи, от которой правили. */
export function savePayload(draft) {
  return { items: draft.rows.map((row) => ({ keyword: row.keyword, synonyms: [...row.terms] })), base_change_id: draft.base };
}

/** «Отменить»: вернуть сохранённое. */
export function discard(draft) {
  return { ...draft, rows: draft.rows.map((row) => ({ ...row, terms: [...row.saved], fresh: [] })) };
}

// ------------------------------------------------------------------ тексты

export function suggestedText(added) {
  return added > 0
    ? `Модель предложила ${added} ${plural(added, "термин", "термина", "терминов")}. Лишние уберите ×, свои допишите.`
    : EMPTY_SUGGEST;
}

/** Отказ «Предложить» (S4): {text, extra?} для .note. Тексты кодов — как в сценарии; чужое сообщение сервера — как пришло. */
export function suggestFailure(error) {
  const code = error && error.code;
  if (code === "llm_daily_limit" || (error && error.status === 429)) {
    return { text: "Предел запросов к модели на сегодня исчерпан", extra: "Вписать вручную можно всегда" };
  }
  if (code === "llm_unavailable" || (error && error.status === 503)) return { text: "Модель сейчас недоступна — впишите синонимы сами" };
  if (error && typeof error.status === "number" && error.message) return { text: error.message };
  return { text: "Не получилось спросить модель — впишите синонимы сами" };
}

// ------------------------------------------------------------------ состояние карточки

/**
 * Что показывать. state: {loading, error, view, draft, asking}. Режим: loading | error | ready.
 * Нет права — строки без полей и кнопок; нет ключей — «Предложить» неактивна (aria-disabled) с подсказкой.
 */
export function panelState({ loading = false, error = null, view = null, draft = null, asking = false } = {}) {
  if (error) return { mode: "error" };
  if (loading || !view || !draft) return { mode: "loading" };
  const canEdit = Boolean(view.can_edit);
  const hasKeys = draft.rows.length > 0;
  const dirty = isDirty(draft);
  return {
    mode: "ready", canEdit, hasKeys,
    suggest: canEdit ? { label: asking ? "Модель подбирает…" : "Предложить синонимы", disabled: asking, inactive: !hasKeys, tip: hasKeys ? "" : NO_KEYS_TIP, stop: asking } : null,
    readonlyNote: canEdit ? "" : READONLY_NOTE,
    showSave: canEdit && dirty, dirtyText: dirtyText(draft),
    emptyRow: (row) => row.terms.length === 0,
    historyTitle: `История синонимов (${(view.history || []).length})`,
  };
}

// ------------------------------------------------------------------ история

/** «Котенко Алексей Петрович» → «Котенко А.»; уже короткое и пустое — как есть, пустое — «система». */
export function shortName(name) {
  const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "система";
  if (parts.length === 1 || /\.$/.test(parts[1])) return parts.join(" ");
  return `${parts[0]} ${parts[1][0]}.`;
}

/** «22.11 14:05» (московское время сервера приходит со смещением; показываем в часовом поясе браузера, как остальной интерфейс). */
export function stampText(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const two = (n) => String(n).padStart(2, "0");
  return `${two(date.getDate())}.${two(date.getMonth() + 1)} ${two(date.getHours())}:${two(date.getMinutes())}`;
}

const dayOnly = (value) => stampText(value).split(" ")[0];

/** «Altium: +KiCad, −OrCAD; схемотехника: +разработка схем». Ключи diff нормализованы — показываем так, как они записаны в профиле. */
export function diffText(diff, keywords = []) {
  const names = new Map(keywords.map((k) => [normKey(k), k]));
  return Object.entries(diff || {}).map(([key, change]) => {
    const parts = [...((change && change["+"]) || []).map((t) => `+${t}`), ...((change && change["-"]) || []).map((t) => `−${t}`)];
    return `${names.get(key) || key}: ${parts.join(", ")}`;
  }).join("; ");
}

/** Строки истории: когда · кто · что; откат — «откат правки от 20.11». */
export function historyLines(view) {
  const history = (view && view.history) || [];
  const keywords = ((view && view.items) || []).map((i) => i.keyword);
  const byId = new Map(history.map((entry) => [entry.id, entry]));
  return history.map((entry) => {
    const target = entry.rollback_of != null ? byId.get(entry.rollback_of) : null;
    const text = entry.rollback_of != null
      ? `откат правки${target ? ` от ${dayOnly(target.applied_at)}` : ""}`
      : diffText(entry.diff, keywords);
    return { id: entry.id, when: stampText(entry.applied_at), by: shortName(entry.by), text, canRollback: Boolean(entry.can_rollback) };
  });
}

/** Окно подтверждения отката. */
export const rollbackSheet = (line) => ({ title: `Откатить правку от ${line.when}?`, hint: ROLLBACK_HINT, confirmText: "Откатить" });
