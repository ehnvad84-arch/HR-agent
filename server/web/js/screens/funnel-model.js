// «Воронка» без DOM (S24, S36): сводка по этапам, куда можно перевести, тело запроса перевода.
// Тексты — из прототипа (BOARD, stage, meter); итоговые статусы — как в задаче.

export const TERMINAL = [["hired", "Вышел на работу"], ["rejected_by_us", "Отказали мы"],
  ["rejected_by_candidate", "Отказался"], ["no_show", "Не пришёл"]];
const MEETING = new Set(["phone_interview", "manager_interview", "test_task"]);

export const meetingStage = (status) => MEETING.has(status);

/** Причина: у отказов обязательна, у «не пришёл» по желанию, у остальных этапов не нужна. */
export function reasonMode(status) {
  if (status === "rejected_by_us" || status === "rejected_by_candidate") return "required";
  return status === "no_show" ? "optional" : "none";
}

/** Перетаскивание сразу переводит, пока не нужны дата встречи и причина; иначе открывается окно. */
export const needsWindow = (status) => meetingStage(status) || reasonMode(status) !== "none";

export const funnelTotals = (stages) => (stages || []).reduce((sum, s) => sum + s.people.length, 0);

/** Строка этапа: «3 чел.», доля от всех в воронке (полоса), «в среднем 4 дн. на этапе» или «никого». */
export function stageSummary(stage, total) {
  const count = stage.people.length;
  return {
    count,
    countText: `${count} чел.`,
    pct: total > 0 ? Math.round((count / total) * 100) : 0,
    avgText: !count || stage.avg_days === null || stage.avg_days === undefined ? "никого"
      : stage.avg_days === 0 ? "в среднем меньше суток на этапе" : `в среднем ${stage.avg_days} дн. на этапе`,
    empty: count === 0,
  };
}

export function dayText(days) {
  if (typeof days !== "number") return "";
  return days === 0 ? "на этапе меньше суток" : `${days} дн. на этапе`;
}

/** Куда можно перевести: остальные этапы воронки и итоги. */
export function moveOptions(stages, current) {
  return {
    stages: (stages || []).filter((s) => s.status !== current).map((s) => ({ value: s.status, label: s.title })),
    final: TERMINAL.filter(([value]) => value !== current).map(([value, label]) => ({ value, label })),
  };
}

/** Выбор в окне по умолчанию: следующий этап; после последнего — первый итог. */
export function defaultTarget(stages, current) {
  const list = stages || [];
  const index = list.findIndex((s) => s.status === current);
  if (index < 0) return list.length ? list[0].status : TERMINAL[0][0];
  return index + 1 < list.length ? list[index + 1].status : TERMINAL[0][0];
}

/** «2026-10-30T11:00» (поле datetime-local) → «2026-10-30T11:00:00+03:00»: время встречи с часовым поясом браузера. */
export function localIso(value, offsetMinutes) {
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::(\d{2}))?$/.exec(String(value ?? ""));
  if (!match || Number.isNaN(new Date(value).getTime())) return "";
  const offset = offsetMinutes ?? new Date(value).getTimezoneOffset();   // минуты «позади» UTC: для +03:00 это −180
  const sign = offset <= 0 ? "+" : "-";
  const abs = Math.abs(offset);
  const pad = (n) => String(n).padStart(2, "0");
  return `${match[1]}T${match[2]}:${match[3] || "00"}${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
}

/** Поля окна → тело POST /api/candidate/{id}/status; ошибка заполнения — текстом. */
export function buildMove({ vacancyId, toStatus, comment = "", when = "", offsetMinutes }) {
  if (!toStatus) return { ok: false, error: "Выберите этап" };
  const text = String(comment ?? "").trim();
  const mode = reasonMode(toStatus);
  if (mode === "required" && !text) return { ok: false, error: "Опишите причину отказа" };
  const payload = { vacancy_id: vacancyId, to_status: toStatus };
  if (mode !== "none" && text) payload.comment = text;
  if (meetingStage(toStatus) && when) {
    const at = localIso(when, offsetMinutes);
    if (at) payload.scheduled_at = at;
  }
  return { ok: true, payload };
}

/** Без номера вакансии открываем первую открытую (на паузе — только если других нет). */
export function pickVacancy(open) {
  const list = open || [];
  return list.find((v) => v.state === "open") || list[0] || null;
}

/** Название этапа или итога по коду: «Телефонное интервью», «Вышел на работу». */
export function statusLabel(stages, status) {
  const stage = (stages || []).find((s) => s.status === status);
  if (stage) return stage.title;
  const final = TERMINAL.find(([code]) => code === status);
  return final ? final[1] : String(status ?? "");
}
