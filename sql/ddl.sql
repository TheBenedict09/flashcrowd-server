CREATE DATABASE IF NOT EXISTS FLASHCROWD;
CREATE SCHEMA IF NOT EXISTS FLASHCROWD.CORE;
USE SCHEMA FLASHCROWD.CORE;
CREATE WAREHOUSE IF NOT EXISTS FC_WH WAREHOUSE_SIZE = XSMALL AUTO_SUSPEND = 60 AUTO_RESUME = TRUE;

CREATE TABLE IF NOT EXISTS EDITS (
  ts           TIMESTAMP_NTZ,   -- event time (UTC)
  wiki         STRING,
  title        STRING,          -- underscores
  namespace    INT,
  type         STRING,          -- edit | new
  user_name    STRING,
  is_bot       BOOLEAN,
  is_revert    BOOLEAN,         -- comment matches revert patterns
  delta_bytes  INT,
  comment      STRING
);

CREATE TABLE IF NOT EXISTS PAGEVIEWS_HOURLY (
  hour_utc      TIMESTAMP_NTZ,  -- start of the hour the views happened in
  wiki          STRING,
  title         STRING,
  views         INT,            -- desktop + mobile web summed
  published_at  TIMESTAMP_NTZ   -- Last-Modified of the dump file (≈ hour + 2h). THE no-look-ahead key.
);

CREATE TABLE IF NOT EXISTS MAIN_PAGE_EVENTS (
  ts     TIMESTAMP_NTZ,   -- when it became visible (TFA: 00:00 UTC of its day; ITN: revision timestamp)
  wiki   STRING,          -- enwiki only is fine
  title  STRING,
  slot   STRING           -- tfa | itn
);

CREATE TABLE IF NOT EXISTS COUNTRY_SHARE (
  day     DATE,
  wiki    STRING,
  title   STRING,
  region  STRING,         -- LDN | NJ | TYO (mapped from country)
  share   FLOAT
);

CREATE TABLE IF NOT EXISTS CAUSE_LABELS (   -- written by PC3's Gemini labeller
  ts        TIMESTAMP_NTZ,  -- decision time the label was produced for
  wiki      STRING,
  title     STRING,
  cause     STRING,         -- see enum in 3.4
  confidence FLOAT,
  reason    STRING
);

CREATE TABLE IF NOT EXISTS FORECASTS (        -- written by PC3 forecast job
  issued_at  TIMESTAMP_NTZ, wiki STRING, title STRING, region STRING,
  horizon_h  INT, p_surge FLOAT, size_q50 FLOAT, size_q90 FLOAT,
  cause STRING, top_reasons VARIANT
);

CREATE TABLE IF NOT EXISTS REGION_FORECASTS ( -- written by PC3, consumed by decision engine + API
  issued_at TIMESTAMP_NTZ, region STRING, target_hour TIMESTAMP_NTZ,
  q50_views FLOAT, q90_views FLOAT
);

CREATE TABLE IF NOT EXISTS INFRA_MEASUREMENTS (  -- written by PC1
  ts TIMESTAMP_NTZ, kind STRING,  -- boot | rtt | capacity
  region STRING, peer STRING, value FLOAT, unit STRING, note STRING
);
