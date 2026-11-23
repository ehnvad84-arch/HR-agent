// Утверждение наборов этапов (S4) и справочник этапов (S1) без DOM: что изменил рекрутер, счётчик «Воронки», группы справочника.
import { plural } from "../format.js";
import { reportDate } from "./funnel-report-model.js";

export const APPROVE_HINT = "Набор начнёт действовать сразу: вакансия пойдёт по новым этапам, кандидаты останутся на своих. Действие записывается в журнал изменений.";
export const DECLINE_HINT = "Комментарий увидит рекрутер: по нему он поправит набор и отправит его снова.";
export const NOTHING_TO_APPROVE = "Наборов, которые ждут утверждения, нет.";

const title = (item) => item.label || item.title;

/** Число наборов, которые ждут утверждения, — для счётчика на вкладке «Воронка»; нет — пустая строка. */
export function pendingBadge(lists) {
  const all = [...((lists && lists.open) || []), ...((lists && lists.closed) || [])];
  const n = all.filter((v) => v.stages_pending).length;
  return n || "";
}

export const pendingCountText = (n) => `${n} ${plural(n, "набор", "набора", "наборов")}`;
export const zoneTitle = (n) => `Ждут утверждения — наборы этапов (${n})`;

/** Общая часть двух последовательностей: этапы не из неё — «перенесены» (каждая сдвинутая пара даёт один перенесённый этап). */
function stayed(a, b) {
  const L = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) L[i][j] = a[i] === b[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  }
  const keep = new Set();
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { keep.add(a[i]); i += 1; j += 1; } else if (L[i + 1][j] >= L[i][j + 1]) i += 1;
    else j += 1;
  }
  return keep;
}

/** Что изменилось между действующим набором и предложенным (по slot): «+ этап», «− этап», «перенесён», «подпись». */
export function diffSets(current, proposed) {
  if (!current || !proposed) return [];
  const before = new Map(current.map((i) => [i.slot, i]));
  const after = new Map(proposed.map((i) => [i.slot, i]));
  const out = [];
  for (const i of proposed) if (!before.has(i.slot)) out.push({ kind: "add", text: `+ ${title(i)}`, tone: "g" });
  for (const i of current) if (!after.has(i.slot)) out.push({ kind: "remove", text: `− ${title(i)}`, tone: "r" });
  const common = (list, other) => list.filter((i) => other.has(i.slot)).map((i) => i.slot);
  const keep = stayed(common(current, after), common(proposed, before));
  for (const i of proposed) if (before.has(i.slot) && !keep.has(i.slot)) out.push({ kind: "move", text: `перенесён: ${title(i)}`, tone: "" });
  for (const i of proposed) {
    const old = before.get(i.slot);
    if (old && (old.label || "") !== (i.label || "")) out.push({ kind: "label", text: `подпись: ${title(i)}`, tone: "" });
  }
  return out;
}

/** «Петрова А. · отправлен 24.11.2026». */
export function approvalLine(set) {
  const who = set.proposed_by_name || set.recruiter || "";
  const when = reportDate(set.proposed_at);
  return [who, when ? `отправлен ${when}` : ""].filter(Boolean).join(" · ");
}

export const decisionToast = (state) => (state === "approved" ? "Набор утверждён" : "Набор отклонён");

/** «в наборах 9 вакансий»; одна вакансия — «в наборах 1 вакансии» (родительный падеж после «в наборах»). */
export function usedText(n) {
  if (!n) return "ни в одном наборе";
  return `в наборах ${n} ${n % 10 === 1 && n % 100 !== 11 ? "вакансии" : "вакансий"}`;
}

/** Справочник (GET /api/stages): этапы по строкам отчёта; вывести из работы можно нестандартный действующий. */
export function libraryGroups(library) {
  return (library.rows || []).map((r) => ({
    code: r.code, title: r.title,
    items: (library.stages || []).filter((s) => s.report_row === r.code).map((s) => ({
      id: s.id, title: s.title, standard: Boolean(s.is_standard), active: Boolean(s.is_active), usedText: usedText(Number(s.vacancies) || 0),
      canRetire: !s.is_standard && Boolean(s.is_active),
    })),
  }));
}

/** Поля формы «Новый этап» → тело POST /api/stages или текст ошибки у поля. */
export function newStageForm({ title: name, report_row: row }) {
  const text = String(name || "").trim();
  if (!text) return { error: "Введите название этапа", field: "title" };
  if (!row) return { error: "Выберите строку отчёта: в ней этап будет считаться", field: "report_row" };
  return { body: { title: text, report_row: row } };
}
