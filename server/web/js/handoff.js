// «Передать в отдел персонала» (S9, S10): окно выбора вакансии и комментария, бейдж статуса на месте кнопки.
// Кнопка — в карточке «Сегодня», строке «Базы», досье и «подходящих»; только с правом handoff.create (руководитель, директор).
// Всё из API выводится только текстом (h(), textContent).
import { h, mount, uid } from "./dom.js";
import {
  DEFAULT_MIN, NO_OPEN_TEXT, canSend, commentCounter, defaultVacancy, handoffBadge, optionList, pickMode, recipientText, sentToast,
} from "./handoff-model.js";
import { chip, personName } from "./people.js";
import { errorLine, field, sheet, toast } from "./ui.js";

/** Бейдж статуса: слово, а не только цвет; причина отказа — строкой под ним, текстом. */
export function statusBadge(handoff) {
  const badge = handoffBadge(handoff);
  return h("span.handoffstatus", chip(badge.text, badge.tone), badge.reason ? h("div.t2", { style: "margin-top:4px" }, badge.reason) : null);
}

/**
 * Окно «Передать: имя»: вакансия (радиокнопки, больше пяти — список; уже переданная неактивна), «Получит: …», «Чем понравился» со счётчиком.
 * Ошибки (404, 409 «уже передан», 422) — текстом в окне, введённое остаётся. Возвращает Promise<reply | null>.
 */
export function openHandoffSheet({ api, signal, person }) {
  const win = sheet({ title: `Передать: ${personName(person)}`, width: "540px",
    hint: "Отдел персонала получит человека, свяжется с ним и ведёт по воронке. Контакты вам не показываются." });
  const body = h("div", h("p.loading", "Загружаю вакансии…"));
  win.body.append(body);
  let result = null;
  win.show();

  const close = (label = "Закрыть") => h("div.sheetfoot.end", h("button.b", { type: "button", "data-tip": label, onClick: () => win.close("cancel") }, label));

  const draw = (options) => {
    const list = optionList(options);
    if (!list.length) {
      mount(body, h("p.t2", { style: "margin:0" }, NO_OPEN_TEXT), close());
      return;
    }
    const min = options && Number.isFinite(options.comment_min) ? options.comment_min : DEFAULT_MIN;
    let chosen = defaultVacancy(list);
    const group = uid("handoff");
    const recipient = h("p.hint", { style: "margin:8px 0 0", role: "status" });
    const status = errorLine();
    const counterId = uid("counter");
    const comment = field({ label: "Чем понравился", kind: "textarea", name: "comment", describedBy: counterId, placeholder: "Что в резюме подходит под вакансию: опыт, навыки, место работы." });
    comment.input.style.minHeight = "96px";
    const counter = h("span.counter", { id: counterId });
    const send = h("button.b.fill", { type: "submit", "data-tip": "handoff-send" }, "Передать");
    let busy = false;
    const update = () => {
      const state = commentCounter(comment.input.value, min);
      counter.textContent = state.text;
      counter.classList.toggle("bad", !state.ok);
      send.disabled = busy || !canSend({ vacancyId: chosen, comment: comment.input.value, min, list });
      recipient.textContent = recipientText(list.find((v) => v.id === chosen));
    };
    const choose = (id) => { chosen = id; update(); };

    let picker;
    if (pickMode(list.length) === "radios") {
      picker = h("fieldset.choices", h("legend", "Вакансия"), list.map((v) => h("label.choice", { style: v.disabled ? "opacity:.6;cursor:not-allowed" : null },
        h("input", { type: "radio", name: group, value: v.id, checked: v.id === chosen, disabled: v.disabled, onChange: () => choose(v.id) }),
        h("span", h("b", v.title), h("span.t2", [v.score !== null ? `балл ${v.score}` : null, v.disabled ? "уже передан" : null].filter(Boolean).join(" · ") || "балла по ней у человека пока нет")))));
    } else {
      const selectId = uid("vacancy");
      const select = h("select", { id: selectId, onChange: () => choose(Number(select.value)) },
        list.map((v) => h("option", { value: v.id, disabled: v.disabled, selected: v.id === chosen }, v.label)));
      picker = h("div.field", h("label", { for: selectId }, "Вакансия"), select);
    }

    const form = h("form", { novalidate: true, onSubmit: async (event) => {
      event.preventDefault();
      if (busy || !canSend({ vacancyId: chosen, comment: comment.input.value, min, list })) return;
      status.clear();
      busy = true;
      send.textContent = "Передаю…";
      update();
      try {
        result = await api.post("/api/handoff", { person_id: person.person_id, vacancy_id: chosen, comment: comment.input.value.trim() }, { signal });
        win.close("sent");
        toast(sentToast(result));
      } catch (error) {
        if (error && error.name === "AbortError") { win.close("cancel"); return; }
        status.show(error);   // 409 «Уже передан в эту вакансию» / «Вакансия не открыта», 422, 404
      } finally {
        busy = false;
        send.textContent = "Передать";
        if (win.el.isConnected) update();
      }
    } }, h("div.form", picker, recipient, comment.root, h("div", { style: "display:flex;justify-content:flex-end" }, counter)), status.el,
    h("div.sheetfoot.end", h("button.b", { type: "button", onClick: () => win.close("cancel") }, "Отмена"), send));
    comment.input.addEventListener("input", update);
    mount(body, form);
    update();
    const first = form.querySelector("input[type=radio]:checked, select");
    (first || comment.input).focus();
  };

  api.get("/api/handoff/options", { query: { person_id: person.person_id }, signal }).then(draw).catch((error) => {
    if (error && error.name === "AbortError") return;
    const status = errorLine();
    status.show(error);
    mount(body, status.el, close());
  });
  return win.closed.then((outcome) => (outcome === "sent" ? result : null));
}

// ------------------------------------------------------------------ кнопка и бейдж

let openCache = null;   // {at, count}: сколько у руководителя открытых вакансий (нужно, чтобы кнопка знала, есть ли куда передавать)

async function openVacancyCount(api, signal) {
  if (openCache && Date.now() - openCache.at < 60000) return openCache.count;
  try {
    const data = await api.get("/api/vacancies", { signal });
    openCache = { at: Date.now(), count: (data.open || []).length };
    return openCache.count;
  } catch (error) {
    if (error && error.name === "AbortError") throw error;
    return null;   // не знаем: кнопка остаётся рабочей, окно скажет само
  }
}

/**
 * Кнопка «Передать в отдел персонала» (short — «Передать» для строки таблицы). Нет права handoff.create — null.
 * Нет открытых вакансий — aria-disabled (остаётся в фокусе) и пояснение, связанное через aria-describedby.
 * existing — уже сделанная передача этого человека: вместо кнопки бейдж; после успешной передачи кнопка тоже заменяется бейджем.
 */
export function handoffControl({ api, signal, session, person, existing = null, short = false, variant = "fill", onSent = null }) {
  if (!session.can("handoff.create")) return null;
  const root = h("span.handoffctl", { style: "display:inline-flex;align-items:center;gap:8px;flex-wrap:wrap" });
  const showBadge = (handoff) => mount(root, statusBadge(handoff));
  if (existing) {
    showBadge(existing);
    return root;
  }
  const reasonId = uid("why");
  const button = h("button.b", { type: "button", class: variant, "data-tip": "handoff-open", "aria-label": short ? `Передать в отдел персонала: ${personName(person)}` : null }, short ? "Передать" : "Передать в отдел персонала");
  const why = h("span.hint", { id: reasonId, hidden: true }, NO_OPEN_TEXT);
  let blocked = false;
  button.addEventListener("click", async () => {
    if (blocked) return;
    button.disabled = true;
    try {
      const reply = await openHandoffSheet({ api, signal, person });
      if (reply) {
        openCache = null;
        showBadge({ state: "sent", created_at: new Date().toISOString(), recruiter: reply.recruiter });
        if (onSent) onSent(reply);
      }
    } finally {
      button.disabled = false;
    }
  });
  mount(root, button, why);
  openVacancyCount(api, signal).then((count) => {
    if (count === 0 && root.isConnected) {
      blocked = true;
      button.setAttribute("aria-disabled", "true");
      button.setAttribute("aria-describedby", reasonId);
      button.classList.add("inactive");
      why.hidden = false;
      button.addEventListener("click", () => toast(NO_OPEN_TEXT));
    }
  }).catch(() => {});
  return root;
}

/** Карта «человек → последняя передача» для списков (GET /api/handoffs): у кого уже есть статус, там бейдж вместо кнопки. */
export async function loadHandoffMap(api, signal) {
  try {
    const reply = await api.get("/api/handoffs", { signal });
    const map = new Map();
    for (const item of reply.items || []) {
      const old = map.get(item.person_id);
      if (!old || String(item.created_at) > String(old.created_at)) map.set(item.person_id, item);
    }
    return map;
  } catch (error) {
    if (error && error.name === "AbortError") throw error;
    return new Map();
  }
}
