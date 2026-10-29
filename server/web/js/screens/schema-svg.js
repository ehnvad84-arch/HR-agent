// Схема пути данных из прототипа (П:486–546): источники → конвейер → рекрутер, контур обучения, база в двух зонах.
// Блоки — данные, а не разметка из строк: SVG собирается через h(). Цифры в надписях — живые (counts из /api/schema).
import { h, uid } from "../dom.js";
import { profilesLabel, zonesTitle } from "./schema-model.js";

const FONT = "-apple-system, system-ui, Segoe UI, Roboto, Arial";
const LABEL = "Схема работы агента подбора: источники, сбор, сшивка досье, монитор изменений, оценка по профилям, очередь предложений, рекрутер, контур обучения и база на сервере компании";

/** Стрелки: линия из двух точек или ломаная по точкам. */
const ARROWS = [
  [[240, 86], [252, 86]], [[480, 86], [492, 86]], [[720, 86], [732, 86]],
  [[850, 128], [850, 152], [170, 152], [170, 198]],
  [[320, 240], [352, 240]], [[640, 240], [672, 240]],
  [[816, 282], [816, 330], [490, 330], [490, 356]],
];

function blocks(counts) {
  const c = counts || {};
  return [
    { x: 20, y: 46, w: 220, h: 82, fill: "var(--card2)", title: "Источники", lines: ["hh API · Работа России", "Авито · Хабр · соцсети"] },
    { x: 260, y: 46, w: 220, h: 82, fill: "var(--green-tint)", title: "1. Сбор, 06:30", lines: ["обход по профилям ролей", "ответ сохраняется целиком"] },
    { x: 500, y: 46, w: 220, h: 82, fill: "var(--green-tint)", title: "2. Сшивка досье", lines: ["резюме + соцсети + дубли", "один человек — одна карточка"] },
    { x: 740, y: 46, w: 220, h: 82, fill: "var(--green-tint)", title: "3. Монитор изменений", lines: ["версии, диффы, события", "сменил работу, открыл поиск"] },
    { x: 20, y: 198, w: 300, h: 84, fill: "var(--green-tint)", title: profilesLabel(c.profiles),
      lines: ["балл не ниже порога — карточка", "остаётся в базе и наблюдается дальше", "профили правят рекрутеры"] },
    { x: 352, y: 198, w: 288, h: 84, fill: "var(--green-tint)", title: "5. Очередь предложений",
      lines: ["письмо, приглашение,", "напоминание, склейка дубля —", "каждое ждёт подтверждения"] },
    { x: 672, y: 198, w: 288, h: 84, fill: "var(--card2)", title: "6. Рекрутер", lines: ["дайджест 08:30, экран", "«Сегодня», решение —", "всегда за человеком"] },
    { x: 200, y: 356, w: 580, h: 86, fill: "var(--violet-tint)", title: "7. Контур обучения", dx: 16,
      lines: ["чат рекрутеров · причины отказов · правки писем →", "недельная сводка и анкета руководителю отдела персонала →", "его решение меняет пороги, веса и профили"] },
    { x: 20, y: 462, w: 940, h: 76, fill: "var(--card)", stroke: "var(--green)", dash: "5 4", title: zonesTitle(c), dx: 14, ys: [24, 44, 64],
      lines: ["наблюдение: профили с площадок, соцсети, версии резюме, события, оценки — без контактов",
        "кандидат: ФИО, контакты, файл резюме, основание обработки, история статусов, журнал доступа"] },
  ];
}

export function schemeSvg(counts) {
  const marker = uid("arrow");
  const pad = (block) => block.dx ?? 12;
  // высота строк внутри блока — как в прототипе: у двухстрочных блоков одна сетка, у трёхстрочных другая
  const ys = (block) => block.ys || (block.lines.length > 2 ? [24, 44, 62, 78] : [22, 42, 60]);
  return h("svg", { viewBox: "0 28 980 524", role: "img", "aria-label": LABEL },
    h("svg:defs", h("svg:marker", { id: marker, markerWidth: 9, markerHeight: 9, refX: 8, refY: 3, orient: "auto" },
      h("svg:path", { d: "M0,0 L8,3 L0,6 z", fill: "currentColor" }))),
    h("svg:g", { fill: "none", stroke: "currentColor", "stroke-width": 1.6, color: "var(--muted)" },
      ARROWS.map((points) => h("svg:path", { d: points.map(([x, y], i) => `${i ? "L" : "M"}${x},${y}`).join(" "), "marker-end": `url(#${marker})` }))),
    h("svg:g", { "font-size": 12.5, "font-family": FONT, fill: "var(--ink)" }, blocks(counts).map((block) => [
      h("svg:rect", { x: block.x, y: block.y, width: block.w, height: block.h, rx: 14, fill: block.fill, stroke: block.stroke || "var(--sep)",
        "stroke-dasharray": block.dash }),
      h("svg:text", { x: block.x + pad(block), y: block.y + ys(block)[0], "font-weight": 650 }, block.title),
      block.lines.map((line, i) => h("svg:text", { x: block.x + pad(block), y: block.y + ys(block)[i + 1], fill: "var(--muted)" }, line)),
    ])));
}
