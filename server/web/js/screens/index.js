// Реестр экранов. Вкладка = запись в SCREENS; порядок вкладок задаёт TAB_ORDER в router.js.
//
// Контракт экрана — функция render(box, ctx):
//   box  — <main id="view">, уже очищенный; экран заполняет его сам (h(), mount(), mountAsync() из ui.js);
//   ctx  — { route: {name, params, query}, params, query, signal, api, session, go(name, params, query, {replace}), setBadge(name, text) }.
//          signal отменяется, когда человек ушёл с экрана: передавайте его в api.get(…, {signal}).
// perm — право из /api/me; без него экран заменяется на «Нет доступа к этому разделу» (сервер проверяет права сам).
// Исключение или отказ промиса из render() показываются карточкой ошибки с «Повторить».
//
// Новый экран: создать screens/<имя>.js с функцией render и добавить запись в SCREENS (и маршрут в router.js).
import { render as renderAccess } from "./access.js";
import { render as renderBase } from "./base.js";
import { render as renderFunnel } from "./funnel.js";
import { render as renderLearn } from "./learn.js";
import { render as renderLogs } from "./logs.js";
import { render as renderPerson } from "./person.js";
import { render as renderProfiles } from "./profiles.js";
import { render as renderSchema } from "./schema.js";
import { render as renderToday } from "./today.js";
import { render as renderVacancies } from "./vacancies.js";

export const SCREENS = {
  today: { title: "Сегодня", perm: "base.read", render: renderToday },
  person: { title: "Досье", perm: "base.read", render: renderPerson },
  base: { title: "База", perm: "base.read", render: renderBase },
  vacancies: { title: "Вакансии", perm: "base.read", render: renderVacancies },
  profiles: { title: "База должностей", perm: "base.read", render: renderProfiles },
  learn: { title: "Обучение агента", perm: "base.read", render: renderLearn },
  logs: { title: "Журнал и отчёт", perm: "report.read", render: renderLogs },
  access: { title: "Доступ", perm: "invite.issue", render: renderAccess },   // специалисту — только «Выдать код» (права внутри экрана)
  funnel: { title: "Воронка", perm: "base.read", render: renderFunnel },
  arch: { title: "Схема", perm: "base.read", render: renderSchema },
};
