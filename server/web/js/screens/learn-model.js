// «Обучение агента» без DOM (S29–S31, S41): теги и лента чата, счётчик сообщения, предложения недельной сводки.
// Тексты — из прототипа (TAGS, drawChat, drawDigest, applyDigest).
import { countChars } from "../decisions-model.js";
import { initials } from "../dom.js";
import { absoluteDay, plural, whenLabel } from "../format.js";
import { FEATURES } from "./profiles-model.js";

export const TAGS = [["remark", "замечание"], ["recommendation", "рекомендация"], ["criticism", "критика"], ["question", "вопрос"],
  ["reject_reason", "причина отказа"], ["postpone_reason", "причина «отложить»"]];
export const MIN_MESSAGE = 40;   // и settings.feedback_min_chars, и CHECK в схеме

export const tagLabel = (code) => (TAGS.find(([c]) => c === code) || [code, String(code ?? "")])[1];
export const canSend = (text) => countChars(text) >= MIN_MESSAGE;
export const counterText = (text) => `${countChars(text)} символов, нужно не меньше ${MIN_MESSAGE}`;

/** Лента для показа: по порядку времени, ответ агента сразу под сообщением; у ответа по шаблону — пометка. */
export function chatItems(feed) {
  const out = [];
  const ordered = [...(feed || [])].sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)) || a.id - b.id);
  for (const item of ordered) {
    out.push({ kind: "user", id: item.id, author: item.author, initials: initials(item.author), at: item.created_at, tag: item.tag,
      tagLabel: tagLabel(item.tag), text: item.body });
    if (item.reply) {
      out.push({ kind: "agent", id: item.id, author: "Агент", initials: "АГ", at: item.created_at, text: item.reply,
        byTemplate: item.reply_by === "template" });
    }
  }
  return out;
}

// ------------------------------------------------------------------ недельная сводка

function dateOnly(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value ?? ""));
  return match ? new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3])) : null;
}

/** «Неделя 22–26 октября»; через границу месяца — «Неделя 28 октября — 1 ноября». */
export function weekTitle(from, to, now = new Date()) {
  const a = dateOnly(from);
  const b = dateOnly(to);
  if (!a || !b) return "Неделя";
  if (a.getTime() === b.getTime()) return `Неделя ${absoluteDay(a, now)}`;
  if (a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth()) return `Неделя ${a.getDate()}–${absoluteDay(b, now)}`;
  return `Неделя ${absoluteDay(a, now)} — ${absoluteDay(b, now)}`;
}

const SETTING_TITLES = {
  watch_threshold: "порог наблюдения", daily_cards_limit: "число карточек в день", alert_score_new: "порог сигнала по новым людям",
  alert_score_pool_event: "порог сигнала по резерву", stalled_after_days: "дней до статуса «зависли»",
  reserve_touch_months: "месяцев до напоминания по резерву",
};

/** Что изменится, если принять: «порог наблюдения → 65», «вес «Открытый код» в профиле «…» → 12». */
export function targetText(target, profileTitles = {}) {
  if (target && target.setting) return `${SETTING_TITLES[target.setting] || target.setting} → ${target.value}`;
  if (target && target.profile_code) {
    const feature = (FEATURES.find(([key]) => key === target.feature) || [null, target.feature])[1];
    return `вес «${feature}» в профиле «${profileTitles[target.profile_code] || target.profile_code}» → ${target.value}`;
  }
  return "правка настроек не нужна: решение запишется в журнал";
}

export const canDecide = (decision) => decision === "pending" || decision === "discuss";

/** Решение по предложению: текст для карточки. Принятое применяется ночью задачей apply_digest. */
export function decisionInfo(proposal, now = new Date()) {
  const note = proposal.decision_note || "";
  const when = proposal.decided_at ? whenLabel(proposal.decided_at, now) : "";
  switch (proposal.decision) {
    case "accepted":
      return proposal.applied_at
        ? { state: "accepted", text: `Принято ${absoluteDay(proposal.decided_at, now)} и применено ${absoluteDay(proposal.applied_at, now)}`, tone: "g", note }
        : { state: "accepted", text: `Принято ${when}. Применится ночью (задача apply_digest)`, tone: "g", note };
    case "declined":
      return { state: "declined", text: `Отклонено ${when}. Изменений не будет`, tone: "r", note };
    case "discuss":
      return { state: "discuss", text: `Помечено «обсудить» ${when}. Решение пока не принято`, tone: "b", note };
    default:
      return { state: "pending", text: "Решение не принято", tone: "o", note: "" };
  }
}

export function proposalCounts(proposals) {
  const list = proposals || [];
  return {
    total: list.length,
    pending: list.filter((p) => canDecide(p.decision)).length,
    accepted: list.filter((p) => p.decision === "accepted").length,
    declined: list.filter((p) => p.decision === "declined").length,
  };
}

export function digestIntro(digest) {
  const count = (digest.proposals || []).length;
  if (!count) return null;
  const messages = digest.messages ?? 0;
  return `Собрано ${messages} ${plural(messages, "сообщение", "сообщения", "сообщений")}. Агент сгруппировал их и предлагает `
    + `${count} ${plural(count, "изменение", "изменения", "изменений")}. По каждому — вопрос анкеты и решение руководителя.`;
}
