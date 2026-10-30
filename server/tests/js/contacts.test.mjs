// Контакт и письма: решение по окну платного контакта, подписи, выбор вида письма (S16–S18)
import assert from "node:assert/strict";
import test from "node:test";

import {
  actionOutcome, approvedText, canConfirm, confirmTexts, contactKindTitle, contactPlan, draftKind, errorHint, hiddenLabels, isLetter,
} from "../../web/js/contacts-model.js";

test("S16: контакт уже открыт — сразу показываем, без окна подтверждения", () => {
  assert.deepEqual(contactPlan({ already_open: true, hidden: [], will_spend: false }),
    { mode: "show", willSpend: false, hidden: [] });
});

test("S17: скрыты телефоны, почта и другие контакты вместе — открывать нечего, кнопки подтверждения нет", () => {
  const plan = contactPlan({ already_open: false, hidden: ["phones", "email", "other_contacts"], will_spend: false });
  assert.equal(plan.mode, "blocked");
  assert.deepEqual(plan.hidden, ["телефоны", "почта", "другие контакты"]);
});

test("S17: решение из карточки в «Сегодня» (флаг blocked в payload)", () => {
  assert.equal(contactPlan({ blocked: true, hidden: ["phones"], will_spend: false, already_open: false }).mode, "blocked");
  assert.equal(contactPlan({ blocked: false, hidden: [], will_spend: true, already_open: false }).mode, "confirm");
});

test("S16: часть каналов скрыта — окно подтверждения со списком скрытого", () => {
  const plan = contactPlan({ already_open: false, hidden: ["phones"], will_spend: true });
  assert.deepEqual(plan, { mode: "confirm", willSpend: true, hidden: ["телефоны"] });
});

test("контакт уже доступен hh без списания — окно говорит правду про списание", () => {
  const plan = contactPlan({ already_open: false, hidden: [], will_spend: false });
  assert.equal(plan.mode, "confirm");
  assert.equal(plan.willSpend, false);
  assert.deepEqual(confirmTexts(plan), {
    text: "Контакт уже доступен: списания не будет. Действие запишется в журнал доступа.", button: "Открыть контакт", consent: null });
  assert.deepEqual(confirmTexts({ willSpend: true }), {
    text: "Вы откроете телефон и почту кандидата. hh спишет один контакт из оплаченного пакета — вернуть его нельзя. "
      + "Открытие запишется в журнал доступа с вашим именем.",
    button: "Открыть за 1 контакт", consent: "Понимаю, что сейчас будет списан платный контакт hh" });
});

test("платный контакт: без галочки «Понимаю…» подтвердить нельзя; без списания галочка не нужна", () => {
  assert.equal(canConfirm({ willSpend: true }, false), false);
  assert.equal(canConfirm({ willSpend: true }, undefined), false);
  assert.equal(canConfirm({ willSpend: true }, true), true);
  assert.equal(canConfirm({ willSpend: false }, false), true);
  assert.equal(canConfirm({ willSpend: false }, true), true);
  assert.equal(confirmTexts({ willSpend: false }).consent, null);   // нет галочки — нечего отмечать
});

test("скрытые поля называются по-русски, незнакомое — как пришло", () => {
  assert.deepEqual(hiddenLabels(["email", "skype"]), ["почта", "skype"]);
  assert.deepEqual(hiddenLabels(null), []);
});

test("виды контактов", () => {
  assert.equal(contactKindTitle("phone"), "Телефон");
  assert.equal(contactKindTitle("email"), "Почта");
  assert.equal(contactKindTitle("telegram"), "Telegram");
  assert.equal(contactKindTitle("что-то"), "что-то");
});

test("письма — действия с текстом черновика; остальное в очереди — не письма", () => {
  for (const kind of ["first_message", "follow_up", "invite_interview", "send_test_task", "reject_notice"]) assert.equal(isLetter(kind), true);
  for (const kind of ["open_contact", "merge_persons", "move_to_reserve"]) assert.equal(isLetter(kind), false);
});

test("«Написать»: первое письмо тем, кому ещё не писали, напоминание — остальным", () => {
  for (const status of ["new", "picked_by_agent", "reserve"]) assert.equal(draftKind(status), "first_message");
  for (const status of ["first_message_sent", "replied", "phone_interview"]) assert.equal(draftKind(status), "follow_up");
});

test("итоги одобрения", () => {
  assert.equal(approvedText("first_message"), "Письмо скопировано и одобрено. Статус кандидата — «Написали»");
  assert.equal(approvedText("follow_up"), "Одобрено: текст скопирован и сохранён в истории кандидата");
});

test("ошибка сети, hh или занятости: действие осталось в очереди", () => {
  const hint = "Действие осталось в очереди подтверждений: его можно повторить позже.";
  assert.equal(errorHint({ status: 409 }), hint);
  assert.equal(errorHint({ status: 503 }), hint);
  assert.equal(errorHint({ status: 502 }), hint);
  assert.equal(errorHint({ status: 0 }), hint);
  assert.equal(errorHint({ status: 403 }), "");
  assert.equal(errorHint({ status: 422 }), "");
  assert.equal(errorHint(new Error("x")), "");
});

test("итог окна черновика для карточки и уведомления", () => {
  assert.deepEqual(actionOutcome("approved", "first_message"), {
    card: "Письмо скопировано и одобрено. Статус кандидата — «Написали»", toast: "Письмо одобрено, статус «Написали»", resolved: true });
  assert.deepEqual(actionOutcome("declined", "first_message"), {
    card: "Черновик отклонён, причина записана", toast: "Черновик отклонён, причина записана", resolved: true });
  assert.deepEqual(actionOutcome("later", "first_message"), {
    card: null, toast: "Черновик остался в «Ждут подтверждения»", resolved: false });
});
