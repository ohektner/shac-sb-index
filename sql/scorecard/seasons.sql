-- season_roles: which seasons are trained on, held out, or set aside (DuckDB).
-- Straight from docs/PREREGISTRATION.md, "Seasons". The one place this list lives.
--
--   train_only  never scored: there are too few seasons before it to fit on.
--   excluded    2020-21. Never trained on, never in the pooled numbers; scored in its own row.
--   held_out    scored, each one by a fit on the eligible seasons before it. Pooled together.
--
-- The live season (2027) is deliberately absent: live results are never mixed with backtests.

CREATE OR REPLACE VIEW season_roles AS
SELECT * FROM (
    VALUES
        (2019, 'train_only'),
        (2020, 'train_only'),
        (2021, 'excluded'),
        (2022, 'train_only'),
        (2023, 'held_out'),
        (2024, 'held_out'),
        (2025, 'held_out'),
        (2026, 'held_out')
) AS t(season, role);
