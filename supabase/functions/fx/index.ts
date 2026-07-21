import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "jsr:@supabase/supabase-js@2";

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

// Tipos de cambio a MXN. Fuente actual: frankfurter.app (BCE, sin llave).
// Para producción fiscal estricta se puede cambiar a Banxico SIE (tipo de cambio DOF)
// agregando BANXICO_TOKEN a private.app_secrets — la estructura de fx_rates no cambia.
const MONEDAS = ["USD", "EUR"];

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  try {
    const admin = createClient(
      Deno.env.get("SUPABASE_URL")!,
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    );

    const hoy = new Date().toISOString().slice(0, 10);

    const { data: existing } = await admin.from("fx_rates").select("moneda").eq("fecha", hoy);
    const have = new Set((existing ?? []).map((r: any) => r.moneda));
    const missing = MONEDAS.filter((m) => !have.has(m));

    if (missing.length) {
      for (const m of missing) {
        try {
          const r = await fetch(`https://api.frankfurter.app/latest?from=${m}&to=MXN`);
          const b = await r.json().catch(() => null);
          const rate = b?.rates?.MXN;
          if (rate) {
            await admin.from("fx_rates").upsert(
              { fecha: hoy, moneda: m, tasa_mxn: rate, fuente: "BCE/frankfurter" },
              { onConflict: "fecha,moneda" },
            );
          }
        } catch (_) { /* siguiente moneda */ }
      }
    }

    const desde = new Date();
    desde.setDate(desde.getDate() - 30);
    const { data: rates } = await admin.from("fx_rates")
      .select("fecha, moneda, tasa_mxn, fuente")
      .gte("fecha", desde.toISOString().slice(0, 10))
      .order("fecha", { ascending: false });

    const latest: Record<string, number> = { MXN: 1 };
    for (const r of rates ?? []) {
      if (latest[r.moneda] === undefined) latest[r.moneda] = Number(r.tasa_mxn);
    }

    return Response.json({ latest, history: rates ?? [] }, { headers: cors });
  } catch (e) {
    return Response.json({ error: String(e), latest: { MXN: 1 } }, { status: 200, headers: cors });
  }
});
