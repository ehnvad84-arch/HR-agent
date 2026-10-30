-- Фича workplace, недельная сводка: порог похожести сообщений чата для одного предложения (S30).
-- Векторы — embed.vector; подбирается по живой ленте, как остальные пороги.
INSERT INTO settings (key, value) VALUES
  ('digest_similarity', '0.35')
ON CONFLICT (key) DO NOTHING;
