import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { adminClient, getApiKey, pick, syncCredential } from "./lib.ts";

Deno.serve(async (req: Request) => {
  const admin = adminClient();
  let payload: any = null;
  try {
    payload = await req.json();
  } catch (_) { /* body vacío */ }

  const eventType = String(
    pick(payload, [["event"], ["type"], ["data", "event"], ["endpoint"]]) ?? "unknown",
  );
  const idCredential = pick(payload, [
    ["id_credential"],
    ["data", "id_credential"],
    ["data", "object", "id_credential"],
    ["data", "credential", "id_credential"],
  ]) as string | null;
  const idUser = pick(payload, [
    ["id_user"],
    ["data", "id_user"],
    ["data", "object", "id_user"],
  ]) as string | null;

  const { data: evRow } = await admin.from("webhook_events").insert({
    event_type: eventType,
    id_credential: idCredential,
    payload,
  }).select("id").single();

  const work = (async () => {
    try {
      if (!idCredential) return;
      let prof = null as any;
      if (idUser) {
        const { data } = await admin.from("profiles").select("id, syncfy_user_id, rfc").eq("syncfy_user_id", idUser).maybeSingle();
        prof = data;
      }
      if (!prof) {
        const { data: credRow } = await admin.from("syncfy_credentials").select("user_id").eq("id_credential", idCredential).maybeSingle();
        if (credRow) {
          const { data } = await admin.from("profiles").select("id, syncfy_user_id, rfc").eq("id", credRow.user_id).maybeSingle();
          prof = data;
        }
      }
      if (!prof?.syncfy_user_id) {
        await admin.from("webhook_events").update({ error: "profile not found", processed: true }).eq("id", evRow?.id);
        return;
      }
      const apiKey = await getApiKey(admin);
      if (!apiKey) return;
      const summary = await syncCredential(admin, apiKey, String(idCredential), prof);
      await admin.from("webhook_events").update({
        processed: true,
        error: summary.errors.length ? summary.errors.slice(0, 5).join("; ") : null,
      }).eq("id", evRow?.id);
    } catch (e) {
      if (evRow?.id) {
        await admin.from("webhook_events").update({ error: String(e) }).eq("id", evRow.id);
      }
    }
  })();

  // @ts-ignore EdgeRuntime existe en Supabase
  if (typeof EdgeRuntime !== "undefined" && EdgeRuntime?.waitUntil) {
    // @ts-ignore
    EdgeRuntime.waitUntil(work);
  } else {
    await work;
  }

  return Response.json({ received: true });
});
