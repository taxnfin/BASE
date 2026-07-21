# TaxnFin — Versión nueva (Supabase)

Plataforma de inteligencia financiera y fiscal para PyMEs mexicanas.
**Comercializadora Oriental Technology SA de CV**

## Estructura

```
frontend/TaxnFin_16.html   App completa (single-file: HTML+CSS+JS)
supabase/migrations/       Esquema de base de datos (11 migraciones, RLS por usuario)
supabase/functions/        Edge Functions (Deno): syncfy, syncfy-webhook,
                           syncfy-validate, sat69b, fx, ai-analisis
ARQUITECTURA.md            Documento de arquitectura completo
docs/                      Análisis competitivo y otros documentos
```

## Módulos de la app

Dashboard de tesorería (flujo 13 semanas, alertas) · Planeación Fiscal (ISR/IVA,
retenciones, calendario SAT con seguimiento, export Excel/PDF) · Reporte Empresarial
(TaxnFin Score, KPIs históricos, Sankey, oportunidades de ahorro, listas 69/69-B) ·
Financiamiento (capacidad de pago, simulador, opciones de crédito MX, análisis IA) ·
Portal de Auditoría (expedientes PBC con evidencia en Storage) · Conexiones Syncfy
(SAT + bancos, documentos SAT).

## Backend

Supabase (proyecto `taxnfin`): Postgres + Auth + Edge Functions + Storage.
Los secretos (SYNCFY_API_KEY, ANTHROPIC_API_KEY, SETUP_KEY) viven en
`private.app_secrets` — **nunca en este repo**.

Ver `ARQUITECTURA.md` para el detalle completo, flujos de datos y pendientes.
