-- TaxnFin v2: esquema multiusuario para Syncfy (SAT + bancos)
-- profiles / syncfy_credentials / bank_accounts / bank_transactions / cfdis /
-- reconciliations / webhook_events + índices + RLS + trigger de registro.
-- (Aplicada como 20260716012953_taxnfin_core_schema)

create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  full_name text, rfc text, syncfy_user_id text unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create table public.syncfy_credentials (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  id_credential text not null unique, id_site text not null, site_name text,
  credential_type text not null check (credential_type in ('sat','bank','other')),
  status_code int, is_authorized boolean default false, last_refresh timestamptz,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table public.bank_accounts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  credential_id uuid references public.syncfy_credentials(id) on delete set null,
  id_account text not null unique, name text, account_number text, account_type text,
  currency text default 'MXN', balance numeric(18,2), site_name text,
  refreshed_at timestamptz, created_at timestamptz not null default now()
);
create table public.bank_transactions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  account_id uuid references public.bank_accounts(id) on delete cascade,
  id_transaction text not null unique, description text,
  amount numeric(18,2) not null, currency text default 'MXN',
  dt_transaction timestamptz not null, reference text, extra jsonb,
  created_at timestamptz not null default now()
);
create table public.cfdis (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  uuid_cfdi text not null,
  direction text not null check (direction in ('emitida','recibida')),
  tipo_comprobante text, version text,
  rfc_emisor text, nombre_emisor text, rfc_receptor text, nombre_receptor text,
  fecha_emision timestamptz, subtotal numeric(18,2), iva numeric(18,2),
  otros_impuestos numeric(18,2), total numeric(18,2), moneda text default 'MXN',
  metodo_pago text, forma_pago text, uso_cfdi text,
  estado text default 'vigente' check (estado in ('vigente','cancelado')),
  xml_raw text, data jsonb, created_at timestamptz not null default now(),
  unique (user_id, uuid_cfdi, direction)
);
create table public.reconciliations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  cfdi_id uuid not null references public.cfdis(id) on delete cascade,
  transaction_id uuid not null references public.bank_transactions(id) on delete cascade,
  matched_by text not null default 'auto' check (matched_by in ('auto','manual')),
  confidence numeric(4,3), created_at timestamptz not null default now(),
  unique (cfdi_id, transaction_id)
);
create table public.webhook_events (
  id uuid primary key default gen_random_uuid(),
  event_type text, id_credential text, payload jsonb,
  processed boolean not null default false, error text,
  created_at timestamptz not null default now()
);
create index idx_cfdis_user_fecha on public.cfdis (user_id, fecha_emision desc);
create index idx_cfdis_user_dir on public.cfdis (user_id, direction);
create index idx_tx_user_fecha on public.bank_transactions (user_id, dt_transaction desc);
create index idx_credentials_user on public.syncfy_credentials (user_id);
create index idx_webhook_processed on public.webhook_events (processed) where not processed;
alter table public.profiles enable row level security;
alter table public.syncfy_credentials enable row level security;
alter table public.bank_accounts enable row level security;
alter table public.bank_transactions enable row level security;
alter table public.cfdis enable row level security;
alter table public.reconciliations enable row level security;
alter table public.webhook_events enable row level security;
create policy "own profile" on public.profiles for all using (auth.uid() = id) with check (auth.uid() = id);
create policy "own credentials" on public.syncfy_credentials for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own accounts" on public.bank_accounts for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own transactions" on public.bank_transactions for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own cfdis" on public.cfdis for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own reconciliations" on public.reconciliations for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
-- webhook_events: solo service role
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, full_name)
  values (new.id, coalesce(new.raw_user_meta_data->>'full_name', ''));
  return new;
end; $$;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();
