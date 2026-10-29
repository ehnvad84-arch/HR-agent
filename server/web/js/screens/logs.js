// «Журнал и отчёт» (S32, S33, S40; П:395–424): журнал изменений системы с периодом и фильтром по виду,
// недельный отчёт («Сформировать отчёт сейчас», «Отправить на почту»), получатели отчёта — только чтение.
// Агент сам ничего не меняет: сюда попадает то, что уже сделали люди и ночные задачи. Всё из API выводится только текстом (h()).
import { h, mount } from "../dom.js";
import { chip } from "../people.js";
import { emptyCard, errorLine, field, mountAsync, sheet, toast } from "../ui.js";
import {
  PRESET_DAYS, RECIPIENTS_NOTE, defaultPeriod, describeChange, filterByKind, isoDay, kindCounts, periodError, periodFor,
  periodLabel, recipientsText, reportView,
} from "./logs-model.js";

// ------------------------------------------------------------------ строки

function logRow(line) {
  return h("div.li", { style: "cursor:default;align-items:flex-start" },
    h("div", { style: "flex:1;min-width:0" },
      h("div", { style: "font-weight:600" }, line.note || line.kindLabel),
      h("div.sub", `${line.when} · ${line.author}`),
      line.summary ? h("div.logsum", line.summary) : null,
      line.details.length ? h("details.logmore", h("summary", "Подробнее"), h("ul.loglines", line.details.map((text) => h("li", text)))) : null),
    chip(line.kindLabel));
}

const OPEN_UP_TO = 5;   // длинные группы отчёта свёрнуты: заголовок с числом виден, записи раскрываются по клику

function reportGroup(group) {
  return h("details.repgroup", { open: group.items.length <= OPEN_UP_TO },
    h("summary", { style: "font-weight:600;font-size:15px" }, `${group.title} · ${group.countText}`),
    h("ul.loglines", group.items.map((item) => h("li", `${item.when} · ${item.note} · ${item.author}`))));
}

// ------------------------------------------------------------------ получатели (S40)

/** Адреса из MAIL_TO; менять их на экране нельзя — только в .env на сервере. */
function recipientsSheet(api, signal) {
  const win = sheet({ title: "Получатели отчёта", width: "480px", hint: RECIPIENTS_NOTE });
  const body = h("div", h("p.loading", "Загружаю…"));
  win.body.append(body);
  win.show();
  const done = h("button.b", { type: "button", onClick: () => win.close("ok") }, "Понятно");
  api.get("/api/report/recipients", { signal }).then((reply) => {
    const list = reply.recipients || [];
    mount(body,
      list.length
        ? h("div.list", { style: "box-shadow:none" }, list.map((address) => h("div.li", { style: "cursor:default" }, h("div", { style: "font-weight:600" }, address))))
        : h("p.t2", { style: "margin:0" }, recipientsText(list)),
      h("div.sheetfoot.end", done));
    done.focus();
  }).catch((error) => {
    if (error && error.name === "AbortError") return;
    const status = errorLine();
    status.show(error);
    mount(body, status.el, h("div.sheetfoot.end", done));
  });
}

// ------------------------------------------------------------------ экран

export function render(box, ctx) {
  const { api, signal } = ctx;
  const now = new Date();
  const state = { period: defaultPeriod(now), kind: "all", items: [], profileTitles: {}, request: 0 };

  const loadJournal = (period) => api.get("/api/changelog", { query: { from: period.from, to: period.to }, signal });

  const load = async () => {
    const [journal, report] = await Promise.all([loadJournal(state.period), api.get("/api/report/weekly", { signal })]);
    // названия профилей нужны только для текста «вес в профиле …»; нет права или сбой — экран работает с кодами
    const profiles = await api.get("/api/profiles", { signal }).catch(() => null);
    return { items: journal.items || [], report, profiles: profiles && profiles.items };
  };

  const view = (data) => {
    state.items = data.items;
    state.profileTitles = Object.fromEntries((data.profiles || []).map((p) => [p.code, p.title]));

    // ---------------- отчёт за неделю (S33)
    const reportBox = h("div");
    const mailStatus = errorLine();
    const buildStatus = errorLine();
    const drawReport = (report) => {
      const info = reportView(report, new Date());
      const mailButton = h("button.b.blue", { type: "button", "data-tip": "report-mail", onClick: async (event) => {
        const button = event.currentTarget;
        mailStatus.clear();
        button.disabled = true;
        try {
          const reply = await api.post("/api/report/weekly/send", {}, { signal });
          drawReport(reply);
          reportBox.querySelector("button").focus();
          toast(`Отчёт отправлен: ${(reply.recipients || []).join(", ") || "получателям"}`);
        } catch (error) {
          if (error && error.name === "AbortError") return;
          mailStatus.show(error);   // 503 «почта не настроена», 502 «письмо не отправлено, отчёт сохранён»
          button.disabled = false;
          refreshReport();          // отчёт собран и сохранён до отправки: показываем его актуальным
        }
      } }, "Отправить на почту");
      mount(reportBox, h("div.card",
        h("div", { style: "display:flex;align-items:center;gap:10px;flex-wrap:wrap" }, h("div.t1", info.title), info.sent ? chip(info.sent, info.sentTone) : null),
        h("div.t2", info.summary),
        info.groups.length ? h("div", { style: "margin-top:12px" }, info.groups.map(reportGroup)) : null,
        h("div.btns", mailButton),
        mailStatus.el,
        h("div.hint", { style: "margin-top:8px" }, "Перед отправкой отчёт собирается заново за последние семь дней. В письме только цифры и ссылка на этот экран, без данных о людях.")));
    };
    const refreshReport = async () => {
      try {
        const fresh = await api.get("/api/report/weekly", { signal });
        if (!signal.aborted) drawReport(fresh);
      } catch (error) {
        if (!(error && error.name === "AbortError")) toast(error && error.message ? error.message : "Не удалось обновить отчёт");
      }
    };
    drawReport(data.report);

    const buildButton = h("button.b.fill", { type: "button", onClick: async (event) => {
      const button = event.currentTarget;
      buildStatus.clear();
      button.disabled = true;
      try {
        drawReport(await api.post("/api/report/weekly/build", {}, { signal }));
        toast("Отчёт сформирован");
        reportBox.scrollIntoView({ block: "nearest" });
      } catch (error) {
        if (error && error.name === "AbortError") return;
        buildStatus.show(error);
      } finally {
        button.disabled = false;
      }
    } }, "Сформировать отчёт сейчас");

    // ---------------- журнал (S32): период, вид, список
    const kindBox = h("div");
    const listBox = h("div.list");
    const heading = h("h2.sechead", "Журнал изменений");
    const periodStatus = errorLine();

    const drawList = () => {
      const shown = filterByKind(state.items, state.kind);
      const lines = shown.map((item) => describeChange(item, { now: new Date(), profileTitles: state.profileTitles }));
      if (!state.items.length) {
        mount(listBox, emptyCard("Изменений ещё не было"));
        listBox.classList.remove("list");
        return;
      }
      listBox.classList.add("list");
      mount(listBox, lines.map(logRow));
    };
    const drawKinds = () => {
      const counts = kindCounts(state.items);
      if (!counts.some((c) => c.kind === state.kind)) state.kind = "all";
      const buttons = counts.map((c) => h("button", { type: "button", "aria-pressed": c.kind === state.kind, "data-tip": "log-kind", onClick: () => {
        state.kind = c.kind;
        buttons.forEach((b, i) => b.setAttribute("aria-pressed", String(counts[i].kind === state.kind)));
        drawList();
      } }, `${c.label} ${c.count}`));
      mount(kindBox, state.items.length ? h("div.tagseg.wrap", { role: "group", "aria-label": "Вид изменения" }, buttons) : null);
    };

    const from = field({ label: "С", kind: "date", name: "log-from", value: state.period.from, tip: "log-from" });
    const to = field({ label: "По", kind: "date", name: "log-to", value: state.period.to, tip: "log-to" });
    from.input.setAttribute("max", isoDay(now));
    to.input.setAttribute("max", isoDay(now));
    const presetButtons = PRESET_DAYS.map((days) => h("button", { type: "button", "aria-pressed": false, "data-tip": "log-preset",
      onClick: () => apply(periodFor(days, new Date())) }, `${days} дней`));
    const markPresets = () => presetButtons.forEach((b, i) => {
      const preset = periodFor(PRESET_DAYS[i], new Date());
      b.setAttribute("aria-pressed", String(preset.from === state.period.from && preset.to === state.period.to));
    });

    const apply = async (period) => {
      from.input.value = period.from;
      to.input.value = period.to;
      await reload();
    };
    const reload = async () => {
      const period = { from: from.input.value, to: to.input.value };
      const problem = periodError(period.from, period.to);
      periodStatus.clear();
      if (problem) {
        periodStatus.show(problem);
        return;
      }
      state.period = period;
      markPresets();
      const ticket = ++state.request;
      listBox.setAttribute("aria-busy", "true");
      try {
        const reply = await loadJournal(period);
        if (ticket !== state.request || signal.aborted) return;
        state.items = reply.items || [];
        heading.textContent = `Журнал изменений: ${periodLabel(period.from, period.to, new Date())}`;
        drawKinds();
        drawList();
      } catch (error) {
        if (ticket !== state.request || (error && error.name === "AbortError")) return;
        periodStatus.show(error);
      } finally {
        if (ticket === state.request) listBox.removeAttribute("aria-busy");
      }
    };
    from.input.addEventListener("change", reload);
    to.input.addEventListener("change", reload);
    markPresets();
    heading.textContent = `Журнал изменений: ${periodLabel(state.period.from, state.period.to, now)}`;
    drawKinds();
    drawList();

    return [
      h("div.card", { style: "margin-bottom:14px" },
        h("div.t1", "Доступ и учётные записи"),
        h("div.t2", "Вход по паролю, одна учётная запись. Вход из 1С появится позже")),
      h("div.card", { style: "margin-bottom:14px" },
        h("div.t1", "Журнал изменений системы"),
        h("div.t2", "Пишется всё, что меняет поведение агента: правки профилей должностей, пороги и веса, настройки источников, решения по недельной сводке, изменения прав. У каждой записи — автор, время, что было и что стало."),
        h("div.btns",
          buildButton,
          h("button.b", { type: "button", onClick: () => recipientsSheet(api, signal) }, "Получатели")),
        buildStatus.el,
        h("div.t2", { style: "margin-top:10px" }, "Раз в неделю, по пятницам в 17:00, журнал уходит письмом на почту получателей отчёта: что изменилось в профилях, в настройках агентов и в системе, кто это сделал и на каком основании.")),
      h("h2.sechead", "Отчёт за неделю"),
      reportBox,
      heading,
      h("div.periodbar", from.root, to.root, h("div.tagseg", { role: "group", "aria-label": "Быстрый выбор периода" }, presetButtons)),
      periodStatus.el,
      kindBox,
      listBox,
    ];
  };

  return mountAsync(box, load, view, { signal });
}
