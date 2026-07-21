-- Esquema privado para secretos (no expuesto por PostgREST)
create schema if not exists private;
create table private.app_secrets (
  key text primary key, value text not null,
  updated_at timestamptz not null default now()
);
revoke all on schema private from anon, authenticated;
revoke all on all tables in schema private from anon, authenticated;
-- Llaves guardadas: SYNCFY_API_KEY, SETUP_KEY, ANTHROPIC_API_KEY
