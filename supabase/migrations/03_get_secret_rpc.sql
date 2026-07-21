create or replace function public.get_secret(k text)
returns text language sql security definer
set search_path = private, public
as $$ select value from private.app_secrets where key = k; $$;
revoke execute on function public.get_secret(text) from public, anon, authenticated;
grant execute on function public.get_secret(text) to service_role;
