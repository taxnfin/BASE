-- Listas negras del SAT (69 no localizados / 69-B EFOS): catálogo compartido
create table public.sat_rfc_lists (
  rfc text not null,
  lista text not null,          -- '69B' o '69'
  situacion text,               -- Definitivo, Presunto, Desvirtuado...
  fecha_carga date not null default current_date,
  primary key (rfc, lista)
);
create index idx_sat69_lista on public.sat_rfc_lists (lista);
alter table public.sat_rfc_lists enable row level security;
create policy "sat69 read" on public.sat_rfc_lists for select to authenticated using (true);
