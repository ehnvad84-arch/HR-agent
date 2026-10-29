// Реестр экранов. Вкладка = запись в SCREENS; порядок вкладок задаёт TAB_ORDER в router.js.
//
// Контракт экрана — функция render(box, ctx):
//   box  — <main id="view">, уже очищенный; экран заполняет его сам (h(), mount(), mountAsync() из ui.js);
//   ctx  — { route: {name, params, query}, params, query, signal, api, session, go(name, params, query), setBadge(name, text) }.
//          signal отменяется, когда человек ушёл с экрана: передавайте его в api.get(…, {signal}).
// perm — право из /api/me; без него экран заменяется на «Нет доступа к этому разделу» (сервер проверяет права сам).
// Исключение или отказ промиса из render() показываются карточкой ошибки с «Повторить».
//
// Чтобы сделать экран (T23–T31): создать screens/<имя>.js с функцией render и заменить ею placeholder ниже.
import { placeholder } from "./placeholder.js";
import { render as renderBase } from "./base.js";
import { render as renderPerson } from "./person.js";
import { render as renderToday } from "./today.js";

export const SCREENS = {
  today: { title: "Сегодня", perm: "base.read", render: renderToday },
  person: { title: "Досье", perm: "base.read", render: renderPerson },
  base: { title: "База", perm: "base.read", render: renderBase },
  vacancies: { title: "Вакансии", perm: "base.read", render: placeholder("Вакансии") },
  profiles: { title: "База должностей", perm: "base.read", render: placeholder("База должностей") },
  learn: { title: "Обучение агента", perm: "base.read", render: placeholder("Обучение агента") },
  logs: { title: "Журнал и отчёт", perm: "report.read", render: placeholder("Журнал и отчёт") },
  funnel: { title: "Воронка", perm: "base.read", render: placeholder("Воронка") },
  arch: { title: "Схема", perm: "base.read", render: placeholder("Схема") },
};
