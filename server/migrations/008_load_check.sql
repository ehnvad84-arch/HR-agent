-- Проверка загрузок (manager-cabinet S20): пороги тревоги о загрузках резюме.
INSERT INTO settings (key, value) VALUES
  ('load_alarm_error_pct',    '20'),
  ('load_stuck_hours',        '3'),
  ('load_alarm_repeat_hours', '6')
ON CONFLICT (key) DO NOTHING;
CREATE INDEX IF NOT EXISTS notification_event_idx ON notification ((body->>'event'));
