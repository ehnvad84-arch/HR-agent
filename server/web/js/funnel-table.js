// Таблица воронки в форме отчёта ОП (S11): 8 строк «Вошли · Прошли · %», внизу — конверсия воронки.
// Общая для вкладки «Отчёт» вакансии и отчётов рекрутера и отдела (funnel-report T14): expand — раскрытие строки по этапам набора.
// Цифры — только текстом; цвет не единственный признак (строка вне набора подписана). Без графиков и оценок «хорошо/плохо».
import { h, uid } from "./dom.js";
import { captionText, conversionText, tableRows } from "./screens/funnel-report-model.js";

function numberCells(view) {
  return [h("td.n", view.entered), h("td.n", view.passed), h("td.n", view.pct)];
}

/** Строка отчёта; у строки с несколькими этапами набора — кнопка ▸/▾ (aria-expanded) и скрытые подстроки. */
function rowNodes(view) {
  const expandable = view.subs.length > 1;
  const panelIds = view.subs.map(() => uid("fr"));
  const title = h("span", view.title, view.note ? h("span.fnote", ` · ${view.note}`) : null);
  let button = null;
  const subRows = expandable ? view.subs.map((sub, i) => h("tr.sub", { id: panelIds[i], hidden: true, class: sub.outOfSet ? "out" : null },
    h("th", { scope: "row" }, sub.title), numberCells(sub))) : [];
  if (expandable) {
    button = h("button.fexp", { type: "button", "aria-expanded": "false", "aria-controls": panelIds.join(" "),
      "aria-label": `${view.title}: показать этапы набора`, "data-tip": "Показать этапы набора в этой строке отчёта",
      onClick: () => {
        const open = button.getAttribute("aria-expanded") !== "true";
        button.setAttribute("aria-expanded", String(open));
        button.firstChild.textContent = open ? "▾" : "▸";
        subRows.forEach((tr) => { tr.hidden = !open; });
      } }, h("span", { "aria-hidden": "true" }, "▸"), " ", title);
  }
  const first = h("th", { scope: "row" }, button || title);
  return [h("tr", { class: view.outOfSet ? "out" : null, dataset: { row: view.code } }, first, numberCells(view)), subRows];
}

/**
 * Таблица отчёта: report — ответ GET /api/vacancy/{id}/funnel-report (или `total` отчёта рекрутера/отдела, строки без `stages`).
 * title — для подписи скринридеру; expand=false — без раскрытия по этапам.
 */
export function funnelTable(report, { expand = true, title = "" } = {}) {
  const rows = tableRows(report, { expand });
  return h("div.tablewrap.funnelwrap",
    h("table.funnel",
      h("caption.sr-only", captionText(title)),
      h("thead", h("tr", h("th", { scope: "col" }, "Строка отчёта"), h("th.n", { scope: "col" }, "Вошли"),
        h("th.n", { scope: "col" }, "Прошли"), h("th.n", { scope: "col" }, "%"))),
      h("tbody", rows.map(rowNodes)),
      h("tfoot", h("tr", h("td.total", { colSpan: 4 }, conversionText(report.conversion)))))
  );
}
