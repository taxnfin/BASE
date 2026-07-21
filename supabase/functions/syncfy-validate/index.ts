import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "jsr:@supabase/supabase-js@2";

const SYNCFY = "https://sync.paybook.com/v1";

Deno.serve(async (req: Request) => {
  const url = new URL(req.url);
  const admin = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
  );
  const { data: setupKey } = await admin.rpc("get_secret", { k: "SETUP_KEY" });
  if (!setupKey || url.searchParams.get("k") !== setupKey) {
    return Response.json({ error: "forbidden" }, { status: 403 });
  }
  const { data: apiKey } = await admin.rpc("get_secret", { k: "SYNCFY_API_KEY" });
  const action = url.searchParams.get("action") ?? "status";

  const sfTok = async (token: string, path: string, init: RequestInit = {}) => {
    const r = await fetch(`${SYNCFY}${path}`, {
      ...init,
      headers: { ...(init.headers ?? {}), "Authorization": `Bearer ${token}` },
    });
    const b = await r.json().catch(() => null);
    return { status: r.status, data: b?.response ?? null, raw: b };
  };
  const getToken = async (idUser: string) => {
    const r = await fetch(`${SYNCFY}/sessions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ api_key: apiKey, id_user: idUser }) });
    const b = await r.json().catch(() => null);
    return b?.response?.token ?? null;
  };

  if (action === "user_creds") {
    const idUser = url.searchParams.get("id_user") ?? "";
    const token = await getToken(idUser);
    if (!token) return Response.json({ error: "no token" }, { status: 502 });
    const cr = await sfTok(token, "/credentials");
    const creds = (cr.data ?? []).map((c: any) => ({ id_credential: c.id_credential, id_site: c.id_site, code: c.code, is_authorized: c.is_authorized, can_sync: c.can_sync, ready_in: c.ready_in, dt_refresh: c.dt_refresh }));
    const out: Record<string, unknown> = { creds };
    const idCred = url.searchParams.get("id_credential");
    if (idCred) {
      const ac = await sfTok(token, `/accounts?id_credential=${idCred}`);
      out.n_accounts = Array.isArray(ac.data) ? ac.data.length : ac.raw;
      const tx = await sfTok(token, `/transactions?id_credential=${idCred}&limit=5`);
      out.n_tx = Array.isArray(tx.data) ? tx.data.length : tx.raw;
    }
    return Response.json(out);
  }

  if (action === "connect") {
    const idUser = url.searchParams.get("id_user") ?? "";
    const idSite = url.searchParams.get("id_site") ?? "";
    const endpoint = url.searchParams.get("endpoint") ?? "/credentials";
    let fields: Record<string, string> = { username: "test", password: "test" };
    const fRaw = url.searchParams.get("fields");
    if (fRaw) { try { fields = JSON.parse(fRaw); } catch (_) {} }
    if (!idUser || !idSite) return Response.json({ error: "id_user e id_site requeridos" }, { status: 400 });
    const token = await getToken(idUser);
    if (!token) return Response.json({ error: "no token" }, { status: 502 });
    const c = await fetch(`${SYNCFY}${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}` },
      body: JSON.stringify({ id_site: idSite, credentials: fields }),
    });
    const cb = await c.json().catch(() => null);
    return Response.json({ http: c.status, code: cb?.code, message: cb?.message ?? null, id_credential: cb?.response?.id_credential ?? null });
  }

  return Response.json({ ok: true });
});
