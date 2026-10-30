#!/usr/bin/env bash
# Автозапуск на время проб (T35, S43): база, портал и ежедневный сбор hh в 18:00 по Москве.
# Запускает человек один раз:  bash ~/projects/HR-agent/server/scripts/install_autostart.sh
# Службы — systemd пользователя (без root): ruware-pg, ruware-portal, таймеры ruware-hh-daily и ruware-trudvsem.
# Ключи читаются из server/.env и ~/.config/ruware-hr/session_key при старте службы, в файлы служб не копируются.
# Убрать всё:  systemctl --user disable --now ruware-pg ruware-portal ruware-hh-daily.timer ruware-trudvsem.timer \
#               ruware-load-watchdog.timer ruware-feedback-week.timer
set -euo pipefail

APP="$HOME/projects/HR-agent/server"
PG="$HOME/.local/pg16"
DATA="$HOME/.local/pg16-data"
UNITS="$HOME/.config/systemd/user"
DB="postgresql://postgres@127.0.0.1:55432/ruware_hr"
ENV_LOAD="set -a; . $APP/.env; set +a; export SESSION_KEY=\"\$\$(cat $HOME/.config/ruware-hr/session_key)\" DATABASE_URL=$DB"

mkdir -p "$UNITS"

cat > "$UNITS/ruware-pg.service" <<EOF
[Unit]
Description=Рувер: PostgreSQL 16 (127.0.0.1:55432)

[Service]
ExecStart=$PG/bin/postgres -D $DATA -p 55432 -c listen_addresses=127.0.0.1 -c unix_socket_directories=/tmp
Restart=on-failure

[Install]
WantedBy=default.target
EOF

cat > "$UNITS/ruware-portal.service" <<EOF
[Unit]
Description=Рувер: портал подбора (127.0.0.1:8000)
After=ruware-pg.service
Requires=ruware-pg.service

[Service]
WorkingDirectory=$APP
ExecStartPre=/bin/sleep 3
ExecStart=/bin/bash -c '$ENV_LOAD; exec .venv/bin/uvicorn hr.main:app --host 127.0.0.1 --port 8000'
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

cat > "$UNITS/ruware-hh-daily.service" <<EOF
[Unit]
Description=Рувер: сбор hh на остаток дневного лимита
After=ruware-pg.service

[Service]
Type=oneshot
WorkingDirectory=$APP
ExecStart=/bin/bash -c '$ENV_LOAD; exec .venv/bin/python -m hr.cli run collect_hh_daily'
EOF

cat > "$UNITS/ruware-hh-daily.timer" <<EOF
[Unit]
Description=Рувер: сбор hh каждый день в 18:00 по Москве

[Timer]
OnCalendar=*-*-* 18:00:00 Europe/Moscow
Persistent=true

[Install]
WantedBy=timers.target
EOF

cat > "$UNITS/ruware-trudvsem.service" <<EOF
[Unit]
Description=Рувер: «Работа России» — резюме Краснодарского края под портреты должностей
After=ruware-pg.service

[Service]
Type=oneshot
WorkingDirectory=$APP
ExecStart=/bin/bash -c '$ENV_LOAD; exec .venv/bin/python -m hr.cli run collect_trudvsem'
EOF

cat > "$UNITS/ruware-trudvsem.timer" <<EOF
[Unit]
Description=Рувер: «Работа России» в ночь с понедельника на вторник, 01:00 по Москве

[Timer]
OnCalendar=Tue *-*-* 01:00:00 Europe/Moscow
Persistent=true

[Install]
WantedBy=timers.target
EOF

cat > "$UNITS/ruware-load-watchdog.service" <<EOF
[Unit]
Description=Рувер: проверка загрузок — не начались по расписанию или зависли (S20)
After=ruware-pg.service

[Service]
Type=oneshot
WorkingDirectory=$APP
ExecStart=/bin/bash -c '$ENV_LOAD; exec .venv/bin/python -m hr.cli run load_watchdog'
EOF

cat > "$UNITS/ruware-load-watchdog.timer" <<EOF
[Unit]
Description=Рувер: проверка загрузок раз в час

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
EOF

cat > "$UNITS/ruware-feedback-week.service" <<EOF
[Unit]
Description=Рувер: недельная сводка всех замечаний — в Телеграм и на почту (S21)
After=ruware-pg.service

[Service]
Type=oneshot
WorkingDirectory=$APP
ExecStart=/bin/bash -c '$ENV_LOAD; exec .venv/bin/python -m hr.cli run feedback_week'
EOF

cat > "$UNITS/ruware-feedback-week.timer" <<EOF
[Unit]
Description=Рувер: сводка замечаний по пятницам в 18:30 по Москве

[Timer]
OnCalendar=Fri *-*-* 18:30:00 Europe/Moscow
Persistent=true

[Install]
WantedBy=timers.target
EOF

# то, что сейчас запущено вручную, уступает службам
"$PG/bin/pg_ctl" -D "$DATA" status >/dev/null 2>&1 && "$PG/bin/pg_ctl" -D "$DATA" stop -m fast || true
pkill -f "uvicorn hr.main:app --host 127.0.0.1 --port 8000" || true
sleep 2

systemctl --user daemon-reload
systemctl --user enable --now ruware-pg.service ruware-portal.service ruware-hh-daily.timer ruware-trudvsem.timer \
    ruware-load-watchdog.timer ruware-feedback-week.timer
# без входа в систему службы пользователя не живут после перезагрузки — включаем «linger»
loginctl enable-linger "$USER" || echo "Не удалось включить linger: выполните  sudo loginctl enable-linger $USER"

sleep 5
echo
systemctl --user --no-pager status ruware-pg.service ruware-portal.service | grep -E "●|Active:"
systemctl --user --no-pager list-timers ruware-hh-daily.timer ruware-trudvsem.timer ruware-load-watchdog.timer \
    ruware-feedback-week.timer | head -6
curl -s -o /dev/null -w "Портал отвечает: HTTP %{http_code}\n" http://127.0.0.1:8000/ || echo "Портал пока не отвечает — подождите 10 секунд и проверьте снова"
