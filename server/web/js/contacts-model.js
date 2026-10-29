// Контакт и письма без DOM (S16–S18): решение по окну платного контакта, подписи, итоги.

const HIDDEN_LABELS = { phones: "телефоны", email: "почта", other_contacts: "другие контакты" };
const BLOCKING = ["phones", "email", "other_contacts"];   // как в hr/contact_hh.py: скрыты все каналы — открывать нечего
const KIND_TITLES = { phone: "Телефон", email: "Почта", telegram: "Telegram", whatsapp: "WhatsApp", skype: "Skype", viber: "Viber" };
const LETTERS = new Set(["first_message", "follow_up", "invite_interview", "send_test_task", "reject_notice"]);
const NOT_WRITTEN_YET = new Set(["new", "picked_by_agent", "reserve"]);

export const COPY_FALLBACK = "Нажмите Ctrl+C";
const QUEUE_HINT = "Действие осталось в очереди подтверждений: его можно повторить позже.";

export const hiddenLabels = (ids) => (ids || []).map((id) => HIDDEN_LABELS[id] || id);
export const contactKindTitle = (kind) => KIND_TITLES[kind] || String(kind);
export const isLetter = (kind) => LETTERS.has(kind);

/** «Написать»: первое письмо — тому, кому ещё не писали, иначе напоминание. */
export const draftKind = (status) => (NOT_WRITTEN_YET.has(status) ? "first_message" : "follow_up");

/**
 * Что делать после prepare (или по payload действия из «Сегодня»):
 *   show    — контакт уже открыт, сразу показываем;
 *   blocked — hh скрыл все каналы, кнопки подтверждения нет;
 *   confirm — окно «Спишется один контакт hh» (willSpend: false, если hh уже отдаёт контакт без списания).
 */
export function contactPlan(prep) {
  const hidden = prep.hidden || [];
  const labels = hiddenLabels(hidden);
  if (prep.already_open) return { mode: "show", willSpend: false, hidden: labels };
  const blocked = prep.blocked ?? BLOCKING.every((id) => hidden.includes(id));
  if (blocked) return { mode: "blocked", willSpend: false, hidden: labels };
  return { mode: "confirm", willSpend: Boolean(prep.will_spend), hidden: labels };
}

export const confirmTexts = ({ willSpend }) => (willSpend
  ? { text: "Спишется один контакт hh. Действие запишется в журнал доступа.", button: "Открыть за 1 контакт" }
  : { text: "Контакт уже доступен: списания не будет. Действие запишется в журнал доступа.", button: "Открыть контакт" });

export const approvedText = (kind) => (kind === "first_message"
  ? "Письмо скопировано и одобрено. Статус кандидата — «Написали»"
  : "Одобрено: текст скопирован и сохранён в истории кандидата");

/** Занятость hh, лимит, сбой сети: действие осталось в очереди — человеку так и скажем. */
export function errorHint(error) {
  return error && [0, 409, 502, 503].includes(error.status) ? QUEUE_HINT : "";
}

/** Итог окна черновика: что написать на карточке и в уведомлении; resolved — действие ушло из очереди. */
export function actionOutcome(outcome, kind) {
  if (outcome === "approved") {
    return { card: approvedText(kind), toast: kind === "first_message" ? "Письмо одобрено, статус «Написали»" : "Письмо одобрено", resolved: true };
  }
  if (outcome === "declined") {
    return { card: "Черновик отклонён, причина записана", toast: "Черновик отклонён, причина записана", resolved: true };
  }
  return { card: null, toast: "Черновик остался в «Ждут подтверждения»", resolved: false };
}
