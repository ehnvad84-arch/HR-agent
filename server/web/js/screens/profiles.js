// «База должностей» (S25–S28, S39; П:372–393, 1456–1585): список профилей с поиском и состояниями версий, редактор профиля,
// предпросмотр, утверждение, замена должностной инструкции, «Кто утверждает». Адрес редактора — #/profiles/<id>.
// Всё из API выводится только текстом (h(), textContent).
import { chipsInput } from "../chips.js";
import { h, mount, uid } from "../dom.js";
import { absoluteDay, plural } from "../format.js";
import { chip, personHref } from "../people.js";
import { hashFor } from "../router.js";
import { confirmSheet, emptyCard, errorCard, errorLine, field, mountAsync, sheet, toast } from "../ui.js";
import {
  FEATURES, GEO_OPTIONS, changesFor, docError, formFromProfile, groupByDept, previewText, roleTitle, rowSub, stateInfo,
  validateForm, weightSum,
} from "./profiles-model.js";

const DEBOUNCE_MS = 350;
let notice = null;   // {id, text}: итог прошлого действия, который показывает экран версии после перехода

// ------------------------------------------------------------------ окна

function newPositionSheet() {
  const win = sheet({ title: "Новая должность", width: "480px",
    hint: "Профиль новой должности с нуля здесь пока не заводится: это делается при обмене с 1С, вместе со справочником подразделений. "
      + "Сейчас можно править портрет существующей должности и заменять её должностную инструкцию." });
  const ok = h("button.b.fill", { type: "button", onClick: () => win.close("ok") }, "Понятно");
  win.body.append(h("div.sheetfoot.end", ok));
  win.show(ok);
}

/** «Кто утверждает» (S39): роли с правом profile.approve и люди с этими ролями; только чтение. */
function approversSheet(api, signal) {
  const win = sheet({ title: "Кто утверждает", width: "480px",
    hint: "Новую версию профиля утверждает роль с правом «утверждать профили». Права ролей меняются в системе, а не на экране; " +
      "до обмена с 1С список только для чтения." });
  const body = h("div", h("p.loading", "Загружаю…"));
  win.body.append(body);
  win.show();
  api.get("/api/approvers", { signal }).then((reply) => {
    mount(body,
      h("p.t2", { style: "margin:0 0 10px" }, `Роли: ${(reply.roles || []).map(roleTitle).join(", ") || "не назначены"}`),
      (reply.users || []).length ? h("div.list", { style: "box-shadow:none" }, reply.users.map((u) =>
        h("div.li", { style: "cursor:default" }, h("div", h("div", { style: "font-weight:600" }, u.full_name), h("div.sub", roleTitle(u.role))))))
        : h("p.t2", "Пользователей с этим правом нет."),
      h("div.sheetfoot.end", h("button.b", { type: "button", onClick: () => win.close("ok") }, "Закрыть")));
  }).catch((error) => {
    if (error && error.name === "AbortError") return;
    const status = errorLine();
    status.show(error);
    mount(body, status.el);
  });
}

/** «Заменить должностную инструкцию» (S28): файл .docx или .pdf и редакция → черновая версия профиля. */
function docSheet({ api, signal, profile, onDone }) {
  const win = sheet({ title: "Заменить должностную инструкцию", width: "520px",
    hint: `Профиль «${profile.title}». Из файла создаётся черновая версия профиля: её нужно отредактировать и отправить на утверждение, молча профиль не меняется.` });
  const fileId = uid("di");
  const file = h("input", { id: fileId, type: "file", accept: ".docx,.pdf", required: true });
  const revision = field({ label: "Редакция (необязательно)", name: "revision", placeholder: `ред. от ${new Date().toLocaleDateString("ru-RU")}` });
  const status = errorLine();
  const save = h("button.b.blue", { type: "submit" }, "Загрузить");
  const form = h("form", { novalidate: true }, h("div.form",
    h("div.field", h("label", { for: fileId }, "Файл инструкции (.docx или .pdf)"), file), revision.root),
  status.el, h("div.sheetfoot.end", h("button.b", { type: "button", onClick: () => win.close("cancel") }, "Отмена"), save));
  let created = null;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    status.clear();
    const chosen = file.files && file.files[0];
    const problem = docError(chosen);
    if (problem) {
      status.show(problem);
      return;
    }
    const data = new FormData();
    data.append("file", chosen);
    if (revision.input.value.trim()) data.append("revision", revision.input.value.trim());
    save.disabled = true;
    try {
      created = await api.upload(`/api/profile/${encodeURIComponent(profile.id)}/doc`, data, { signal });
      win.close("ok");
    } catch (error) {
      if (error && error.name === "AbortError") { win.close("cancel"); return; }
      status.show(error);   // 422 «нужен файл .docx или .pdf» и т. п.
      save.disabled = false;
    }
  });
  win.body.append(form);
  win.show(file);
  win.closed.then((outcome) => { if (outcome === "ok" && created) onDone(created); });
}

// ------------------------------------------------------------------ список

function listScreen(box, ctx) {
  const { api, signal } = ctx;
  let q = "";
  let dept = "";
  let total = null;
  let depts = new Map();
  let current = null;
  let timer = null;

  const heading = h("div.t1");
  const search = h("input.search", { id: "profile-q", type: "search", placeholder: "Поиск должности: электронщик, ОТК, продажи, бухгалтер…", autocomplete: "off",
    onInput: () => { clearTimeout(timer); timer = setTimeout(() => { q = search.value.trim(); load(); }, DEBOUNCE_MS); } });
  const deptSelect = h("select.search", { "aria-label": "Подразделение", style: "flex:0 0 240px", onChange: () => { dept = deptSelect.value; draw(); } });
  const found = h("span.hint", { role: "status" });
  const results = h("div");
  let items = [];

  const header = h("div.card",
    heading,
    h("div.t2", "Справочник должностей компании и портретов поиска по каждой. Изменившаяся должность правится: портрет любой должности правит любой рекрутер; "
      + "сохранение создаёт новую версию с вашим именем и уходит руководителю подбора на утверждение, до утверждения выдача считается по прежней версии."),
    h("div.btns",
      h("button.b.fill", { type: "button", onClick: () => newPositionSheet() }, "Новая должность"),
      h("button.b", { type: "button", onClick: () => ctx.go("logs") }, "Журнал изменений"),
      h("button.b.tint", { type: "button", onClick: () => approversSheet(api, signal) }, "Кто утверждает")));
  const tools = h("form.tools", { role: "search", style: "margin-top:14px", onSubmit: (e) => e.preventDefault() },
    h("label.sr-only", { for: "profile-q" }, "Поиск должности"), search, deptSelect, found);

  const draw = () => {
    const shown = dept ? items.filter((p) => p.dept_code === dept) : items;
    found.textContent = `${shown.length} из ${total ?? items.length} показано`;
    const count = total ?? items.length;
    heading.textContent = `База должностей: ${count} ${plural(count, "карточка", "карточки", "карточек")}`;
    mount(deptSelect, [h("option", { value: "" }, "Все подразделения"),
      ...[...depts.entries()].sort((a, b) => a[1].localeCompare(b[1], "ru")).map(([code, title]) => h("option", { value: code }, title))]);
    deptSelect.value = dept;
    if (!shown.length) {
      mount(results, emptyCard(q || dept ? "Ничего не найдено" : "Профилей пока нет"));
      return;
    }
    mount(results, groupByDept(shown).map((group) => h("div", { style: "margin-top:14px" },
      h("h2.sechead", group.dept),
      h("div.list", group.items.map((p) => {
        const info = stateInfo(p.state);
        return h("a.li", { href: hashFor("profiles", { id: p.id }) },
          h("div", h("div", { style: "font-weight:600" }, p.title), h("div.sub", rowSub(p))),
          p.pending_id ? chip("есть версия, ждёт утверждения", "o") : null,
          h("span.pill", { class: info.pill }, info.text), h("span.chev", "›"));
      })))));
  };

  async function load() {
    if (current) current.abort();
    const controller = new AbortController();
    current = controller;
    signal.addEventListener("abort", () => controller.abort(), { once: true });
    try {
      const reply = await api.get("/api/profiles", { query: { q }, signal: controller.signal });
      if (current !== controller) return;
      items = reply.items || [];
      if (!q) {   // полный список — основа для числа должностей и выбора подразделения
        total = items.length;
        depts = new Map(items.map((p) => [p.dept_code, p.dept]));
      }
      draw();
    } catch (error) {
      if (current !== controller || (error && error.name === "AbortError")) return;
      mount(results, errorCard(error, { onRetry: load }));
    }
  }

  mount(box, header, tools, results);
  load();
}

// ------------------------------------------------------------------ редактор версии

function editorScreen(box, ctx, id) {
  const { api, signal, session } = ctx;
  const canEdit = session.can("profile.edit");
  const canApprove = session.can("profile.approve");
  const pid = encodeURIComponent(id);

  const load = async () => {
    try {
      const [profile, list] = await Promise.all([api.get(`/api/profile/${pid}`, { signal }), api.get("/api/profiles", { signal })]);
      return { profile, list: list.items || [] };
    } catch (error) {
      if (error && error.status === 404) return null;
      throw error;
    }
  };

  const view = (data) => {
    if (!data) return emptyCard("Профиль не найден", h("a.link", { href: "#/profiles" }, "К списку должностей"));
    const { profile, list } = data;
    const info = stateInfo(profile.state);
    const sibling = list.find((p) => p.code === profile.code);
    const approvedRow = sibling && sibling.id !== profile.id ? sibling : null;   // действующая версия, если открыта не она
    const pendingId = sibling && sibling.id === profile.id ? sibling.pending_id : null;
    const status = errorLine();
    const saveStatus = errorLine();

    // ---------------- поля
    const title = field({ label: "Название должности", name: "title", value: profile.title, required: true });
    const form0 = formFromProfile(profile);
    const must = field({ label: "Обязательные требования (по строке на условие)", kind: "textarea", name: "must_have", value: form0.must_have });
    const nice = field({ label: "Желательно", kind: "textarea", name: "nice_to_have", value: form0.nice_to_have });
    const stop = field({ label: "Стоп-факторы", kind: "textarea", name: "stop_factors", value: form0.stop_factors });
    const keywords = chipsInput({ label: "Ключевые слова и синонимы, как они встречаются в резюме", values: form0.keywords, disabled: !canEdit,
      placeholder: "слово и Enter", hint: "Enter или запятая добавляют слово, × убирает." });
    const donors = chipsInput({ label: "Компании-доноры", values: form0.donor_companies, disabled: !canEdit, placeholder: "компания и Enter" });
    const geoId = uid("geo");
    const geo = h("select", { id: geoId }, GEO_OPTIONS.map(([code, label]) => h("option", { value: code }, label)));
    geo.value = form0.geo;
    const min = field({ label: "Вилка от, ₽", kind: "number", name: "salary_min", value: form0.salary_min });
    const max = field({ label: "Вилка до, ₽", kind: "number", name: "salary_max", value: form0.salary_max });
    for (const f of [min, max]) f.input.min = "0";
    const weightInputs = Object.fromEntries(FEATURES.map(([key, label]) => {
      const f = field({ label, kind: "number", name: `w_${key}`, value: String(form0.weights[key] ?? 0) });
      f.input.min = "0";
      f.input.max = "100";
      f.input.step = "1";
      return [key, f];
    }));
    const sum = h("p.weightsum", { role: "status" });
    const drawSum = () => {
      const total = weightSum(Object.fromEntries(FEATURES.map(([key]) => [key, weightInputs[key].input.value])));
      sum.textContent = `Сумма весов: ${total}. Сумма не обязана быть 100: сервер приводит веса к 100.`;
    };
    for (const f of Object.values(weightInputs)) f.input.addEventListener("input", drawSum);
    drawSum();
    const inputs = [title, must, nice, stop, min, max, ...Object.values(weightInputs)].map((f) => f.input);
    if (!canEdit) [...inputs, geo].forEach((el) => { el.disabled = true; });

    const readForm = () => ({
      title: title.input.value, geo: geo.value, salary_min: min.input.value, salary_max: max.input.value,
      keywords: keywords.values, donor_companies: donors.values,
      must_have: must.input.value, nice_to_have: nice.input.value, stop_factors: stop.input.value,
      weights: Object.fromEntries(FEATURES.map(([key]) => [key, weightInputs[key].input.value])),
    });

    // ---------------- действия
    const save = h("button.b.blue", { type: "button", onClick: async () => {
      saveStatus.clear();
      const form = readForm();
      const problem = validateForm(form);
      if (problem) {
        saveStatus.show(problem);
        return;
      }
      const changes = changesFor(profile, form);
      if (!Object.keys(changes).length) {
        saveStatus.show("Нет изменений: поправьте поля и сохраните");
        return;
      }
      save.disabled = true;
      try {
        const created = await api.post(`/api/profile/${pid}`, changes, { signal });
        notice = { id: String(created.id), text: `Сохранено версией ${created.version} и ждёт утверждения. До утверждения выдача считается по прежней версии; запись ушла в журнал изменений.` };
        toast(`Версия ${created.version} сохранена и ждёт утверждения`);
        ctx.go("profiles", { id: created.id });
      } catch (error) {
        if (error && error.name === "AbortError") return;
        saveStatus.show(error);   // 422: текст проверки; введённое остаётся
        save.disabled = false;
      }
    } }, "Сохранить как новую версию");

    const previewBox = h("div", { "aria-live": "polite" });
    const previewButton = h("button.b.fill", { type: "button", onClick: async () => {
      previewButton.disabled = true;
      mount(previewBox, h("p.loading", "Считаю предпросмотр…"));
      try {
        const p = await api.get(`/api/profile/${pid}/preview`, { signal });
        mount(previewBox, previewCard(p));
      } catch (error) {
        if (error && error.name === "AbortError") return;
        mount(previewBox, errorCard(error, { title: "Предпросмотр не получился" }));
      } finally {
        previewButton.disabled = false;
      }
    } }, "Предпросмотр изменений");

    const approve = h("button.b.fill", { type: "button", onClick: async () => {
      const agreed = await confirmSheet({ title: `Утвердить версию ${profile.version}?`, confirmText: "Утвердить",
        hint: "Версия начнёт действовать: выдача перейдёт на новые настройки, а вся база будет пересчитана в фоне. Действие записывается в журнал изменений." });
      if (!agreed) return;
      status.clear();
      approve.disabled = true;
      try {
        await api.post(`/api/profile/${pid}/approve`, {}, { signal });
        notice = { id: String(profile.id), text: `Версия ${profile.version} действует. База пересчитывается в фоне.`, link: true };
        toast("Версия действует, база пересчитывается в фоне");
        await refresh();
      } catch (error) {
        if (error && error.name === "AbortError") return;
        status.show(error);
        approve.disabled = false;
      }
    } }, "Утвердить");

    // ---------------- части экрана
    const mine = notice && notice.id === String(profile.id) ? notice : null;
    notice = null;
    const banners = [
      mine ? h("div.note", { role: "status" }, mine.text, mine.link ? [" ", h("a.link", { href: hashFor("arch") }, "Ход пересчёта — в «Схеме»")] : null) : null,
      profile.state !== "approved" ? h("div.note", { role: "note" },
        `Версия ${profile.version}${profile.state === "draft" ? " собрана из новой инструкции и" : ""} ждёт утверждения: выдача пока считается по `,
        approvedRow ? [`версии ${approvedRow.version} (`, h("a.link", { href: hashFor("profiles", { id: approvedRow.id }) }, "открыть"), ")"] : "прежней версии", ".") : null,
      pendingId ? h("div.note", { role: "note" }, "Есть версия, ждёт утверждения: ",
        h("a.link", { href: hashFor("profiles", { id: pendingId }) }, "открыть версию"), ".") : null,
    ].filter(Boolean);

    const diLine = h("div.fileline", h("div.ic", "DI"),
      h("div.grow", h("div", { style: "font-weight:600" }, profile.di_file || "Инструкция не загружена"),
        h("div.hint", [profile.di_revision ? (/^ред/i.test(profile.di_revision) ? profile.di_revision : `ред. ${profile.di_revision}`) : null, profile.di_uploaded_at ? `загружена ${absoluteDay(profile.di_uploaded_at)}` : null]
          .filter(Boolean).join(" · ") || "Данных о загрузке нет")),
      canEdit ? h("button.b.blue", { type: "button", onClick: () => docSheet({ api, signal, profile, onDone: (created) => {
        notice = { id: String(created.draft_version_id), text: "Инструкция загружена: создана черновая версия профиля. Отредактируйте её и отправьте на утверждение; до утверждения выдача считается по прежней версии." };
        toast("Инструкция загружена, создана черновая версия");
        ctx.go("profiles", { id: created.draft_version_id });
      } }) }, "Заменить должностную инструкцию") : null);

    return [
      h("p", { style: "margin:0 6px 10px" }, h("a.link", { href: "#/profiles" }, "← К списку должностей")),
      h("div.card", h("div.row", h("div.grow", h("h2.t1", profile.title),
        h("div.t2", `${profile.dept} · версия ${profile.version}`),
        h("div.chips", h("span.pill", { class: info.pill }, info.text))),
      ), h("div.t2", { style: "margin-top:10px" },
        "Правка портрета. Сохранение создаёт новую версию с вашим именем и уходит руководителю подбора на утверждение; до утверждения выдача считается по прежней версии."),
      banners.length ? h("div.stack", { style: "margin-top:12px" }, banners) : null,
      profile.state !== "approved" ? h("div.btns", canEdit ? previewButton : null, canApprove ? approve : null) : null,
      status.el, previewBox),
      h("div.card", { style: "margin-top:14px" }, h("div.t1", { style: "margin-bottom:10px" }, "Портрет должности"),
        h("div.form", title.root, must.root, h("div.two", nice.root, stop.root), keywords.root, donors.root,
          h("div.two", h("div.field", h("label", { for: geoId }, "География"), geo), h("div.two", min.root, max.root)),
          h("div", h("div.t1", { style: "font-size:15px;margin-bottom:8px" }, "Веса признаков"),
            h("div.weights", FEATURES.map(([key]) => weightInputs[key].root)), sum,
            form0.usingDefaultWeights ? h("p.hint", { style: "margin:4px 0 0" }, "Веса в профиле не заданы: показаны стандартные для подразделения. Если их не менять, они не отправляются.") : null)),
        canEdit ? h("div.btns", save) : h("p.hint", "У вас нет права править профили: поля только для чтения."),
        saveStatus.el,
        h("p.hint", { style: "margin:10px 0 0" }, "Выдача считается по прежней версии до утверждения.")),
      h("div.card", { style: "margin-top:14px" }, h("div.t1", { style: "margin-bottom:10px" }, "Должностная инструкция"), diLine,
        h("p.hint", { style: "margin:8px 0 0" },
          "Загрузили новую редакцию — создаётся черновая версия профиля, молча профиль не меняется: её нужно отредактировать и отправить на утверждение.")),
    ];
  };

  const refresh = async () => {
    try {
      const data = await load();
      if (signal.aborted) return;
      mount(box, view(data));
    } catch (error) {
      if (error && error.name === "AbortError") return;
      toast(error && error.message ? error.message : "Не удалось обновить");
    }
  };
  return mountAsync(box, load, view, { signal });
}

/** Предпросмотр (S26): сколько людей проходит порог сейчас и по новой версии, кто войдёт и кто выпадет. */
function previewCard(p) {
  const rows = (list, className) => (list.length ? h("div.list", { style: "box-shadow:none;margin-top:6px" }, list.map((row) =>
    h("a.li", { href: personHref(row.person_id) },
      h("div", h("div", { style: "font-weight:600" }, row.label || `Человек № ${row.person_id}`)),
      h("span.num", { class: className }, `${row.score_now} → ${row.score_new}`)))) : h("p.hint", { style: "margin:4px 0 0" }, "Никого."));
  return h("div.card.inset", { style: "margin-top:12px" }, h("div.t1", { style: "font-size:15px" }, previewText(p)),
    p.enter.length === 0 && p.leave.length === 0 ? h("p.hint", { style: "margin:6px 0 0" }, "Состав выдачи не изменится.") : null,
    h("div.t2", { style: "margin-top:10px;font-weight:600" }, `Войдут в выдачу: ${p.enter.length}`), rows(p.enter),
    h("div.t2", { style: "margin-top:10px;font-weight:600" }, `Выпадут из выдачи: ${p.leave.length}`), rows(p.leave),
    h("p.hint", { style: "margin:8px 0 0" }, "Показаны первые 50 человек в каждом списке. Изменения ещё не действуют."));
}

export function render(box, ctx) {
  const id = ctx.params.id;
  if (id) return editorScreen(box, ctx, id);
  return listScreen(box, ctx);
}
