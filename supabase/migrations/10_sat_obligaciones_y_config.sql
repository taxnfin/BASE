-- Seguimiento de declaraciones presentadas (Calendario SAT) + config en perfil
create table if not exists public.sat_obligaciones (
  user_id uuid not null references auth.users(id) on delete cascade,
  fecha date not null, obligacion text not null, periodo text not null default '',
  presentada boolean not null default false, fecha_presentacion date,
  fuente text not null default 'manual',   -- 'manual' | 'sat' (acuses Syncfy, futuro)
  updated_at timestamptz default now(),
  primary key (user_id, fecha, obligacion)
);
alter table public.sat_obligaciones enable row level security;
create policy "sat_oblig_select" on public.sat_obligaciones for select using (auth.uid() = user_id);
create policy "sat_oblig_insert" on public.sat_obligaciones for insert with check (auth.uid() = user_id);
create policy "sat_oblig_update" on public.sat_obligaciones for update using (auth.uid() = user_id);
alter table public.profiles add column if not exists cu numeric;               -- coeficiente de utilidad
alter table public.profiles add column if not exists umbral_tesoreria numeric; -- umbral de alerta
