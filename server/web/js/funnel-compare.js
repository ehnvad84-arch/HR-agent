// Сравнение рекрутеров по процентам этапов (S13): строки отчёта × «Отдел, рекрутеры»; в ячейке % и мелко «прошли из вошли».
// Без графиков и оценок «хорошо/плохо»; цифры только текстом. Имя рекрутера — ссылка на его отчёт.
import { h } from "./dom.js";
import { hashFor } from "./router.js";
import { compareView } from "./screens/funnel-overview-model.js";

const NOTE = "нет в наборе";

function body(cellView) {
  return [h("span.pct", cellView.pct), cellView.of ? h("span.of", cellView.of) : null, cellView.outOfSet ? h("span.of", NOTE) : null];
}

/** department — ответ GET /api/funnel/department; year — для ссылок на отчёты рекрутеров. */
export function compareTable(department, { year } = {}) {
  const view = compareView(department);
  const head = h("tr", h("th", { scope: "col" }, "Строка отчёта"), view.heads.map((c) => h("th.n", { scope: "col" },
    c.recruiterId === null ? c.title
      : h("a.link", { href: hashFor("funnel", {}, { ...(year ? { year } : {}), recruiter: c.recruiterId }),
        "aria-label": `Отчёт рекрутера: ${c.title}`, "data-tip": "Открыть отчёт этого рекрутера за выбранный год" }, c.title))));
  return h("div.tablewrap.funnelwrap",
    h("table.funnel.compare",
      h("caption.sr-only", "Сравнение рекрутеров: процент прошедших на каждом этапе воронки"),
      h("thead", head),
      h("tbody", view.rows.map((row) => h("tr", { dataset: { row: row.code } }, h("th", { scope: "row" }, row.title),
        row.cells.map((c) => h("td.n", { class: c.outOfSet ? "out" : null }, body(c)))))),
      h("tfoot", h("tr", h("th", { scope: "row", class: "total" }, "Конверсия воронки"),
        view.conversion.map((c) => h("td.n.total", body(c)))))));
}
