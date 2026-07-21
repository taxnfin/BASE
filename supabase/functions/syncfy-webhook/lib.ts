import { createClient } from "jsr:@supabase/supabase-js@2";

export const SYNCFY = "https://sync.paybook.com/v1";

export function adminClient() {
  return createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
  );
}

export async function getApiKey(admin: any): Promise<string | null> {
  const { data } = await admin.rpc("get_secret", { k: "SYNCFY_API_KEY" });
  return data ?? null;
}

export async function sfKey(apiKey: string, path: string, init: RequestInit = {}) {
  const r = await fetch(`${SYNCFY}${path}`, {
    ...init,
    headers: { ...(init.headers ?? {}), "Authorization": `api_key api_key=${apiKey}` },
  });
  const b = await r.json().catch(() => null);
  return { status: r.status, ok: r.ok, data: b?.response ?? null, raw: b };
}

export async function sfTok(token: string, path: string, init: RequestInit = {}) {
  const r = await fetch(`${SYNCFY}${path}`, {
    ...init,
    headers: { ...(init.headers ?? {}), "Authorization": `Bearer ${token}` },
  });
  const b = await r.json().catch(() => null);
  return { status: r.status, ok: r.ok, data: b?.response ?? null, raw: b };
}

export async function getUserToken(apiKey: string, idUser: string): Promise<string | null> {
  const r = await fetch(`${SYNCFY}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey, id_user: idUser }),
  });
  const b = await r.json().catch(() => null);
  return b?.response?.token ?? null;
}

export function pick(obj: unknown, paths: string[][]): unknown {
  for (const p of paths) {
    let cur: any = obj;
    for (const k of p) cur = cur?.[k];
    if (cur !== null && cur !== undefined && cur !== "") return cur;
  }
  return null;
}

export const num = (v: unknown) => (v === null || v === undefined || v === "" ? null : Number(v));
export const unixToIso = (v: unknown) => {
  const n = num(v);
  return n ? new Date(n * 1000).toISOString() : null;
};

export function parseRetenciones(e: any): { ret_iva: number | null; ret_isr: number | null } {
  let retIva: number | null = null;
  let retIsr: number | null = null;
  const raw: any = pick(e, [["impuestos", "retenciones"], ["Impuestos", "Retenciones"], ["retenciones"], ["Retenciones"]]);
  let list: any[] = [];
  if (Array.isArray(raw)) list = raw;
  else if (Array.isArray(raw?.retencion)) list = raw.retencion;
  else if (Array.isArray(raw?.Retencion)) list = raw.Retencion;
  else if (raw && typeof raw === "object") list = [raw.retencion ?? raw.Retencion ?? raw].flat().filter(Boolean);
  for (const r of list) {
    const imp = String(r?.impuesto ?? r?.Impuesto ?? "").toUpperCase();
    const v = num(r?.importe ?? r?.Importe) ?? 0;
    if (imp === "001" || imp.includes("ISR")) retIsr = (retIsr ?? 0) + v;
    else if (imp === "002" || imp.includes("IVA")) retIva = (retIva ?? 0) + v;
  }
  return { ret_iva: retIva, ret_isr: retIsr };
}

export async function ensureSyncfyUser(admin: any, apiKey: string, userId: string): Promise<string | null> {
  const { data: prof } = await admin.from("profiles").select("syncfy_user_id").eq("id", userId).maybeSingle();
  if (prof?.syncfy_user_id) return prof.syncfy_user_id;
  const r = await sfKey(apiKey, "/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: `taxnfin-${userId}` }),
  });
  const idUser = r.data?.id_user;
  if (!idUser) return null;
  await admin.from("profiles").upsert({ id: userId, syncfy_user_id: idUser });
  return idUser;
}

const SAT_SITE_IDS = new Set([
  "61c12b8cde3c034b3c8b25b1",
  "5da784f1f9de2a06483abec1",
  "5f2c2aacd74b837fc10602c1",
  "5ee233e9c2923160b00e27e1",
  "6744ff4808f420eff5c9d31e",
  "58b884fc056f295aa1483a02",
]);

function addDays(iso: string, days: number): string | null {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  d.setDate(d.getDate() + (days || 0));
  return d.toISOString().slice(0, 10);
}

export async function syncCredential(
  admin: any,
  apiKey: string,
  idCredential: string,
  userRow: { id: string; syncfy_user_id: string; rfc?: string | null },
) {
  const summary = { site: "", type: "", accounts: 0, transactions: 0, cfdis: 0, errors: [] as string[] };

  const token = await getUserToken(apiKey, userRow.syncfy_user_id);
  if (!token) {
    summary.errors.push("no session token");
    return summary;
  }

  const creds = await sfTok(token, "/credentials");
  const cred = (creds.data ?? []).find((c: any) => c.id_credential === idCredential) ?? null;
  let siteName = String(cred?.site?.name ?? "");
  if (!siteName && cred?.id_site) {
    const cat = await sfTok(token, "/catalogues/sites");
    const site = (cat.data ?? []).find((s: any) => String(s.id_site) === String(cred.id_site));
    siteName = String(site?.name ?? "");
  }
  const isSat = SAT_SITE_IDS.has(String(cred?.id_site)) || /\bsat\b|ciec|cfdi|attachment/i.test(siteName);
  summary.site = siteName;
  summary.type = isSat ? "sat" : "bank";

  const { data: credRow, error: credErr } = await admin.from("syncfy_credentials").upsert({
    user_id: userRow.id,
    id_credential: idCredential,
    id_site: String(cred?.id_site ?? ""),
    site_name: siteName,
    credential_type: isSat ? "sat" : "bank",
    status_code: cred?.code ?? null,
    is_authorized: cred?.is_authorized === 1 || cred?.is_authorized === true,
    last_refresh: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  }, { onConflict: "id_credential" }).select().single();
  if (credErr) summary.errors.push(`cred: ${credErr.message}`);

  const accts = await sfTok(token, `/accounts?id_credential=${idCredential}`);
  const accountMap: Record<string, string> = {};
  for (const a of accts.data ?? []) {
    const { data: accRow, error } = await admin.from("bank_accounts").upsert({
      user_id: userRow.id,
      credential_id: credRow?.id ?? null,
      id_account: String(a.id_account),
      name: a.name ?? null,
      account_number: a.number ?? null,
      account_type: a.account_type ?? a.nature ?? null,
      currency: a.currency ?? "MXN",
      balance: num(a.balance),
      site_name: siteName,
      refreshed_at: unixToIso(a.dt_refresh) ?? new Date().toISOString(),
    }, { onConflict: "id_account" }).select("id,id_account").single();
    if (error) summary.errors.push(`acct: ${error.message}`);
    else {
      accountMap[accRow.id_account] = accRow.id;
      summary.accounts++;
    }
  }

  if (!isSat) {
    let skip = 0;
    const limit = 500;
    while (skip < 5000) {
      const txs = await sfTok(token, `/transactions?id_credential=${idCredential}&limit=${limit}&skip=${skip}`);
      const list = txs.data ?? [];
      if (!list.length) break;
      const rows = list.map((t: any) => ({
        user_id: userRow.id,
        account_id: accountMap[String(t.id_account)] ?? null,
        id_transaction: String(t.id_transaction),
        description: t.description ?? null,
        amount: num(t.amount) ?? 0,
        currency: t.currency ?? "MXN",
        dt_transaction: unixToIso(t.dt_transaction) ?? new Date().toISOString(),
        reference: t.reference ?? null,
        extra: t.extra ?? null,
      }));
      const { error } = await admin.from("bank_transactions").upsert(rows, { onConflict: "id_transaction" });
      if (error) summary.errors.push(`tx: ${error.message}`);
      else summary.transactions += rows.length;
      if (list.length < limit) break;
      skip += limit;
    }
  }

  if (isSat) {
    const { data: contactRows } = await admin.from("contacts")
      .select("id, rfc, dias_credito, category_id").eq("user_id", userRow.id).not("rfc", "is", null);
    const contactByRfc: Record<string, any> = {};
    for (const c of contactRows ?? []) contactByRfc[String(c.rfc).toUpperCase().trim()] = c;

    const { data: existRows } = await admin.from("cfdis").select("uuid_cfdi, direction").eq("user_id", userRow.id);
    const existSet = new Set((existRows ?? []).map((r: any) => `${r.uuid_cfdi}|${r.direction}`));

    let askip = 0;
    const alimit = 50;
    const maxPerRun = 150;
    while (summary.cfdis < maxPerRun) {
      const at = await sfTok(token, `/attachments?id_credential=${idCredential}&limit=${alimit}&skip=${askip}`);
      const list = at.data ?? [];
      if (!list.length) break;
      for (const att of list) {
        if (summary.cfdis >= maxPerRun) break;
        try {
          const ex = await sfTok(token, `/attachments/${att.id_attachment}/extra`);
          const e: any = ex.data ?? {};
          const uuid = pick(e, [["uuid"], ["UUID"], ["complemento", "timbreFiscalDigital", "UUID"], ["Complemento", "TimbreFiscalDigital", "UUID"], ["timbre", "uuid"]]) ?? att.id_attachment;
          if (!uuid) continue;
          const rfcEmisor = String(pick(e, [["emisor", "rfc"], ["Emisor", "Rfc"], ["rfc_emisor"]]) ?? "").toUpperCase();
          const rfcReceptor = String(pick(e, [["receptor", "rfc"], ["Receptor", "Rfc"], ["rfc_receptor"]]) ?? "").toUpperCase();
          const myRfc = (userRow.rfc ?? "").toUpperCase();
          const direction = myRfc && rfcEmisor === myRfc ? "emitida" : "recibida";
          const iva = pick(e, [["impuestos", "totalImpuestosTrasladados"], ["Impuestos", "TotalImpuestosTrasladados"], ["iva"]]);
          const rets = parseRetenciones(e);
          const rfcContraparte = direction === "emitida" ? rfcReceptor : rfcEmisor;
          const contact = rfcContraparte ? (contactByRfc[rfcContraparte] ?? null) : null;
          const fechaEmision = String(pick(e, [["fecha"], ["Fecha"], ["fecha_emision"]]) ?? unixToIso(att.dt_created) ?? new Date().toISOString());
          const metodoPago = pick(e, [["metodoPago"], ["MetodoPago"]]);
          const uuidUp = String(uuid).toUpperCase();
          const key = `${uuidUp}|${direction}`;

          const core = {
            user_id: userRow.id,
            uuid_cfdi: uuidUp,
            direction,
            tipo_comprobante: pick(e, [["tipoDeComprobante"], ["TipoDeComprobante"], ["tipo_comprobante"]]),
            version: pick(e, [["version"], ["Version"]]),
            rfc_emisor: rfcEmisor || null,
            nombre_emisor: pick(e, [["emisor", "nombre"], ["Emisor", "Nombre"]]),
            rfc_receptor: rfcReceptor || null,
            nombre_receptor: pick(e, [["receptor", "nombre"], ["Receptor", "Nombre"]]),
            fecha_emision: fechaEmision,
            subtotal: num(pick(e, [["subTotal"], ["SubTotal"], ["subtotal"]])),
            iva: num(iva),
            ret_iva: rets.ret_iva,
            ret_isr: rets.ret_isr,
            total: num(pick(e, [["total"], ["Total"], ["monto"]])),
            moneda: pick(e, [["moneda"], ["Moneda"]]) ?? "MXN",
            metodo_pago: metodoPago,
            forma_pago: pick(e, [["formaPago"], ["FormaPago"]]),
            uso_cfdi: pick(e, [["receptor", "usoCFDI"], ["Receptor", "UsoCFDI"]]),
            data: { id_attachment: att.id_attachment, mime: att.mime ?? null, extra: e },
          };

          let error = null;
          if (existSet.has(key)) {
            const r = await admin.from("cfdis").update(core)
              .eq("user_id", userRow.id).eq("uuid_cfdi", uuidUp).eq("direction", direction);
            error = r.error;
          } else {
            const esPUE = String(metodoPago ?? "").toUpperCase() === "PUE";
            const row = {
              ...core,
              contact_id: contact?.id ?? null,
              category_id: contact?.category_id ?? null,
              fecha_pago_estimada: addDays(fechaEmision, contact?.dias_credito ?? 0),
              status_pago: esPUE ? "pagado" : "pendiente",
              fecha_pago: esPUE ? fechaEmision.slice(0, 10) : null,
            };
            const r = await admin.from("cfdis").insert(row);
            error = r.error;
            if (!error) existSet.add(key);
          }
          if (error) summary.errors.push(`cfdi ${uuidUp}: ${error.message}`);
          else summary.cfdis++;
        } catch (err) {
          summary.errors.push(`att ${att?.id_attachment}: ${String(err)}`);
        }
      }
      if (list.length < alimit) break;
      askip += alimit;
    }
  }

  return summary;
}
