# TaxnFin — Arquitectura del sistema
**Comercializadora Oriental Technology SA de CV · RFC COT080703U40**
Actualizado: 17 de julio de 2026

## 1. Visión general

TaxnFin es una plataforma de inteligencia financiera y fiscal. La versión actual corre como
**aplicación de un solo archivo HTML** (`TaxnFin_16.html`) conectada directamente a
**Supabase** (base de datos Postgres + Auth + Edge Functions + Storage), que a su vez se
integra con **Syncfy/Paybook** (SAT y bancos), el **SAT** (listas 69/69-B), **Frankfurter/BCE**
(tipos de cambio) y la **API de Anthropic** (análisis con IA).

```
┌─────────────────────────────────────────────────────────────────────┐
│  TaxnFin_16.html  (frontend single-file: HTML+CSS+JS, Chart.js,     │
│  SheetJS y jsPDF por CDN, widget de Syncfy)                         │
└──────────────┬──────────────────────────────────────────────────────┘
               │ supabase-js (Auth + PostgREST + Storage)  /  fetch a Edge Functions
┌──────────────▼──────────────────────────────────────────────────────┐
│  SUPABASE  proyecto "taxnfin"  (vjgmzviusiyxonvfeboc, us-east-1)    │
│                                                                     │
│  Auth (email) ── profiles (trigger de registro + seed de categorías)│
│  Postgres (RLS por usuario en todas las tablas de datos)            │
│  Storage: bucket privado "audit" (evidencia del portal de auditoría)│
│  private.app_secrets + RPC get_secret (SYNCFY_API_KEY,              │
│    ANTHROPIC_API_KEY, SETUP_KEY) — solo service_role                │
│                                                                     │
│  Edge Functions (Deno):                                             │
│   · syncfy          (v8)  widget, sync, documentos SAT, catálogos   │
│   · syncfy-webhook  (v6)  eventos de Syncfy → resync en background  │
│   · syncfy-validate (v15) diagnóstico protegido con SETUP_KEY       │
│   · sat69b          (v2)  cruce listas negras + refresco background │
│   · fx              (v1)  tipos de cambio USD/EUR→MXN               │
│   · ai-analisis     (v1)  análisis CFO con Claude (Anthropic API)   │
└───────┬───────────────┬───────────────┬───────────────┬─────────────┘
        │               │               │               │
   Syncfy/Paybook    SAT (omawww)   Frankfurter     Anthropic API
   (SAT + bancos,    listas 69/69-B (BCE, FX)       (claude-sonnet-4-5)
   widget + webhooks) ⚠ bloquea IPs de nube
```

## 2. Frontend — `TaxnFin_16.html`

SPA por hash-routing (`#/ruta`), sin framework. Librerías por CDN: supabase-js,
Chart.js, SheetJS (export Excel), jsPDF + autotable (export PDF), widget v3 de Syncfy.

| Ruta | Módulo | Qué hace |
|---|---|---|
| `#/dashboard` | **Dashboard** | 4 KPIs (posición de caja + ancla bancaria, CxC viva + DSO-90d, CxP viva + DPO-90d, posición proyectada), flujo 13 semanas (saldo semanal: real/proyectado/rojo bajo umbral con línea de umbral), alertas de tesorería (saldo bajo proyectado, cobranza vencida, concentración de cliente), top ingresos esperados 4 semanas y top egresos comprometidos, tarjeta listas 69/69-B, cuentas y últimos CFDIs. |
| `#/conexiones` | **Conexiones** | Widget de Syncfy (SAT/bancos; contenedor `#widget` se recrea en cada apertura — fix del bug `nextSibling`), sincronizar ahora, y **Documentos SAT** (constancia de situación fiscal, acuses, declaraciones vía attachments; requiere credencial SAT real). |
| `#/facturas`, `#/movimientos` | CFDIs y movimientos bancarios sincronizados. |
| `#/contactos`, `#/categorias` | Catálogos: clientes/proveedores (RFC, días de crédito, tasas IVA/retención) y categorías (ingreso/COGS/SG&A + catálogo SAT c_UsoCFDI) con auto-categorización. |
| `#/cxc` | CxC/CxP con aging. |
| `#/flujo` | Motor de flujo de efectivo semanal (real = pagado; proyectado = pendiente por `fecha_pago_estimada` con rolado). |
| `#/financiamiento` | **Financiamiento**: opciones de crédito en México (Konfío, BBVA, Banorte, Kapital, HSBC, Santander — simular con su tasa / solicitar), otras vías (factoraje calculado con la CxC real, revolvente, leasing), capacidad de pago (conservadora/moderada/agresiva, clic para cargar), simulador con amortización francesa, score de viabilidad 0-100, CAT aproximado, IVA sobre intereses, tabla de impacto semanal OK/Déficit (26 sem), amortización desplegable con export PDF y **Análisis IA — CFO Virtual** (edge `ai-analisis`, con fallback a reglas). |
| `#/proyectos` | Partidas proyectadas no facturadas (`forecast_items`). |
| `#/metricas` | Métricas financieras (márgenes, DSO/DPO, ciclo de conversión, runway) con valores en vivo. |
| `#/reporte` | **Reporte Empresarial** (estilo Syncfy FinScore con datos propios): TaxnFin Score 300-850 (margen 35% + concentración HHI 30% + DSO 20% + CxC 15%) con gauge e historial 12 meses, desempeño anual (ingresos/egresos, ticket promedio, clientes activos, % top 5, HHI, canceladas), top clientes/proveedores 12m, listas negras 69/69-B. |
| `#/conciliacion` | Conciliación banco ↔ CFDI. |
| `#/impuestos` | **Planeación Fiscal** con tabs: **Resumen** (ISR provisional = CU×30%−ret. clientes con vencimiento a día hábil, IVA con arrastre LIVA 6, ISR anual estimado, ingresos nominales real/proyectado, provisionales por mes, semáforo fiscal), **Calendario SAT** (obligaciones PM 2026 a día hábil, checkbox "Presentada" → `sat_obligaciones`, export Excel/PDF), **IVA & Deducciones** (IVA 16/116 sobre flujo, gasto por categoría, cédula mensual con saldo a favor y ret. a enterar), **Retenciones** (ISR/IVA retenidos por clientes = a favor y a proveedores = a enterar, leídos de `ret_isr`/`ret_iva` de los CFDIs), **Estrategias** (sugerencias por reglas). |
| `#/auditoria` | **Portal de Auditoría**: expedientes (auditoría externa, revisión SAT, materialidad, due diligence) → solicitudes PBC por categoría con prioridad/fecha límite, flujo pendiente→enviada→en revisión→aceptada/rechazada (con motivo), subida de evidencia al bucket `audit` y descarga con URL firmada, barra de progreso. |
| `#/perfil` | Perfil y configuración: razón social, RFC, **coeficiente de utilidad** y **umbral de tesorería** (guardados en `profiles` + espejo en localStorage). |

Reglas de cálculo clave: montos no-MXN se convierten con `fx_rates` (edge `fx`);
"semana" = ISO week; CFDIs PUE se consideran cobrados/pagados al emitirse;
`fecha_pago_estimada` = emisión + días de crédito del contacto.

## 3. Base de datos (Postgres, esquema `public`, RLS por usuario)

| Tabla | Propósito |
|---|---|
| `profiles` | Extiende `auth.users`: razón social, RFC, `syncfy_user_id`, `cu`, `umbral_tesoreria`. Trigger al registrarse crea perfil + siembra categorías. |
| `syncfy_credentials` | Credenciales conectadas (SAT/banco), estatus del sitio. |
| `bank_accounts` / `bank_transactions` | Cuentas y movimientos sincronizados de Syncfy. |
| `cfdis` | Facturas SAT: emisor/receptor, importes, IVA, **ret_iva/ret_isr**, uso CFDI, método de pago, estado de cobro (`status_pago`, `fecha_pago`, `fecha_pago_estimada`), vínculos a contacto/categoría, JSON crudo en `data`. |
| `contacts` / `categories` | Catálogos (días de crédito, tasas; grupos ingreso/COGS/SG&A, catálogo SAT, colores, seeds idempotentes). |
| `forecast_items` | Ingresos/gastos proyectados no facturados. |
| `reconciliations` | Matching banco↔CFDI. |
| `fx_rates` | Tipos de cambio a MXN (catálogo compartido, solo lectura). |
| `sat_rfc_lists` | Listas negras SAT 69/69-B (catálogo compartido). ⚠ Pendiente carga inicial. |
| `sat_list_refresh` | Estado del refresco del listado (running/done/error). |
| `sat_obligaciones` | Declaraciones marcadas como presentadas (manual; preparado para fuente 'sat'). |
| `audit_engagements` / `audit_requests` | Portal de auditoría (expedientes y solicitudes con archivos JSON → Storage). |
| `webhook_events` | Bitácora de webhooks Syncfy (solo service role). |
| `private.app_secrets` | Secretos (SYNCFY_API_KEY, ANTHROPIC_API_KEY, SETUP_KEY); RPC `get_secret` solo para service_role. |

Storage: bucket privado **`audit`** con rutas `{uid}/{expediente}/{solicitud}/{archivo}` y
políticas por dueño.

## 4. Edge Functions

| Función | Auth | Qué hace |
|---|---|---|
| `syncfy` v8 | JWT usuario | Acciones: `widget_token` (crea usuario Syncfy si no existe + token de sesión para el widget), `credentials`, `sync_credential` / `sync_all` (bancos → transactions; SAT → attachments `/extra` → cfdis con retenciones, dirección por RFC propio, PUE=pagado, dedupe por uuid+dirección), `relink_contacts`, `auto_categorize` (contacto → uso CFDI → reglas de texto), **`documents`** (detecta constancia/acuses/PDFs entre attachments), **`download_attachment`** (archivo en base64). |
| `syncfy-webhook` v6 | pública | Recibe eventos de Syncfy, los registra en `webhook_events` y re-sincroniza la credencial en background (`EdgeRuntime.waitUntil`). |
| `syncfy-validate` v15 | SETUP_KEY | Herramienta de diagnóstico: estado de credenciales, conexión sandbox. |
| `sat69b` v2 | JWT usuario | Cruce inmediato de los RFCs del usuario (contactos + emisores de recibidas) contra `sat_rfc_lists`; si el listado tiene >7 días lanza descarga de los CSV del SAT en background con parseo streaming y estado en `sat_list_refresh`. ⚠ el SAT bloquea IPs de nube: el refresco automático falla desde Supabase (ver §6). |
| `fx` v1 | JWT usuario | Asegura tasas del día USD/EUR→MXN (frankfurter/BCE) y regresa últimas + 30 días. |
| `ai-analisis` v1 | JWT usuario | Análisis ejecutivo con Claude (`claude-sonnet-4-5`, API key en `app_secrets`): tipo `financiamiento` (mismo prompt CFO que el backend de referencia) o prompt libre acotado. |

## 5. Flujos principales

1. **Conexión SAT/banco**: Conexiones → `syncfy(widget_token)` → widget Syncfy → evento
   `success` → `sync_credential` → tablas. Después, webhooks de Syncfy mantienen los datos
   al día sin intervención.
2. **Cash flow**: real = CFDIs pagados por semana; proyectado = pendientes por
   `fecha_pago_estimada`; el saldo se encadena anclado al saldo bancario real.
3. **Listas negras**: dashboard/reporte → `sat69b` → matches + estado del listado; el
   frontend hace polling mientras `refreshing`.
4. **Fiscal**: todo se deriva de `cfdis` (devengado para ISR, flujo para IVA, retenciones de
   los campos ret_*); CU y umbral vienen de `profiles`.
5. **Financiamiento**: capacidad y simulación 100% en el cliente con el flujo semanal;
   análisis IA vía `ai-analisis`.
6. **Auditoría**: expedientes/solicitudes en Postgres; evidencia en Storage con URLs firmadas.

## 6. Limitaciones y pendientes conocidos

- **SAT bloquea la red de Supabase** (tcp timeout hacia omawww.sat.gob.mx): la carga del
  listado 69-B debe hacerse manualmente (Kary descarga el CSV en su navegador → se carga
  vía SQL) o desde un servidor con IP permitida (p. ej. Railway). El refresco automático
  queda implementado y se auto-recupera si el SAT llega a responder.
- La credencial actual de Syncfy es el **sitio sandbox** ("Transactions with Attachments"):
  no publica constancia/documentos ni datos reales. Conectar "SAT All in One" con la CIEC
  real habilita documentos, historial completo y (futuro) marcado automático de
  declaraciones presentadas con acuses.
- Portal de auditoría: aún sin link público/invitados para el auditor externo, comentarios
  por solicitud, ni analítica forense (7 analizadores tipo EY Helix del backend de
  referencia `audit_analytics.py`).
- El modelo de IA está fijado a `claude-sonnet-4-5` en `ai-analisis`.
- CU/umbral se espejan en localStorage; el Dashboard/Fiscal los leen del espejo (visitar
  Perfil una vez en cada navegador nuevo).

## 7. Mapa del paquete entregado

```
entrega/
├── ARQUITECTURA.md              ← este documento
├── frontend/TaxnFin_16.html     ← app completa (idéntica a Downloads y al repo)
└── supabase/
    ├── migrations/  01…11       ← esquema completo (el SQL exacto también vive en
    │                              supabase_migrations.schema_migrations del proyecto)
    └── functions/   syncfy/ syncfy-webhook/ syncfy-validate/ sat69b/ fx/ ai-analisis/
```

Backend de referencia (FastAPI/Mongo del producto cashflow.taxnfin.com) en el repo
`Documents\TAXNFIN1\backend\` — módulos espejados hasta ahora: `financiamiento.py` y
`audit_portal.py` (parcial).
