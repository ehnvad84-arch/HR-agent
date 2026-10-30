// Классический скрипт в <head>: ставит сохранённую тему до первой отрисовки (без мигания).
(function () {
  try {
    var mode = localStorage.getItem("hrTheme");
    if (mode === "light" || mode === "dark") document.documentElement.setAttribute("data-theme", mode);
  } catch (e) { /* хранилище недоступно: тема по системе */ }
})();
