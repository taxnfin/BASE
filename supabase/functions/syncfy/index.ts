import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "jsr:@supabase/supabase-js@2";
import { adminClient, getApiKey, getUserToken, sfTok, ensureSyncfyUser, syncCredential, SYNCFY } from "./lib.ts";

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function bufToB64(buf: Uint8Array): string {
  let bin = "";
  const CH = 0x8000;
  for (let i = 0; i < buf.length; i += CH) bin += String.fromCharCode(...buf.subarray(i, i + CH));
  return btoa(bin);
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });

  try {
    const userClient = createClient(
      Deno.env.get("SUPABASE_URL")!,
      Deno.env.get("SUPABASE_ANON_KEY")!,
      { global: { headers: { Authorization: req.headers.get("Authorization") ?? "" } } },
    );
    const { data: { user } } = await userClient.auth.getUser();
    if (!user) {
      return Response.json({ error: "unauthorized" }, { status: 401, headers: cors });
    }

    const admin = adminClient();
    const apiKey = await getApiKey(admin);
    if (!apiKey) return Response.json({ error: "missing api key" }, { status: 500, headers: cors });

    const body = await req.json().catch(() => ({}));
    const action = body?.action;

    if (action === "widget_token") {
      const idUser = await ensureSyncfyUser(admin, apiKey, user.id);
      if (!idUser) return Response.json({ error: "syncfy user failed" }, { status: 502, headers: cors });
      const token = await getUserToken(apiKey, idUser);
      if (!token) return Response.json({ error: "session failed" }, { status: 502, headers: cors });
      return Response.json({ token, id_user: idUser }, { headers: cors });
    }

    if (action === "credentials") {
      const idUser = await ensureSyncfyUser(admin, apiKey, user.id);
      if (!idUser) return Response.json({ error: "syncfy user failed" }, { status: 502, headers: cors });
      const token = await getUserToken(apiKey, idUser);
      if (!token) return Response.json({ error: "session failed" }, { status: 502, headers: cors });
      const r = await sfTok(token, "/credentials");
      return Response.json({ credentials: r.data ?? [] }, { headers: cors });
    }

    // Lista documentos SAT (constancia de situación fiscal, opinión de cumplimiento,
    // declaraciones, acuses) detectados entre los attachments de las credenciales.
    if (action === "documents") {
      const idUser = await ensureSyncfyUser(admin, apiKey, user.id);
      if (!idUser) return Response.json({ error: "syncfy user failed" }, { status: 502, headers: cors });
      const token = await getUserToken(apiKey, idUser);
      if (!token) return Response.json({ error: "session failed" }, { status: 502, headers: cors });
      const creds = await sfTok(token, "/credentials");
      const docs: Record<string, unknown>[] = [];
      const wanted = String(body?.id_credential ?? "");
      for (const c of creds.data ?? []) {
        if (wanted && String(c.id_credential) !== wanted) continue;
        let skip = 0;
        while (skip < 1000) {
          const at = await sfTok(token, `/attachments?id_credential=${c.id_credential}&limit=100&skip=${skip}`);
          const list = at.data ?? [];
          if (!list.length) break;
          for (const a of list) {
            const mime = String(a.mime ?? "");
            const label = String(a.description ?? a.name ?? a.file_name ?? a.keywords ?? "");
            const looksDoc = mime.includes("pdf") ||
              /constancia|situaci|opini|cumplim|declaraci|acuse|cédula|cedula/i.test(label);
            if (looksDoc) {
              docs.push({
                id_attachment: a.id_attachment,
                mime,
                label: label || "Documento SAT",
                dt_created: a.dt_created ?? null,
                site: c.site?.name ?? "",
                id_credential: c.id_credential,
              });
            }
          }
          if (list.length < 100) break;
          skip += 100;
        }
      }
      return Response.json({ documents: docs }, { headers: cors });
    }

    // Descarga el archivo de un attachment (p. ej. el PDF de la constancia) como base64.
    if (action === "download_attachment") {
      const idAtt = String(body?.id_attachment ?? "");
      if (!idAtt) return Response.json({ error: "id_attachment required" }, { status: 400, headers: cors });
      const idUser = await ensureSyncfyUser(admin, apiKey, user.id);
      if (!idUser) return Response.json({ error: "syncfy user failed" }, { status: 502, headers: cors });
      const token = await getUserToken(apiKey, idUser);
      if (!token) return Response.json({ error: "session failed" }, { status: 502, headers: cors });
      const r = await fetch(`${SYNCFY}/attachments/${idAtt}`, { headers: { Authorization: `Bearer ${token}` } });
      if (!r.ok) return Response.json({ error: `attachment HTTP ${r.status}` }, { status: 502, headers: cors });
      const ct = r.headers.get("content-type") ?? "application/octet-stream";
      if (ct.includes("application/json")) {
        const j = await r.json().catch(() => null);
        const resp: any = j?.response ?? j;
        const b64 = resp?.file ?? resp?.content ?? null;
        const url = resp?.url ?? null;
        if (b64) return Response.json({ base64: b64, mime: resp?.mime ?? "application/pdf" }, { headers: cors });
        if (url) {
          const f = await fetch(url);
          if (!f.ok) return Response.json({ error: `file HTTP ${f.status}` }, { status: 502, headers: cors });
          const buf = new Uint8Array(await f.arrayBuffer());
          return Response.json({ base64: bufToB64(buf), mime: f.headers.get("content-type") ?? "application/pdf" }, { headers: cors });
        }
        return Response.json({ error: "attachment sin archivo", meta: resp }, { status: 404, headers: cors });
      }
      const buf = new Uint8Array(await r.arrayBuffer());
      return Response.json({ base64: bufToB64(buf), mime: ct }, { headers: cors });
    }

    if (action === "sync_credential") {
      const idCredential = String(body?.id_credential ?? "");
      if (!idCredential) return Response.json({ error: "id_credential required" }, { status: 400, headers: cors });
      const { data: prof } = await admin.from("profiles").select("id, syncfy_user_id, rfc").eq("id", user.id).maybeSingle();
      if (!prof?.syncfy_user_id) return Response.json({ error: "no syncfy user" }, { status: 400, headers: cors });
      const summary = await syncCredential(admin, apiKey, idCredential, prof);
      return Response.json({ summary }, { headers: cors });
    }

    if (action === "sync_all") {
      const { data: prof } = await admin.from("profiles").select("id, syncfy_user_id, rfc").eq("id", user.id).maybeSingle();
      if (!prof?.syncfy_user_id) return Response.json({ error: "no syncfy user" }, { status: 400, headers: cors });
      const token = await getUserToken(apiKey, prof.syncfy_user_id);
      if (!token) return Response.json({ error: "session failed" }, { status: 502, headers: cors });
      const r = await sfTok(token, "/credentials");
      const results: Record<string, unknown> = {};
      for (const c of r.data ?? []) {
        results[c.id_credential] = await syncCredential(admin, apiKey, c.id_credential, prof);
      }
      return Response.json({ results }, { headers: cors });
    }

    if (action === "relink_contacts") {
      const { data: contacts } = await admin.from("contacts").select("id, rfc, dias_credito, category_id").eq("user_id", user.id).not("rfc", "is", null);
      let linked = 0;
      for (const c of contacts ?? []) {
        const rfc = String(c.rfc).toUpperCase().trim();
        const r1 = await admin.from("cfdis").update({ contact_id: c.id }).eq("user_id", user.id).eq("direction", "recibida").eq("rfc_emisor", rfc).is("contact_id", null).select("id");
        const r2 = await admin.from("cfdis").update({ contact_id: c.id }).eq("user_id", user.id).eq("direction", "emitida").eq("rfc_receptor", rfc).is("contact_id", null).select("id");
        linked += (r1.data?.length ?? 0) + (r2.data?.length ?? 0);
        const { data: pend } = await admin.from("cfdis").select("id, fecha_emision").eq("user_id", user.id).eq("contact_id", c.id).eq("status_pago", "pendiente");
        for (const f of pend ?? []) {
          const d = new Date(f.fecha_emision);
          if (!isNaN(d.getTime())) {
            d.setDate(d.getDate() + (c.dias_credito ?? 0));
            await admin.from("cfdis").update({ fecha_pago_estimada: d.toISOString().slice(0, 10) }).eq("id", f.id);
          }
        }
      }
      return Response.json({ linked }, { headers: cors });
    }

    if (action === "auto_categorize") {
      let n = 0;
      // 1) Hereda la categoría default del contacto
      const { data: cts } = await admin.from("contacts").select("id, category_id").eq("user_id", user.id).not("category_id", "is", null);
      for (const c of cts ?? []) {
        const r = await admin.from("cfdis").update({ category_id: c.category_id }).eq("user_id", user.id).eq("contact_id", c.id).is("category_id", null).select("id");
        n += r.data?.length ?? 0;
      }
      const { data: cats } = await admin.from("categories").select("id, nombre").eq("user_id", user.id);
      // 2) Catálogo SAT: asigna por uso_cfdi (el código es el prefijo del nombre, ej. "G03 Gastos en general")
      const satCats = (cats ?? []).filter((c: any) => /^(G0[1-3]|I0[1-8]|D0[1-9]|D10|S01|CP01|CN01)\s/.test(String(c.nombre)));
      for (const sc of satCats) {
        const code = String(sc.nombre).split(" ")[0];
        const r = await admin.from("cfdis").update({ category_id: sc.id }).eq("user_id", user.id).eq("uso_cfdi", code).is("category_id", null).select("id");
        n += r.data?.length ?? 0;
      }
      // 3) Reglas por texto del emisor/receptor
      const RULES: Array<[RegExp, string]> = [
        [/nomina|sueldo|payroll|imss|infonavit/i, "nomina"],
        [/renta|arrendamiento|inmobiliaria/i, "renta"],
        [/cfe|comision federal|energia|luz/i, "servicios"],
        [/telmex|telcel|at&t|izzi|totalplay|internet|telefon/i, "telefon"],
        [/gasolina|combustible|pemex/i, "combustible"],
        [/comision|interes bancario/i, "comisiones"],
        [/honorario|consultor|despacho|contador/i, "honorarios"],
        [/publicidad|marketing|google|facebook|meta platforms/i, "publicidad"],
      ];
      const norm = (s: string) => s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
      const { data: pend } = await admin.from("cfdis").select("id, direction, nombre_emisor, nombre_receptor").eq("user_id", user.id).is("category_id", null).limit(2000);
      for (const f of pend ?? []) {
        const name = norm(String(f.direction === "emitida" ? (f.nombre_receptor ?? "") : (f.nombre_emisor ?? "")));
        for (const [rx, key] of RULES) {
          if (rx.test(name)) {
            const cat = (cats ?? []).find((c: any) => norm(c.nombre).includes(key));
            if (cat) { await admin.from("cfdis").update({ category_id: cat.id }).eq("id", f.id); n++; }
            break;
          }
        }
      }
      return Response.json({ categorized: n }, { headers: cors });
    }

    return Response.json({ error: "unknown action" }, { status: 400, headers: cors });
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 500, headers: cors });
  }
});
