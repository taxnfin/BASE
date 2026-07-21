-- Grupo del flujo (ingreso/cogs/sga), categoría default por contacto,
-- estado de pago + aging en CFDIs, proyecciones no facturadas y tipos de cambio.
alter table public.categories
  add column if not exists grupo text not null default 'sga' check (grupo in ('ingreso','cogs','sga'));
update public.categories set grupo = 'ingreso' where flujo = 'ingreso';
alter table public.contacts
  add column if not exists category_id uuid references public.categories(id) on delete set null;
alter table public.cfdis
  add column if not exists fecha_pago_estimada date,
  add column if not exists fecha_pago date,
  add column if not exists status_pago text not null default 'pendiente' check (status_pago in ('pendiente','pagado','cancelado'));
update public.cfdis set status_pago = 'pagado', fecha_pago = fecha_emision::date
  where metodo_pago = 'PUE' and status_pago = 'pendiente';
update public.cfdis c set fecha_pago_estimada = (c.fecha_emision::date + coalesce(ct.dias_credito, 0))
  from public.contacts ct where c.contact_id = ct.id and c.fecha_pago_estimada is null;
update public.cfdis set fecha_pago_estimada = fecha_emision::date
  where fecha_pago_estimada is null and fecha_emision is not null;
create index if not exists idx_cfdis_pago on public.cfdis (user_id, status_pago, fecha_pago_estimada);
create table public.forecast_items (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  tipo text not null check (tipo in ('ingreso','gasto')),
  proyecto text, concepto text not null,
  category_id uuid references public.categories(id) on delete set null,
  monto numeric(18,2) not null, moneda text not null default 'MXN',
  fecha_estimada date not null, realizado boolean not null default false,
  notas text, created_at timestamptz not null default now()
);
create index idx_forecast_user on public.forecast_items (user_id, fecha_estimada);
alter table public.forecast_items enable row level security;
create policy "own forecast" on public.forecast_items for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
create table public.fx_rates (
  fecha date not null, moneda text not null,
  tasa_mxn numeric(12,6) not null, fuente text default 'DOF/Banxico',
  primary key (fecha, moneda)
);
alter table public.fx_rates enable row level security;
create policy "fx read" on public.fx_rates for select to authenticated using (true);
