-- Ход фоновой задачи для полосы загрузки (S48) и порядок подразделений оргструктуры 1С (S47).
ALTER TABLE ops.job_run ADD COLUMN IF NOT EXISTS progress jsonb NOT NULL DEFAULT '{}';
-- порядок подразделений как в 1С: в схеме его нет, без него дерево не совпадёт с отчётом
ALTER TABLE org_unit ADD COLUMN IF NOT EXISTS sort_order integer;
