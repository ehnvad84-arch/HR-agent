// Вкладки, шапка и границы кабинета по роли из /api/me (manager-cabinet, S4, S17) — без DOM.
// Показ и скрытие — по правам из /api/me.permissions; роль нужна только там, где права одинаковы (кабинет руководителя) и для подписей.
// Сервер проверяет права сам: здесь решаем только, что показывать.

export const ROLE_TITLES = { recruiter: "специалист отдела персонала", head_recruiter: "руководитель отдела персонала",
  hiring_manager: "руководитель подразделения", director: "директор", no_access: "без доступа" };
export const NO_ACCESS_TEXT = "Доступ не выдан, обратитесь в отдел персонала";
export const NO_UNIT_TEXT = "Подразделение не указано — обратитесь в отдел персонала";

const STAFF_TABS = ["today", "person", "base", "vacancies", "profiles", "learn", "logs", "funnel", "arch"];
const CABINET_TABS = ["today", "vacancies", "profiles", "base", "learn", "arch"];   // «Схема» — только справка (S19)
const CABINET_TITLES = { vacancies: "Мои вакансии", profiles: "Мои должности" };
const CABINET_ROLES = new Set(["hiring_manager", "director"]);
const HR_ROLES = new Set(["recruiter", "head_recruiter"]);

/**
 * {kind: "staff" | "cabinet" | "none", tabs: [имена вкладок по порядку], titles: {имя: подпись вместо обычной}}.
 * Специалист и руководитель отдела персонала — прежние вкладки, «Доступ» после «Журнала» — по праву access.manage.
 * Руководитель подразделения и директор — кабинет. Без роли — вкладок нет.
 */
export function navFor(user, can) {
  if (!user || user.role === "no_access") return { kind: "none", tabs: [], titles: {} };
  if (CABINET_ROLES.has(user.role)) return { kind: "cabinet", tabs: [...CABINET_TABS], titles: { ...CABINET_TITLES } };
  const tabs = [...STAFF_TABS];
  // «Доступ»: у руководителя отдела персонала целиком (access.manage); у специалиста урезанный — только «Выдать код» руководителям (invite.issue)
  if (can("access.manage") || can("invite.issue")) tabs.splice(tabs.indexOf("logs") + 1, 0, "access");
  return { kind: "staff", tabs, titles: {} };
}

/** Кабинет руководителя: руководитель подразделения и директор (без решений по людям, контактов и загрузки резюме). */
export const isCabinet = (user) => Boolean(user && CABINET_ROLES.has(user.role));

/** Какая вкладка подсвечена для адреса: у руководителя досье и воронка — без вкладки, подсвечивается список, откуда пришли. */
export function activeTab(routeName, nav) {
  if (nav.tabs.includes(routeName)) return routeName;
  if (nav.kind === "cabinet") return { person: "base", funnel: "vacancies" }[routeName] || null;
  return null;
}

/** Подпись в шапке: подразделение руководителя («… и вложенные») или «Вся компания» у директора; у специалиста подписи нет. */
export function headerLine(user, scope) {
  if (!user) return "";
  if (user.role === "director") return "Вся компания";
  if (user.role !== "hiring_manager" || (scope && scope.reason === "no_unit")) return "";
  const title = (scope && scope.unit_title) || user.org_unit || user.org_unit_title || "";
  return `${title || "Своё подразделение"} и вложенные`;
}

/** S17: у руководителя нет подразделения — кабинет пуст. */
export const noUnit = (user, scope) => Boolean(user && CABINET_ROLES.has(user.role) && scope && scope.reason === "no_unit");

/** Вся база и ход задач (S19): право base.all; пока сервер его не выдаёт — отдел персонала по роли. */
export function hasFullBase(user, can) {
  return can("base.all") || (Boolean(user) && HR_ROLES.has(user.role));
}
