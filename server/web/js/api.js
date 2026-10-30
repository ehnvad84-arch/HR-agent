// Клиент API. Все ответы сервера — JSON; ошибка — {error, message}, message уже по-русски.
//   api.get(path, {query, signal, as})   api.post(path, body, {signal})   api.upload(path, formData)
// Что делает сам:
//  - изменяющие запросы идут с телом JSON и заголовком X-CSRF-Token;
//  - 401 → окно входа поверх экрана, после входа запрос уходит ещё раз (введённое в формах не теряется);
//  - 403 «csrf» (токен сменила другая вкладка) → новый токен из /api/me и один повтор;
//  - сбой сети и зависший сервер → ApiError со статусом 0 и понятным текстом (S35);
//  - отмена через AbortSignal пробрасывается как AbortError — экран, который ушёл, такие ошибки не показывает.
import { session as defaultSession } from "./session.js";

export const NETWORK_MESSAGE = "Сервер недоступен, проверьте подключение";
export const TIMEOUT_MESSAGE = "Сервер не отвечает, повторите позже";
const STATUS_MESSAGES = {
  400: "Некорректный запрос",
  401: "Войдите в рабочее место",
  403: "Нет прав на это действие",
  404: "Не найдено",
  409: "Действие сейчас недоступно",
  422: "Проверьте введённые данные",
  429: "Слишком много запросов, повторите позже",
  502: "Внешний сервис не отвечает, повторите позже",
  503: "Сервис временно недоступен, повторите позже",
};

export class ApiError extends Error {
  /**
   * status — HTTP-код (0: ответа не было), code — поле error из ответа (network, timeout, validation, csrf …),
   * fields — имена полей, не прошедших проверку (422), data — тело ответа как есть.
   */
  constructor({ status, code, message, fields = [], data = null }) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fields = fields;
    this.data = data;
  }
}

function toApiError(status, data) {
  const code = (data && typeof data.error === "string" && data.error) || `http_${status}`;
  let message = data && typeof data.message === "string" && data.message.trim() ? data.message : null;
  if (status === 403 && code !== "csrf") message = STATUS_MESSAGES[403];
  return new ApiError({
    status,
    code,
    message: message || STATUS_MESSAGES[status] || "Ошибка сервера, повторите позже",
    fields: data && Array.isArray(data.fields) ? data.fields : [],
    data,
  });
}

function queryString(query) {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

function filenameFrom(disposition) {
  if (!disposition) return null;
  const star = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(disposition);
  if (star) {
    try { return decodeURIComponent(star[1].trim()); } catch { /* берём обычное имя ниже */ }
  }
  const plain = /filename\s*=\s*"?([^";]+)"?/i.exec(disposition);
  return plain ? plain[1].trim() : null;
}

/**
 * fetchImpl, session и reauth подставляются в тестах.
 * reauth() — открыть окно входа и дождаться успешного входа (null: не повторять после 401).
 */
export function createClient({ fetchImpl, session = defaultSession, reauth = null, timeoutMs = 60000 } = {}) {
  const doFetch = fetchImpl || ((...args) => globalThis.fetch(...args));
  let reauthHook = reauth;
  let loginInFlight = null;
  let refreshInFlight = null;

  async function once(method, path, { query, body, form, signal, as = "json", timeoutMs: limit = timeoutMs } = {}) {
    const headers = { Accept: as === "json" ? "application/json" : "*/*" };
    if (session.csrf) headers["X-CSRF-Token"] = session.csrf;
    // Referrer-Policy сервера — no-referrer, а браузер не шлёт Origin с GET; серверу для GET …/export нужен Origin
    // или Referer своего хоста (CSRF), поэтому своему же серверу Referer разрешён. Чужим адресам этот клиент не пишет.
    const init = { method, headers, credentials: "same-origin", cache: "no-store", referrerPolicy: "same-origin" };
    if (form) {
      init.body = form;   // multipart: границу и Content-Type выставляет браузер
    } else if (method !== "GET") {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body ?? {});
    }

    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, limit);
    const onCallerAbort = () => controller.abort();
    if (signal) {
      if (signal.aborted) controller.abort();
      else signal.addEventListener("abort", onCallerAbort, { once: true });
    }
    init.signal = controller.signal;

    try {
      let response;
      let payload;
      try {
        response = await doFetch(path + queryString(query), init);
        const type = response.headers.get("content-type") || "";
        if (response.ok && as === "blob") {
          payload = { blob: await response.blob(), filename: filenameFrom(response.headers.get("content-disposition")) };
        } else if (type.includes("json")) {
          payload = await response.json().catch((error) => {
            if (signal && signal.aborted) throw error;   // экран ушёл посреди чтения тела: это отмена, а не пустой ответ
            return null;
          });
        } else {
          payload = null;
          if (response.ok) await response.arrayBuffer().catch(() => null);
        }
      } catch (error) {
        if (signal && signal.aborted) throw error;
        if (timedOut) throw new ApiError({ status: 0, code: "timeout", message: TIMEOUT_MESSAGE });
        throw new ApiError({ status: 0, code: "network", message: NETWORK_MESSAGE });
      }
      if (!response.ok) throw toApiError(response.status, payload);
      return payload;
    } finally {
      clearTimeout(timer);
      if (signal) signal.removeEventListener("abort", onCallerAbort);
    }
  }

  function askLogin() {
    if (!loginInFlight) {
      loginInFlight = Promise.resolve(reauthHook()).finally(() => { loginInFlight = null; });
    }
    return loginInFlight;
  }

  // токен хранится в базе хешем, поэтому каждый /api/me выдаёт новый; параллельные запросы делят один вызов
  function refreshCsrf() {
    if (!refreshInFlight) {
      refreshInFlight = once("GET", "/api/me").then((data) => session.set(data)).finally(() => { refreshInFlight = null; });
    }
    return refreshInFlight;
  }

  async function request(method, path, options = {}) {
    let askedLogin = false;
    let refreshedCsrf = false;
    for (;;) {
      const sentToken = session.csrf;
      try {
        return await once(method, path, options);
      } catch (error) {
        if (!(error instanceof ApiError)) throw error;
        if (error.status === 401 && reauthHook && !options.noAuth && !askedLogin) {
          askedLogin = true;
          await askLogin();
          continue;
        }
        if (error.status === 403 && error.code === "csrf" && !options.noCsrfRetry && !refreshedCsrf) {
          refreshedCsrf = true;
          if (session.csrf === sentToken) {
            try {
              await refreshCsrf();
            } catch (refreshError) {
              if (refreshError instanceof ApiError && refreshError.status === 401 && reauthHook && !options.noAuth && !askedLogin) {
                askedLogin = true;
                await askLogin();
              } else {
                throw refreshError;
              }
            }
          }
          continue;
        }
        throw error;
      }
    }
  }

  return {
    /** options: {query, signal, as: "json" | "blob", timeoutMs}. as "blob" → {blob, filename}. */
    get: (path, options) => request("GET", path, options),
    /** body — объект, уйдёт как JSON. */
    post: (path, body, options) => request("POST", path, { ...options, body }),
    /** form — FormData (загрузка файла ДИ). */
    upload: (path, form, options) => request("POST", path, { ...options, form }),
    /** Окно входа для 401: функция возвращает промис, который завершается после успешного входа. */
    setReauth(fn) { reauthHook = fn; },

    /** Кто я: новый токен CSRF и права. 401 здесь не открывает окно входа — вызывающий покажет страницу входа. */
    async me(options) {
      const data = await request("GET", "/api/me", { ...options, noAuth: true, noCsrfRetry: true });
      session.set(data);
      return data;
    },
    /** Неверный пароль приходит ошибкой 401 с текстом сервера и остаётся в форме. */
    async login(login, password, options) {
      const data = await request("POST", "/api/login", { ...options, body: { login, password }, noAuth: true, noCsrfRetry: true });
      session.set(data);
      return data;
    },
    /** Сессия уже закончилась (401) — это тоже выход. Сбой сети — ошибка: человек остаётся внутри. */
    async logout() {
      try {
        await request("POST", "/api/logout", { noAuth: true });
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 401) throw error;
      }
      session.clear();
    },
  };
}

export const api = createClient();
