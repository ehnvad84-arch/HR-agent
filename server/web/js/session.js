// Сессия в памяти страницы: кто вошёл, какие у него права, текущий токен CSRF.
// Cookie сессии браузер держит сам (HttpOnly), из скриптов её не видно; токен приходит из /api/login и /api/me
// и в хранилище браузера не записывается.

export function createSession() {
  let user = null;
  let permissions = new Set();
  let csrf = null;
  const subscribers = new Set();

  const notify = (previous) => subscribers.forEach((fn) => fn(user, previous));

  return {
    get user() { return user; },
    get csrf() { return csrf; },
    /** Есть ли у вошедшего право (проверку делает сервер, здесь только решаем, что показывать). */
    can(permission) { return permissions.has(permission); },
    /** Ответ /api/login или /api/me: {user, permissions, csrf}. */
    set(data) {
      const previous = user;
      user = data.user ?? null;
      permissions = new Set(data.permissions ?? []);
      csrf = data.csrf ?? null;
      notify(previous);
    },
    clear() {
      const previous = user;
      user = null;
      permissions = new Set();
      csrf = null;
      notify(previous);
    },
    /** fn(user, previousUser) — на каждый вход, обновление и выход. Возвращает функцию отписки. */
    subscribe(fn) {
      subscribers.add(fn);
      return () => subscribers.delete(fn);
    },
  };
}

export const session = createSession();
