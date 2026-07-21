-- Portal de Auditoría: expedientes + solicitudes PBC + bucket privado de evidencia
create table if not exists public.audit_engagements (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  nombre text not null default 'Expediente sin nombre',
  descripcion text not null default '',
  anio int not null default extract(year from now()),
  tipo text not null default 'Auditoría externa',
  status text not null default 'activo',
  categorias text[] not null default array['General','Fiscal/SAT','Bancos','Nómina','CxC','CxP','Inventario','Contratos','Activos Fijos','Otro'],
  created_at timestamptz default now(), updated_at timestamptz default now()
);
create table if not exists public.audit_requests (
  id uuid primary key default gen_random_uuid(),
  engagement_id uuid not null references public.audit_engagements(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  categoria text not null default 'General',
  nombre text not null default '', descripcion text not null default '',
  prioridad text not null default 'media',
  status text not null default 'pendiente',   -- pendiente|enviada|en_revision|aceptada|rechazada
  asignado_a text not null default '', fecha_limite date,
  motivo_rechazo text not null default '',
  archivos jsonb not null default '[]',       -- [{nombre,path,bytes,subido}]
  created_at timestamptz default now(), updated_at timestamptz default now()
);
alter table public.audit_engagements enable row level security;
alter table public.audit_requests enable row level security;
create policy "aud_eng_all" on public.audit_engagements for all using (auth.uid()=user_id) with check (auth.uid()=user_id);
create policy "aud_req_all" on public.audit_requests for all using (auth.uid()=user_id) with check (auth.uid()=user_id);
insert into storage.buckets (id,name,public) values ('audit','audit',false) on conflict (id) do nothing;
create policy "audit_files_ins" on storage.objects for insert to authenticated with check (bucket_id='audit' and (storage.foldername(name))[1]=auth.uid()::text);
create policy "audit_files_sel" on storage.objects for select to authenticated using (bucket_id='audit' and (storage.foldername(name))[1]=auth.uid()::text);
create policy "audit_files_del" on storage.objects for delete to authenticated using (bucket_id='audit' and (storage.foldername(name))[1]=auth.uid()::text);
