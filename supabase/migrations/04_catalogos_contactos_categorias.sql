-- Contactos (clientes/proveedores con RFC, días de crédito, tasas IVA y retenciones)
create table public.contacts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  contact_type text not null default 'cliente' check (contact_type in ('cliente','proveedor','ambos')),
  nombre text not null, rfc text, regimen_fiscal text,
  dias_credito int default 0,
  iva_tasa numeric(5,4) default 0.16, ret_iva numeric(6,4) default 0, ret_isr numeric(6,4) default 0,
  email text, telefono text, direccion text, notas text,
  activo boolean not null default true,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  unique (user_id, rfc)
);
create index idx_contacts_user on public.contacts (user_id, contact_type);
-- Categorías (parent_id null = categoría; con parent = subcategoría)
create table public.categories (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  parent_id uuid references public.categories(id) on delete cascade,
  nombre text not null,
  flujo text not null default 'gasto' check (flujo in ('ingreso','gasto','ambos')),
  activo boolean not null default true, created_at timestamptz not null default now(),
  unique (user_id, parent_id, nombre)
);
create index idx_categories_user on public.categories (user_id, parent_id);
alter table public.cfdis
  add column if not exists ret_iva numeric(18,2),
  add column if not exists ret_isr numeric(18,2),
  add column if not exists contact_id uuid references public.contacts(id) on delete set null,
  add column if not exists category_id uuid references public.categories(id) on delete set null;
create index if not exists idx_cfdis_contact on public.cfdis (contact_id);
create index if not exists idx_cfdis_category on public.cfdis (category_id);
alter table public.contacts enable row level security;
alter table public.categories enable row level security;
create policy "own contacts" on public.contacts for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own categories" on public.categories for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
