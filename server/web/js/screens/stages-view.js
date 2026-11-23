// Вкладка «Этапы» вакансии (S2–S5): действующий набор по строкам отчёта и предложенный, плашки «ждёт утверждения» / «отклонён».
// Здесь чтение; редактор набора и кнопки «Отозвать», «Утвердить» — funnel-report T13. Всё из API — только текстом.
import { h } from "../dom.js";
import { chip } from "../people.js";
import { PENDING_NOTE, declinedNote, groupItems, isStandardSet, setHeading } from "./stages-model.js";

function column(heading, set, tone) {
  const groups = groupItems(set.items, set.people);
  return h("div",
    h("h2.sechead", heading, isStandardSet(set) ? chip("стандартный", tone) : null),
    groups.map((g) => h("section", { "aria-label": g.title },
      h("h3.sechead", g.title),
      h("div.list", g.items.map((i) => h("div.li", { style: "cursor:default" },
        h("div.grow", i.title), h("span.cnt.t2", i.peopleText)))))));
}

/** sets — ответ GET /api/vacancy/{id}/stage-sets: {current, proposed, declined}. */
export function stagesTab(sets) {
  const { current, proposed, declined } = sets;
  return [
    proposed ? h("div.note", { role: "note" }, PENDING_NOTE) : null,
    declined ? h("div.note.bad", { role: "note" }, declinedNote(declined)) : null,
    h("div.split",
      current ? column(setHeading(current), current, "") : h("div", h("h2.sechead", "Действует"), h("p.hint", "Набор этапов ещё не создан: вакансия идёт по 8 стандартным этапам.")),
      proposed ? column("Предлагается", proposed, "o") : null),
  ];
}
