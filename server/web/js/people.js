// Человек на экранах: имя или ярлык, фото или заглушка, значок балла. Общее для «Сегодня», «Досье», «Базы», воронки.
import { h, initials, mount } from "./dom.js";
import { scoreBand } from "./format.js";
import { hashFor } from "./router.js";

/** Имя, если hh его отдал, иначе ярлык «должность, город». */
export const personName = (person) => person.name || person.label || "Без имени";
export const personHref = (id) => hashFor("person", { id });

/** Значок «нет фото и нет имени» (П:917–921). */
export function silhouette() {
  return h("svg", { viewBox: "0 0 40 40", "aria-hidden": "true" },
    h("svg:rect", { width: 40, height: 40, fill: "var(--green-tint)" }),
    h("svg:circle", { cx: 20, cy: 15, r: 7, fill: "var(--green-ink)", opacity: ".85" }),
    h("svg:path", { d: "M6 40c0-8 6.5-13 14-13s14 5 14 13z", fill: "var(--green-ink)", opacity: ".85" }));
}

/**
 * Кружок с фото из резюме (S9): /api/person/<id>/photo. Нет фото или оно не загрузилось — инициалы, если есть имя,
 * иначе силуэт. person: {person_id, name, photo}. small — 28 px вместо 44; size — "md" 56 px, "lg" 96×120.
 */
export function face(person, { small = false, size = null } = {}) {
  // size: "lg" — портрет 96×120 у карточки сигнала, "md" — 56 px в таблице «Базы»
  const box = h("div.face", { class: size || (small ? "sm" : null) });
  const fallback = () => {
    box.title = "Фото к резюме не приложено";
    // у кандидата без имени в name лежит ярлык «должность, город»: инициалы из него бессмысленны
    const realName = person.name && person.name !== person.label ? person.name : "";
    mount(box, (realName && initials(realName)) || silhouette());
  };
  if (person.photo && person.person_id) {
    box.title = "Фото из резюме";
    mount(box, h("img", {
      src: `/api/person/${encodeURIComponent(person.person_id)}/photo`, alt: "Фото из резюме", loading: "lazy",
      onError: fallback,
    }));
  } else {
    fallback();
  }
  return box;
}

/** Значок балла «88 из 100» (П:945). */
export function scoreBadge(score) {
  const band = scoreBand(score);
  return h("div.badge-score", { class: band || null },
    h("b", typeof score === "number" ? score : "—"), h("span", "из 100"));
}

export const chip = (text, tone = "") => h("span.chip", { class: tone || null }, text);

const TEMPLATE_TIP = "Модель недоступна или кончился пакет";

/** Пометка «по шаблону» (S8): нейтральный значок с подсказкой при наведении и фокусе. */
export function templateChip(text = "разбор по шаблону") {
  return h("span.chip.tip", { tabindex: 0, "data-tip": TEMPLATE_TIP, title: TEMPLATE_TIP },
    text, h("span.sr-only", ` — ${TEMPLATE_TIP}`));
}
