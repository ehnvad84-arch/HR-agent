// Справочник этапов (S1): этапы по 8 строкам отчёта; руководитель ОП (stage.approve) добавляет этап и выводит его из работы.
// Всё из API выводится только текстом.
import { h } from "../dom.js";
import { chip } from "../people.js";
import { confirmSheet, errorLine, field, mountAsync, sheet, toast } from "../ui.js";
import { libraryGroups, newStageForm } from "./stage-approvals-model.js";

const NEW_HINT = "Каждый этап считается в одной из 8 строк отчёта. Стандартные этапы не меняются.";
const DUPLICATE = "Такой этап уже есть в справочнике";

/** Окно «Новый этап»: название и строка отчёта. Ошибка сервера — под кнопкой. Promise<{saved, title}>. */
function newStageSheet({ api, signal, rows }) {
  const win = sheet({ title: "Новый этап", width: "480px" });
  const name = field({ label: "Название", name: "stage_title", required: true });
  const rowId = `row-${Math.random().toString(36).slice(2)}`;
  const select = h("select", { id: rowId, required: true, "aria-describedby": `${rowId}-hint` },
    h("option", { value: "" }, "Выберите строку…"), rows.map((r) => h("option", { value: r.code }, r.title)));
  const rowError = h("p.fielderr");
  const status = errorLine();
  const save = h("button.b.fill", { type: "submit" }, "Добавить");
  let saved = null;
  const form = h("form", { novalidate: true }, h("div.form", name.root,
    h("div.field", h("label", { for: rowId }, "Строка отчёта"), select,
      h("div.hint", { id: `${rowId}-hint` }, "В отчёте этап войдёт в эту строку"), rowError)),
  status.el, h("div.sheetfoot.end", h("button.b", { type: "button", onClick: () => win.close("cancel") }, "Отмена"), save));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    status.clear();
    name.error("");
    rowError.textContent = "";
    const checked = newStageForm({ title: name.input.value, report_row: select.value });
    if (checked.error) {
      if (checked.field === "title") name.error(checked.error); else rowError.textContent = checked.error;
      return;
    }
    save.disabled = true;
    try {
      await api.post("/api/stages", checked.body, { signal });
      saved = checked.body.title;
      win.close("saved");
    } catch (error) {
      if (error && error.name === "AbortError") { win.close("cancel"); return; }
      status.show(error && error.code === "duplicate" ? DUPLICATE : error);
      save.disabled = false;
    }
  });
  win.body.append(form);
  win.show(name.input);
  return win.closed.then((outcome) => ({ saved: outcome === "saved", title: saved }));
}

export function render(box, ctx) {
  const { api, signal, session } = ctx;
  const canApprove = session.can("stage.approve");
  const load = () => api.get("/api/stages", { signal });
  const reload = () => mountAsync(box, load, draw, { signal });

  const add = async (library) => {
    const result = await newStageSheet({ api, signal, rows: library.rows });
    if (!result.saved) return;
    toast(`Этап «${result.title}» добавлен`);
    await reload();
  };
  const retire = async (item, button) => {
    const agreed = await confirmSheet({ title: `Вывести из работы: ${item.title}?`, confirmText: "Вывести из работы",
      hint: "Этап пропадёт из списка для новых наборов. Действующие наборы и отметки не меняются." });
    if (!agreed) return;
    button.disabled = true;
    try {
      await api.post(`/api/stages/${encodeURIComponent(item.id)}/retire`, {}, { signal });
      toast(`Этап «${item.title}» выведен из работы`);
      await reload();
    } catch (error) {
      if (error && error.name === "AbortError") return;
      status.show(error);
      button.disabled = false;
    }
  };
  const status = errorLine();

  function draw(library) {
    const groups = libraryGroups(library);
    return [
      h("div.card", { style: "margin-bottom:12px" }, h("div.row",
        h("div.grow", h("h2.t1", "Справочник этапов"), h("div.hint", NEW_HINT)),
        h("div.btns", { style: "margin:0" },
          h("button.b", { type: "button", onClick: () => ctx.go("funnel") }, "К воронке"),
          canApprove ? h("button.b.fill", { type: "button", onClick: () => add(library) }, "Добавить этап") : null))),
      status.el,
      groups.map((g) => h("section", { "aria-label": g.title },
        h("h2.sechead", g.title),
        g.items.length ? h("div.list", g.items.map((i) => {
          const button = canApprove && i.canRetire
            ? h("button.b.sm", { type: "button", "aria-label": `Вывести из работы: ${i.title}`, style: "margin-top:0", onClick: (event) => retire(i, event.currentTarget) }, "Вывести из работы")
            : null;
          return h("div.li", { style: "cursor:default" }, h("div.grow", i.title),
            i.standard ? chip("стандартный") : null, i.active ? null : chip("выведен из работы", "o"),
            h("span.t2", i.usedText), button);
        })) : h("p.hint", { style: "margin:0 6px" }, "Этапов нет"))),
    ];
  }
  return mountAsync(box, load, draw, { signal });
}
