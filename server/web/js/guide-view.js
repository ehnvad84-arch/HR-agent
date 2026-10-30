// Блок «Как работает портал» на «Схеме» (S19): всегда раскрыт (решение человека 2026-10-30). Текст и таблица прав — guide.js.
// Что человеку доступно, решают его права из /api/me.permissions (session.can); подсвечивается его роль в таблице.
import { h } from "./dom.js";
import { CAN, CANNOT, RULES, SECTIONS, myRights, rightsTable } from "./guide.js";
import { ROLE_TITLES } from "./nav-model.js";

const list = (items) => h("ul.guidelist", items.map((text) => h("li", text)));

export function guideBlock(session) {
  const user = session.user;
  const open = true;   // описание видно сразу и каждый раз; свернуть можно щелчком по заголовку
  const table = rightsTable();
  const rights = myRights((permission) => session.can(permission));
  const section = (id) => SECTIONS.find((s) => s.id === id);

  const rolesTable = h("div.tablewrap", { style: "max-height:none;box-shadow:none;border:.5px solid var(--sep)" },
    h("table.guidetable", h("caption.sr-only", "Кто что видит и может по ролям"),
      h("thead", h("tr", h("th", { scope: "col" }, "Что"), table.columns.map((c) => h("th", { scope: "col", class: c.role === (user && user.role) ? "me" : null },
        c.title, c.role === (user && user.role) ? h("span.guideme", " · это вы") : null)))),
      h("tbody", table.rows.map((row) => h("tr", h("th", { scope: "row" }, row.title),
        row.cells.map((cell, i) => h("td", { class: [cell.yes ? "yes" : "no", table.columns[i].role === (user && user.role) ? "me" : null] }, cell.text)))))));

  const mine = h("div.guidemine",
    h("div", { style: "font-weight:650" }, user && ROLE_TITLES[user.role] ? `Ваша роль: ${ROLE_TITLES[user.role]}` : "Ваша роль не определена"),
    rights.length ? [h("div.t2", { style: "margin-top:4px" }, "Вам доступно:"), list(rights)] : h("div.t2", "Действий по людям у вас нет: только то, что описано в справке."));

  return h("details.guide", { open },
    h("summary", h("span.t1", "Как работает портал"), h("span.hint", { style: "margin-left:10px" }, "что делает, кто что может, правила")),
    h("div.guidebody",
      h("h3", section("what").title), section("what").paragraphs.map((text) => h("p", text)),
      h("h3", section("roles").title), mine, rolesTable,
      h("h3", section("rules").title), list(RULES),
      h("h3", section("can").title),
      h("div.split", h("div", h("div", { style: "font-weight:650" }, "Умеет"), list(CAN)), h("div", h("div", { style: "font-weight:650" }, "Не умеет"), list(CANNOT)))));
}
