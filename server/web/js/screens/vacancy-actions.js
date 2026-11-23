// Действия над вакансией, общие для «Вакансий» и экрана вакансии: закрыть (комментарий от 200 знаков) и пауза (S9, S14).
import { openCommentSheet } from "../decisions.js";
import { confirmSheet, toast } from "../ui.js";
import {
  CLOSE_HINT, CLOSE_REASONS, CLOSE_TOAST, PAUSE_HINT, PAUSE_TITLE, PAUSE_TOAST, RESUME_TOAST, closeResult,
} from "./vacancies-model.js";

const CLOSE_PLACEHOLDER = "Чем закрыта потребность, кто вышел на работу, что стоит учесть агенту в следующий раз по этой должности.";

/** Окно закрытия вакансии. Возвращает Promise<{saved, text}>: text — итог для карточки, toast показан. */
export async function closeVacancy({ api, signal, vacancy }) {
  const result = await openCommentSheet({
    title: `Закрыть вакансию: ${vacancy.title}`, hint: CLOSE_HINT, reasons: CLOSE_REASONS, saveLabel: "Закрыть вакансию",
    placeholder: CLOSE_PLACEHOLDER,
    send: (text) => api.post(`/api/vacancy/${encodeURIComponent(vacancy.id)}/close`, { comment: text }, { signal }),
  });
  if (!result.saved) return { saved: false };
  toast(CLOSE_TOAST);
  return { saved: true, text: closeResult(result.length) };
}

/**
 * Пауза: «Поставить на паузу» — с подтверждением, «Снять с паузы» — сразу. Ошибка — исключением (её покажет вызывающий).
 * Возвращает Promise<boolean>: состояние изменилось.
 */
export async function togglePause({ api, signal, vacancy }) {
  const id = encodeURIComponent(vacancy.id);
  if (vacancy.state === "on_hold") {
    await api.post(`/api/vacancy/${id}/resume`, {}, { signal });
    toast(RESUME_TOAST);
    return true;
  }
  const agreed = await confirmSheet({ title: PAUSE_TITLE, hint: PAUSE_HINT, confirmText: "Поставить на паузу" });
  if (!agreed) return false;
  await api.post(`/api/vacancy/${id}/pause`, {}, { signal });
  toast(PAUSE_TOAST);
  return true;
}
