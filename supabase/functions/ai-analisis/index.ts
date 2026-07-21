// TaxnFin — Análisis con IA (Claude) para financiamiento y otros módulos
import { createClient } from 'npm:@supabase/supabase-js@2'

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, content-type, apikey, x-client-info',
}
const J = (b: unknown, s = 200) =>
  new Response(JSON.stringify(b), { status: s, headers: { ...CORS, 'Content-Type': 'application/json' } })

function promptFinanciamiento(d: any): string {
  const pct = d.prom > 0 ? `${(d.cuotaSem / d.prom * 100).toFixed(0)}%` : 'N/A'
  return `Eres un CFO experto en empresas mexicanas. Analiza esta solicitud de crédito:\n\n` +
    `Monto solicitado: $${Number(d.monto).toLocaleString('es-MX')} MXN\n` +
    `Tasa anual: ${d.tasaPct}%\n` +
    `Plazo: ${d.plazo} meses\n` +
    `Cuota mensual: $${Math.round(d.cuota).toLocaleString('es-MX')} MXN (${pct} del flujo semanal promedio)\n` +
    `Total a pagar: $${Math.round(d.totalPagar).toLocaleString('es-MX')} MXN (intereses: $${Math.round(d.intereses).toLocaleString('es-MX')})\n` +
    `Flujo neto promedio semanal: $${Math.round(d.prom).toLocaleString('es-MX')} MXN\n` +
    `Semanas con flujo negativo si toma el crédito: ${d.crit} de 16\n` +
    `Viabilidad calculada: ${d.viab}\n\n` +
    `Da un análisis ejecutivo en español de máximo 100 palabras con: ` +
    `1) Si conviene tomar este crédito, 2) El mayor riesgo, 3) Una recomendación concreta.`
}

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: CORS })
  try {
    const supaUrl = Deno.env.get('SUPABASE_URL')!
    const auth = createClient(supaUrl, Deno.env.get('SUPABASE_ANON_KEY')!, {
      global: { headers: { Authorization: req.headers.get('Authorization') || '' } },
    })
    const { data: { user } } = await auth.auth.getUser()
    if (!user) return J({ error: 'No autenticado' }, 401)
    const admin = createClient(supaUrl, Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!)
    const { data: apiKey } = await admin.rpc('get_secret', { k: 'ANTHROPIC_API_KEY' })
    if (!apiKey) return J({ error: 'ANTHROPIC_API_KEY no configurada' }, 500)

    const body = await req.json().catch(() => ({}))
    let prompt = ''
    if (body?.tipo === 'financiamiento') prompt = promptFinanciamiento(body.datos || {})
    else if (typeof body?.prompt === 'string' && body.prompt.length < 6000) {
      prompt = 'Eres un CFO experto en empresas mexicanas. Responde en español, máximo 120 palabras.\n\n' + body.prompt
    }
    if (!prompt) return J({ error: 'tipo o prompt requerido' }, 400)

    const r = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'x-api-key': String(apiKey),
        'anthropic-version': '2023-06-01',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: 'claude-sonnet-4-5',
        max_tokens: 300,
        messages: [{ role: 'user', content: prompt }],
      }),
    })
    const jb: any = await r.json().catch(() => null)
    if (!r.ok) return J({ error: 'Anthropic API: ' + (jb?.error?.message || r.status) }, 502)
    const texto = jb?.content?.[0]?.text || ''
    return J({ texto })
  } catch (e) {
    return J({ error: String((e as Error)?.message || e) }, 500)
  }
})
