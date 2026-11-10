-- Выпуски (releases): журнал версий, заметки миграций и смена умолчаний настроек без перетирания правок людей.

-- Журнал версий: обновление, автоматический возврат, откат, исходная точка (S2, S4, S5, S11)
CREATE TABLE IF NOT EXISTS ops.release (
    id           bigserial PRIMARY KEY,
    kind         text NOT NULL CHECK (kind IN ('update', 'auto_rollback', 'rollback', 'baseline')),
    from_version text,
    to_version   text NOT NULL,
    git_commit   text,
    migrations   text[] NOT NULL DEFAULT '{}',
    actor        text,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    ok           boolean,
    note         text
);
CREATE INDEX IF NOT EXISTS release_started_idx ON ops.release (started_at DESC);

-- Что миграция сочла нужным сказать в итоге обновления: «настройка X оставлена как есть» (S8)
CREATE TABLE IF NOT EXISTS ops.release_notice (
    id         bigserial PRIMARY KEY,
    migration  text NOT NULL,
    setting    text,
    message    text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Новое умолчание настройки (S8). Меняет значение, только если его никто не трогал: оно равно прежнему
-- умолчанию и портал не записывал правку человека в agent_change. Иначе — заметка в итог обновления.
-- Ключа нет — вставка. Уже равно новому — ничего. Возвращает true, если значение записано.
CREATE OR REPLACE FUNCTION ops.change_default(p_migration text, p_key text, p_old jsonb, p_new jsonb,
                                              p_note text DEFAULT NULL) RETURNS boolean
LANGUAGE plpgsql AS $$
DECLARE
    cur jsonb;
    note text := 'выпуск ' || p_migration || coalesce(': ' || p_note, '');
BEGIN
    SELECT value INTO cur FROM settings WHERE key = p_key FOR UPDATE;
    IF NOT FOUND THEN
        INSERT INTO settings (key, value) VALUES (p_key, p_new);
        INSERT INTO agent_change (kind, before, after, applied_by, note)
        VALUES ('threshold', NULL, jsonb_build_object('setting', p_key, 'value', p_new), NULL, note);
        RETURN true;
    END IF;
    IF cur = p_new THEN
        RETURN false;
    END IF;
    IF cur = p_old AND NOT EXISTS (SELECT 1 FROM agent_change
                                   WHERE after->>'setting' = p_key AND applied_by IS NOT NULL) THEN
        UPDATE settings SET value = p_new, updated_at = now() WHERE key = p_key;
        INSERT INTO agent_change (kind, before, after, applied_by, note)
        VALUES ('threshold', jsonb_build_object('setting', p_key, 'value', cur),
                jsonb_build_object('setting', p_key, 'value', p_new), NULL, note);
        RETURN true;
    END IF;
    INSERT INTO ops.release_notice (migration, setting, message)
    VALUES (p_migration, p_key, 'настройка ' || p_key || ' оставлена как есть');
    RETURN false;
END;
$$;
