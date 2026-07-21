// TaxnFin — Listas 69 / 69-B del SAT v2
// La petición responde de inmediato con el cruce contra sat_rfc_lists; la descarga
// pesada de los CSV del SAT corre en segundo plano (EdgeRuntime.waitUntil) con
// parseo en streaming. Estado del refresco en sat_list_refresh.
// NOTA: el SAT (omawww.sat.gob.mx) bloquea conexiones desde la red de Supabase
// (tcp connect timeout), por lo que el refresco automático falla desde la nube;
// la carga inicial se hace manualmente (CSV descargado por el usuario → SQL).
import { createClient } from 'npm:@supabase/supabase-js@2'

declare const EdgeRuntime: { waitUntil(p: Promise<unknown>): void }

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, content-type, apikey, x-client-info',
}
const J = (b: unknown, s = 200) =>
  new Response(JSON.stringify(b), { status: s, headers: { ...CORS, 'Content-Type': 'application/json' } })

const RFC_RE = /^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$/
const URL_69B = 'https://omawww.sat.gob.mx/cifras_sat/Documents/Listado_Completo_69-B.csv'
const URL_69_NOLOC = 'https://omawww.sat.gob.mx/cifras_sat/Documents/No%20localizados.csv'
const STUCK_MS = 15 * 60_000

function csvFields(line: string): string[] {
  const out: string[] = []
  let cur = '', q = false
  for (const ch of line) {
    if (ch === '"') q = !q
    else if (ch === ',' && !q) { out.push(cur); cur = '' }
    else cur += ch
  }
  out.push(cur)
  return out
}

async function* csvLines(url: string, timeoutMs = 240_000): AsyncGenerator<string> {
  const r = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) })
  if (!r.ok || !r.body) throw new Error(`${url} -> HTTP ${r.status}`)
  const reader = r.body.getReader()
  let dec: TextDecoder | null = null
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    if (!dec) {
      const enc = value[0] === 0xef && value[1] === 0xbb && value[2] === 0xbf ? 'utf-8' : 'windows-1252'
      dec = new TextDecoder(enc)
    }
    buf += dec.decode(value, { stream: true })
    let i: number
    while ((i = buf.indexOf('\n')) >= 0) {
      yield buf.slice(0, i).replace(/\r$/, '')
      buf = buf.slice(i + 1)
    }
  }
  if (dec) buf += dec.decode()
  if (buf) yield buf.replace(/\r$/, '')
}

async function refreshLists(svc: ReturnType<typeof createClient>, rfcs: string[]) {
  const mark = (patch: Record<string, unknown>) =>
    svc.from('sat_list_refresh').upsert({ lista: '69B', ...patch })
  try {
    const rows: { rfc: string; lista: string; situacion: string }[] = []
    for await (const line of csvLines(URL_69B)) {
      const f = csvFields(line)
      const rfc = (f[1] || '').trim().toUpperCase()
      if (RFC_RE.test(rfc)) rows.push({ rfc, lista: '69B', situacion: (f[3] || '').trim() || 'Publicado 69-B' })
    }
    if (rows.length <= 100) throw new Error('Listado 69-B sospechosamente corto (' + rows.length + ' filas); no se reemplaza la tabla')
    await svc.from('sat_rfc_lists').delete().eq('lista', '69B')
    for (let i = 0; i < rows.length; i += 1000) {
      const { error } = await svc.from('sat_rfc_lists').upsert(rows.slice(i, i + 1000))
      if (error) throw new Error('insert 69B: ' + error.message)
    }
    let err69: string | null = null
    try {
      const mine = new Set(rfcs)
      const hits: { rfc: string; lista: string; situacion: string }[] = []
      for await (const line of csvLines(URL_69_NOLOC)) {
        const f = csvFields(line)
        const rfc = (f[1] || '').trim().toUpperCase()
        if (mine.has(rfc)) hits.push({ rfc, lista: '69', situacion: 'No localizado' })
      }
      if (hits.length) await svc.from('sat_rfc_lists').upsert(hits)
    } catch (e) { err69 = String((e as Error)?.message || e) }
    await mark({ status: 'done', total: rows.length, error: err69, finished_at: new Date().toISOString() })
  } catch (e) {
    await mark({ status: 'error', error: String((e as Error)?.message || e), finished_at: new Date().toISOString() })
  }
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
    const svc = createClient(supaUrl, Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!)

    let force = false
    try { force = !!(await req.json())?.force } catch (_) { /* sin cuerpo */ }

    // 1) Universo de RFCs del usuario: contactos + emisores de CFDIs recibidas
    const nombres = new Map<string, string>()
    const { data: cts } = await svc.from('contacts').select('nombre,rfc').eq('user_id', user.id).not('rfc', 'is', null)
    for (const c of cts || []) { const r = (c.rfc || '').trim().toUpperCase(); if (RFC_RE.test(r) && !nombres.has(r)) nombres.set(r, c.nombre || '') }
    const { data: emis } = await svc.from('cfdis').select('rfc_emisor,nombre_emisor').eq('user_id', user.id).eq('direction', 'recibida').limit(10000)
    for (const c of emis || []) { const r = (c.rfc_emisor || '').trim().toUpperCase(); if (RFC_RE.test(r) && !nombres.has(r)) nombres.set(r, c.nombre_emisor || '') }
    const rfcs = [...nombres.keys()]

    // 2) ¿Hace falta refrescar? (listado con más de 7 días, o force)
    const { data: last } = await svc.from('sat_rfc_lists').select('fecha_carga').eq('lista', '69B')
      .order('fecha_carga', { ascending: false }).limit(1)
    const lastDate = last?.[0]?.fecha_carga ? new Date(last[0].fecha_carga) : null
    const stale = force || !lastDate || (Date.now() - lastDate.getTime()) > 7 * 86400e3

    const { data: st } = await svc.from('sat_list_refresh').select('*').eq('lista', '69B').limit(1)
    const status = st?.[0] || null
    const runningFresh = status?.status === 'running' && status.started_at &&
      (Date.now() - new Date(status.started_at).getTime()) < STUCK_MS

    let refreshing = !!runningFresh
    if (stale && !runningFresh) {
      await svc.from('sat_list_refresh').upsert({
        lista: '69B', status: 'running', error: null, finished_at: null,
        started_at: new Date().toISOString(),
      })
      EdgeRuntime.waitUntil(refreshLists(svc, rfcs))
      refreshing = true
    }

    // 3) Cruce inmediato contra lo que ya hay en la tabla
    const matches: { rfc: string; lista: string; situacion: string | null; nombre: string }[] = []
    for (let i = 0; i < rfcs.length; i += 200) {
      const { data } = await svc.from('sat_rfc_lists').select('rfc,lista,situacion').in('rfc', rfcs.slice(i, i + 200))
      for (const m of data || []) matches.push({ ...m, nombre: nombres.get(m.rfc) || '' })
    }

    return J({
      matches,
      revisados: rfcs.length,
      fecha_listado: lastDate ? lastDate.toISOString().slice(0, 10) : null,
      refreshing,
      refresh_status: refreshing ? 'running' : (status?.status || 'idle'),
      refresh_error: status?.status === 'error' ? status.error : null,
      total_69b: status?.total || null,
    })
  } catch (e) {
    return J({ error: String((e as Error)?.message || e) }, 500)
  }
})
