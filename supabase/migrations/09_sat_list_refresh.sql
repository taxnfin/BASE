create table if not exists public.sat_list_refresh (
  lista text primary key,
  status text not null default 'idle',   -- idle|running|done|error
  total integer, error text,
  started_at timestamptz, finished_at timestamptz
);
alter table public.sat_list_refresh enable row level security;
-- Solo service role (edge functions)
