-- NetaLog Supabase Schema
-- Run this in your Supabase project: Dashboard → SQL Editor → New Query

create table if not exists states (
  id              serial primary key,
  name            text not null,
  code            text not null unique,
  region          text,
  capital         text,
  population      bigint,
  last_election   integer,
  next_election   integer,
  ruling_party    text,
  party_seats     integer,
  total_seats     integer,
  in_power_since  integer
);

-- Add new columns to existing states table (safe to run on existing DB):
alter table states add column if not exists capital        text;
alter table states add column if not exists population     bigint;
alter table states add column if not exists last_election  integer;
alter table states add column if not exists next_election  integer;
alter table states add column if not exists ruling_party   text;
alter table states add column if not exists party_seats    integer;
alter table states add column if not exists total_seats    integer;
alter table states add column if not exists in_power_since integer;

create table if not exists politicians (
  id              serial primary key,
  name            text not null,
  party           text not null,
  state_id        integer references states(id),
  constituency    text,
  position        text,
  education       text,
  assets          numeric,
  liabilities     numeric,
  criminal_cases  integer default 0,
  age             integer,
  gender          text,
  image_url       text,
  myneta_url      text,
  created_at      timestamptz default now()
);

create table if not exists achievements (
  id              serial primary key,
  politician_id   integer references politicians(id) on delete cascade,
  title           text not null,
  description     text not null,
  source_url      text,
  published_date  date,
  category        text,
  created_at      timestamptz default now()
);

create table if not exists verified_politicians (
  id              integer primary key,
  name            text not null,
  party           text not null,
  state_id        integer references states(id),
  constituency    text,
  position        text,
  education       text,
  assets          numeric,
  liabilities     numeric,
  criminal_cases  integer default 0,
  age             integer,
  gender          text,
  image_url       text,
  myneta_url      text,
  created_at      timestamptz,
  verified_at     timestamptz default now()
);

-- Indexes for common filters
create index if not exists idx_politicians_state    on politicians(state_id);
create index if not exists idx_politicians_party    on politicians(party);
create index if not exists idx_achievements_pol     on achievements(politician_id);
create index if not exists idx_achievements_date    on achievements(published_date desc);
create index if not exists idx_vp_state             on verified_politicians(state_id);
create index if not exists idx_vp_party             on verified_politicians(party);
create index if not exists idx_vp_position          on verified_politicians(position);

-- Sample seed data
insert into states (name, code, region) values
  ('Andhra Pradesh',      'AP', 'South'),
  ('Arunachal Pradesh',   'AR', 'Northeast'),
  ('Assam',               'AS', 'Northeast'),
  ('Bihar',               'BR', 'East'),
  ('Chhattisgarh',        'CG', 'Central'),
  ('Goa',                 'GA', 'West'),
  ('Gujarat',             'GJ', 'West'),
  ('Haryana',             'HR', 'North'),
  ('Himachal Pradesh',    'HP', 'North'),
  ('Jharkhand',           'JH', 'East'),
  ('Karnataka',           'KA', 'South'),
  ('Kerala',              'KL', 'South'),
  ('Madhya Pradesh',      'MP', 'Central'),
  ('Maharashtra',         'MH', 'West'),
  ('Manipur',             'MN', 'Northeast'),
  ('Meghalaya',           'ML', 'Northeast'),
  ('Mizoram',             'MZ', 'Northeast'),
  ('Nagaland',            'NL', 'Northeast'),
  ('Odisha',              'OD', 'East'),
  ('Punjab',              'PB', 'North'),
  ('Rajasthan',           'RJ', 'North'),
  ('Sikkim',              'SK', 'Northeast'),
  ('Tamil Nadu',          'TN', 'South'),
  ('Telangana',           'TS', 'South'),
  ('Tripura',             'TR', 'Northeast'),
  ('Uttar Pradesh',       'UP', 'North'),
  ('Uttarakhand',         'UK', 'North'),
  ('West Bengal',         'WB', 'East'),
  ('Delhi',               'DL', 'North'),
  ('Jammu & Kashmir',     'JK', 'North')
on conflict (code) do nothing;
