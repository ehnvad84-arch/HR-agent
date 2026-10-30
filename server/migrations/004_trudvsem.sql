-- «Работа России»: еженедельный сбор резюме из открытых данных (S45, решение человека 2026-10-30).
-- Источник включается; в выдачу идут только резюме региона, обновлённые не раньше trudvsem_max_age_days.
INSERT INTO settings (key, value) VALUES ('trudvsem_max_age_days', '365') ON CONFLICT (key) DO NOTHING;

INSERT INTO agent_change (kind, before, after, note)
SELECT 'source', '{"trudvsem": false}', '{"trudvsem": true}',
       'миграция 004: «Работа России» — еженедельный сбор по Краснодарскому краю (решение человека 2026-10-30)'
WHERE EXISTS (SELECT 1 FROM source WHERE code = 'trudvsem' AND NOT is_active);
UPDATE source SET is_active = true WHERE code = 'trudvsem';
