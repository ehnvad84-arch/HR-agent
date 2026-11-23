-- 013 (funnel-report, ревью находка 5): встреча, назначенная отметкой «прошёл», привязана к открытому ею этапу —
-- отмена отметки снимает именно эту встречу из «Сегодня и завтра». Только добавляет (AD-8): v1.2.0 таблиц ops
-- воронки не читает.
ALTER TABLE ops.candidate_stage ADD COLUMN IF NOT EXISTS interview_id bigint REFERENCES interview(id) ON DELETE SET NULL;
