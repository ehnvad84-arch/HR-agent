// Этапы вакансии без DOM (S2–S5): действующий набор по строкам отчёта, плашки «ждёт утверждения» и «отклонён».
// Редактор набора (предложить, отозвать, утвердить) — funnel-report T13; здесь — только чтение.
import { plural } from "../format.js";
import { reportDate } from "./funnel-report-model.js";

/** Пределы длины, как в API этапов (`hr/api/stages.py`): названия и подписи этапов, комментарии. */
export const TITLE_MAX = 80;
export const COMMENT_MAX = 2000;

export const PENDING_NOTE = "Новый набор ждёт утверждения у руководителя ОП. Пока вакансия идёт по действующему.";

/** Строки отчёта — как в ops.funnel_row (spec S1); порядок задаёт набор. */
export const ROW_TITLES = {
  selected: "Отобранные резюме", contact: "Первичный контакт", screening: "Пройденное первичное интервью", interview: "Интервью",
  meeting: "Собеседование", security: "Проверка СБ", offer: "Оффер", first_day: "Пришедшие в 1 день",
};

export const peopleText = (count) => (count ? `${count} чел.` : "—");

/** Набор v1 из 8 стандартных этапов — «стандартный» (S2). */
export const isStandardSet = (set) => Boolean(set) && set.version === 1 && (set.items || []).length > 0 && set.items.every((i) => i.is_standard);

export function setHeading(set) {
  if (!set) return "Действует";
  const n = (set.items || []).length;
  return `Действует (${isStandardSet(set) ? "стандартный" : `версия ${set.version}`}, ${n} ${plural(n, "этап", "этапа", "этапов")})`;
}

/** Этапы набора по строкам отчёта, в порядке набора; people — {slot: человек на этапе}. */
export function groupItems(items, people = {}) {
  const groups = [];
  for (const i of items || []) {
    let g = groups[groups.length - 1];
    if (!g || g.code !== i.report_row) {
      g = { code: i.report_row, title: ROW_TITLES[i.report_row] || String(i.report_row), items: [] };
      groups.push(g);
    }
    const count = Number((people || {})[i.slot]) || 0;
    g.items.push({ slot: i.slot, title: i.label || i.title, count, peopleText: peopleText(count), standard: Boolean(i.is_standard) });
  }
  return groups;
}

/** «Набор отклонён 24.11.2026, Смирнова Е.: «комментарий»» (S4). */
export function declinedNote(set) {
  const head = `Набор отклонён ${reportDate(set.decided_at)}`.trim();
  const who = set.decided_by_name ? `, ${set.decided_by_name}` : "";
  const why = set.decision_comment ? `: «${set.decision_comment}»` : "";
  return `${head}${who}${why}`;
}
