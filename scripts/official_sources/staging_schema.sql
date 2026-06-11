-- Official-source staging tables for NetaLog coverage probe.
-- Run this in Supabase Dashboard → SQL Editor BEFORE running the probe script.
-- These tables are append-only staging; they never replace verified_politicians.

create table if not exists official_source_runs (
  id               serial primary key,
  state_code       text        not null,
  run_date         timestamptz default now(),
  script_version   text,
  total_duration_s float,
  status           text,        -- 'complete' | 'partial' | 'error'
  summary_json     jsonb
);

create table if not exists official_politician_staging (
  id              serial primary key,
  run_id          integer references official_source_runs(id),
  state_code      text        not null,
  constituency    text,
  name            text,
  party           text,
  position        text,        -- 'MLA' | 'Cabinet Minister' | 'Chief Minister' | 'MP-LS' | 'MP-RS'
  age             integer,
  education       text,
  assets          numeric,
  liabilities     numeric,
  criminal_cases  integer,
  votes           integer,
  source_type     text,        -- 'ECI_RESULTS' | 'MYNETA_ECI_AFFIDAVIT' | 'STATE_GOV' | 'SANSAD' | 'WIKIPEDIA'
  source_url      text,
  confidence      text,        -- 'HIGH' | 'MEDIUM' | 'LOW'
  fetched_at      timestamptz default now(),
  raw_json        jsonb,
  missing_fields  text[]
);

create table if not exists official_source_coverage (
  id              serial primary key,
  run_id          integer references official_source_runs(id),
  state_code      text        not null,
  field_name      text        not null,
  total_expected  integer,
  total_found     integer,
  coverage_pct    float,
  source_type     text,
  notes           text,
  created_at      timestamptz default now()
);

create index if not exists idx_ops_state_run  on official_politician_staging(run_id, state_code);
create index if not exists idx_ops_source     on official_politician_staging(source_type);
create index if not exists idx_osc_run        on official_source_coverage(run_id, state_code);
