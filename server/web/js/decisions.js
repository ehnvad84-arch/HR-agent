// Окна решений по человеку: «Временно отложить» и «Категорически нет» (общее окно комментария), «Сохранить» (в кандидаты) с выбором вакансии (S11–S13).
// Используется на «Сегодня» и в «Досье». Всё из API выводится только текстом.
import {
  DEFAULT_MIN, HINTS, PLACEHOLDER, REASONS, TITLES, appendReason, canSave, counterText, countChars,
  minFromError, pickVacancies, profileOptions,
} from "./decisions-model.js";
import { h, uid } from "./dom.js";
import { plural } from "./format.js";
import { personName } from "./people.js";
import { confirmSheet, errorLine, field, sheet } from "./ui.js";

let minChars = DEFAULT_MIN;   // порог из ответа сервера (422 comment_too_short) запоминается для следующих окон

/**
 * Общее окно комментария (S13): подсказки-причины, счётчик знаков, «Сохранить решение» только с порога.
 * Используется для «Временно отложить», «Категорически нет» и закрытия вакансии. Ошибка сервера — под кнопками, окно не закрывается,
 * текст остаётся; порог из ответа 422 запоминается.
 *   send(text) — запрос на сервер (бросает ApiError); before — поля над подсказками (например выбор профиля).
 * Возвращает Promise<{saved, length}>.
 */
export function openCommentSheet({ title, hint, reasons, send, before = null, width = "560px", saveLabel = "Сохранить решение", placeholder = PLACEHOLDER }) {
  const win = sheet({ title, hint, width });
  const counterId = uid("counter");
  const comment = field({ label: "Комментарий", kind: "textarea", name: "comment", required: true, placeholder,
    describedBy: counterId, tip: "comment" });
  const counter = h("span.counter.bad", { id: counterId }, counterText("", minChars));
  const status = errorLine();
  const save = h("button.b.fill", { type: "submit", disabled: true, "data-tip": saveLabel }, saveLabel);

  let busy = false;
  const update = () => {
    const ready = canSave(comment.input.value, minChars);
    counter.textContent = counterText(comment.input.value, minChars);
    counter.classList.toggle("bad", !ready);
    save.disabled = busy || !ready;
  };

  const chips = h("div.reasons", reasons.map((reason) =>
    h("button", { type: "button", "data-tip": "reason-chip", onClick: () => {
      comment.input.value = appendReason(comment.input.value, reason);
      comment.input.focus();
      update();
    } }, `+ ${reason}`)));

  const form = h("form", { novalidate: true }, before, chips, comment.root,
    h("div.sheetfoot", counter,
      h("button.b", { type: "button", onClick: () => win.close("cancel") }, "Отмена"), save),
    status.el);
  comment.input.addEventListener("input", update);

  let outcome = { saved: false };
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = comment.input.value.trim();
    if (!canSave(text, minChars) || busy) return;
    status.clear();
    busy = true;
    update();
    try {
      await send(text);
      outcome = { saved: true, length: countChars(text) };
      win.close("saved");
    } catch (error) {
      if (error && error.name === "AbortError") { win.close("cancel"); return; }
      minChars = minFromError(error, minChars);   // сервер строже клиента: счётчик берёт его число
      status.show(error);
      busy = false;
      update();
    }
  });

  win.body.append(form);
  update();
  win.show(comment.input);
  return win.closed.then((result) => (result === "saved" ? outcome : { saved: false }));
}

/**
 * Окно «Временно отложить» / «Категорически нет» по человеку: общее окно комментария и выбор профиля.
 *   person: {person_id, name, label}; matches: [{role_profile_id, role_title, score}] — профили на выбор.
 * Возвращает Promise<{saved, length, roleProfileId}>.
 */
export async function openDecisionSheet({ api, signal, kind, person, matches }) {
  const options = profileOptions(matches, kind);
  const profileId = h("select", { id: uid("profile") }, options.map((o) => h("option", { value: o.id ?? "" }, o.label)));
  const profileField = options.length > 1
    ? h("div.field", h("label", { for: profileId.id }, "По какому профилю"), profileId) : null;
  const chosenProfile = () => {
    if (options.length === 0) return null;
    if (options.length === 1) return options[0].id;
    return profileId.value === "" ? null : Number(profileId.value);
  };
  let roleProfileId = null;
  const result = await openCommentSheet({
    title: `${TITLES[kind]}: ${personName(person)}`, hint: HINTS[kind], reasons: REASONS[kind], before: profileField,
    send: (text) => {
      roleProfileId = chosenProfile();
      return api.post("/api/decision", { person_id: person.person_id, kind, role_profile_id: roleProfileId, comment: text }, { signal });
    },
  });
  return result.saved ? { ...result, roleProfileId } : result;
}

// ------------------------------------------------------------------ «Сохранить» человека в кандидаты

function chooseVacancy(options, person) {
  const win = sheet({ title: `Сохранить: ${personName(person)}`, width: "520px",
    hint: "По профилю несколько открытых вакансий. Выберите, в чью воронку поставить человека: он встанет на этапе «Отобран агентом»." });
  const group = uid("vacancy");
  const radios = options.map((v, index) => {
    const input = h("input", { type: "radio", name: group, value: v.id, checked: index === 0 });
    const days = typeof v.open_days === "number" ? `открыта ${v.open_days} ${plural(v.open_days, "день", "дня", "дней")}` : null;
    return h("label.choice", input, h("span", h("b", v.title),
      h("span.t2", [days, v.headcount ? `ставок: ${v.headcount}` : null, `в работе: ${v.in_work ?? 0}`].filter(Boolean).join(" · "))));
  });
  const form = h("form", h("fieldset.choices", h("legend", "Вакансия"), radios),
    h("div.sheetfoot.end", h("button.b", { type: "button", onClick: () => win.close("cancel") }, "Отмена"),
      h("button.b.fill", { type: "submit", "data-tip": "take" }, "Сохранить")));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    win.close("ok");
  });
  win.body.append(form);
  win.show(radios[0].querySelector("input"));
  return win.closed.then((result) => {
    if (result !== "ok") return null;
    const checked = form.querySelector("input:checked");
    return options.find((v) => String(v.id) === checked.value) || null;
  });
}

/**
 * «Сохранить» в кандидаты (S11): вакансию определяет профиль человека. Одна подходящая — сразу, несколько — выбор в окне,
 * ни одной — подтверждение «в резерв без вакансии». Ошибки API уходят вызывающему.
 *   person: {person_id, name, label}; matches — если уже известны (в досье), иначе подгружаются.
 * Возвращает {taken, vacancy, candidateId}; отмена в окне — {taken: false}.
 */
export async function takePerson({ api, signal, person, matches }) {
  const id = encodeURIComponent(person.person_id);
  const [vacancies, found] = await Promise.all([
    api.get("/api/vacancies", { signal }),
    matches ? Promise.resolve(matches) : api.get(`/api/person/${id}`, { signal }).then((d) => d.matches),
  ]);
  const options = pickVacancies(vacancies.open, found);
  let vacancy = null;
  if (options.length === 1) {
    [vacancy] = options;
  } else if (options.length > 1) {
    vacancy = await chooseVacancy(options, person);
    if (!vacancy) return { taken: false };
  } else {
    const agreed = await confirmSheet({ title: `Сохранить: ${personName(person)}`, confirmText: "Сохранить", confirmTip: "take",
      hint: "По профилю человека нет открытой вакансии. Он станет кандидатом в резерве без вакансии — привязать к вакансии можно позже." });
    if (!agreed) return { taken: false };
  }
  const reply = await api.post(`/api/person/${id}/take`, vacancy ? { vacancy_id: vacancy.id } : {}, { signal });
  return { taken: true, vacancy, candidateId: reply.candidate_id };
}

