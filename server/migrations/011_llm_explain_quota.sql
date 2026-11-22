-- 011: свой суточный предел фоновых объяснений (explain_pending), чтобы утренний разбор пар не съедал
-- общий предел модели. Фон списывает и llm_explain, и общий llm_call: рекрутеру остаётся не меньше
-- llm_calls_per_day − llm_explain_per_day. Только добавляет (AD-8).

SELECT ops.change_default('011', 'llm_explain_per_day', NULL, '300',
                          'фоновых объяснений моделью в сутки, внутри общего llm_calls_per_day');
