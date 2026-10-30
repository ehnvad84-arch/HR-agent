// Вход по одноразовому коду без DOM (S3): нормализация кода, проверка нового пароля до отправки, текст отказа.

export const MIN_PASSWORD = 8;
export const MAX_PASSWORD_BYTES = 72;   // предел bcrypt
export const BAD_CODE_TEXT = "Логин или код не подходят. Код мог истечь — попросите новый в отдел персонала";

/** Из кода убираются пробелы и дефисы (его диктуют и печатают группами); регистр не трогаем: сравнивает сервер. */
export const normalizeCode = (text) => String(text ?? "").replace(/[\s -]+/g, "");

export const passwordBytes = (text) => new TextEncoder().encode(String(text ?? "")).length;

/** null — можно отправлять; иначе {field: "password" | "repeat", text} — у какого поля показать ошибку. */
export function passwordProblem(password, repeat) {
  const text = String(password ?? "");
  if ([...text].length < MIN_PASSWORD) return { field: "password", text: "Не короче 8 знаков" };
  if (passwordBytes(text) > MAX_PASSWORD_BYTES) return { field: "password", text: "Слишком длинный пароль: не больше 72 байт" };
  if (text !== String(repeat ?? "")) return { field: "repeat", text: "Пароли не совпадают" };
  return null;
}
