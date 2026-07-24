"""
Integration Scheduler - Automatic periodic sync for CONTALink, Alegra, and other accounting systems.
Runs every 6 hours for active integrations.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_sync_task = None


async def sync_contalink_for_company(db, integration: dict):
    """Sync CONTALink data: trial balance → financial statements"""
    from services.contalink import ContalinkClient
    from services.account_mapper import map_trial_balance_to_statements
    
    api_key = integration.get('credentials', {}).get('api_key', '')
    if not api_key:
        return {'status': 'error', 'message': 'No API key'}
    
    client = ContalinkClient(api_key)
    company_id = integration['company_id']
    
    # Sync last 3 months of trial balance
    now = datetime.now()
    results = []
    
    for months_back in range(3):
        target = now - timedelta(days=30 * months_back)
        year = target.year
        month = target.month
        start = f"{year}-{month:02d}-01"
        # Last day of month
        if month == 12:
            end = f"{year}-12-31"
        else:
            next_m = datetime(year, month + 1, 1) - timedelta(days=1)
            end = next_m.strftime("%Y-%m-%d")
        periodo = f"{year}-{month:02d}"
        
        try:
            tb = await client.get_trial_balance(start, end)
            items = tb.get('trial_balance', {}).get('items', [])
            
            if not items:
                continue
            
            # Save raw trial balance
            await db.integration_sync_data.update_one(
                {'integration_id': integration['id'], 'type': 'trial_balance', 'period': periodo},
                {'$set': {
                    'company_id': company_id,
                    'integration_id': integration['id'],
                    'type': 'trial_balance',
                    'period': periodo,
                    'data': tb,
                    'synced_at': datetime.now(timezone.utc).isoformat(),
                }},
                upsert=True
            )
            
            # Map trial balance to financial statements
            mapped = map_trial_balance_to_statements(items)
            
            if mapped.get('income'):
                await db.financial_statements.update_one(
                    {'company_id': company_id, 'tipo': 'estado_resultados', 'periodo': periodo},
                    {'$set': {
                        'company_id': company_id,
                        'tipo': 'estado_resultados',
                        'periodo': periodo,
                        'año': year,
                        'mes': month,
                        'datos': mapped['income'],
                        'archivo_original': f'CONTALink auto-sync {periodo}',
                        'source': 'contalink',
                        'updated_at': datetime.now(timezone.utc).isoformat(),
                    },
                    '$setOnInsert': {'created_at': datetime.now(timezone.utc).isoformat()}},
                    upsert=True
                )
            
            if mapped.get('balance'):
                await db.financial_statements.update_one(
                    {'company_id': company_id, 'tipo': 'balance_general', 'periodo': periodo},
                    {'$set': {
                        'company_id': company_id,
                        'tipo': 'balance_general',
                        'periodo': periodo,
                        'año': year,
                        'mes': month,
                        'datos': mapped['balance'],
                        'archivo_original': f'CONTALink auto-sync {periodo}',
                        'source': 'contalink',
                        'updated_at': datetime.now(timezone.utc).isoformat(),
                    },
                    '$setOnInsert': {'created_at': datetime.now(timezone.utc).isoformat()}},
                    upsert=True
                )
            
            results.append({'period': periodo, 'items': len(items), 'mapped': True})
            
        except Exception as e:
            logger.error(f"CONTALink sync error for {periodo}: {e}")
            results.append({'period': periodo, 'error': str(e)})
    
    # Update integration status
    await db.integrations.update_one(
        {'id': integration['id']},
        {'$set': {
            'last_sync': datetime.now(timezone.utc).isoformat(),
            'connection_status': 'connected',
            'sync_count': integration.get('sync_count', 0) + 1,
        }}
    )
    
    return {'status': 'success', 'results': results}


async def sync_alegra_for_company(db, integration: dict):
    """Sync Alegra data: generate financial statements from synced invoices/bills"""
    from services.alegra_financials import generate_alegra_financial_statements
    
    company_id = integration['company_id']
    
    # Check if Alegra is connected at company level
    company = await db.companies.find_one({'id': company_id}, {'_id': 0})
    if not company or not company.get('alegra_connected'):
        return {'status': 'skipped', 'message': 'Alegra not connected at company level'}
    
    # Generate financial statements for last 3 months
    now = datetime.now()
    results = []
    for months_back in range(3):
        target = now - timedelta(days=30 * months_back)
        periodo = f"{target.year}-{target.month:02d}"
        try:
            result = await generate_alegra_financial_statements(db, company_id, periodo)
            results.append(result)
        except Exception as e:
            results.append({'periodo': periodo, 'error': str(e)})
    
    # Update integration status
    await db.integrations.update_one(
        {'id': integration['id']},
        {'$set': {
            'last_sync': datetime.now(timezone.utc).isoformat(),
            'connection_status': 'connected',
            'sync_count': integration.get('sync_count', 0) + 1,
        }}
    )
    
    return {'status': 'success', 'results': results}


async def sync_sat_ciec_for_company(db, cred: dict):
    """Sync SAT CFDI data for a company that has an active CIEC credential."""
    from modules.cfdi_sat import SATSyncService
    from datetime import datetime, timezone, timedelta

    company_id = cred['company_id']
    rfc = cred.get('rfc', company_id[:8])

    # Smart date: overlap 30 days from last downloaded CFDI, or 1 year back if first sync
    now = datetime.now(timezone.utc)
    last_cfdi = await db.cfdis.find_one(
        {'company_id': company_id, 'source': 'sat_ciec'},
        {'fecha_emision': 1},
        sort=[('fecha_emision', -1)]
    )
    if last_cfdi and last_cfdi.get('fecha_emision'):
        try:
            last_date = datetime.fromisoformat(
                str(last_cfdi['fecha_emision']).replace('Z', '+00:00')
            )
            fecha_inicio = last_date - timedelta(days=30)
        except Exception:
            fecha_inicio = datetime(now.year - 1, 1, 1, tzinfo=timezone.utc)
    else:
        fecha_inicio = datetime(now.year - 1, 1, 1, tzinfo=timezone.utc)

    logger.info(f"  SAT CIEC sync RFC={rfc} desde {fecha_inicio.date()} hasta {now.date()}")

    try:
        service = SATSyncService(db)
        result = await service.sync_cfdis(
            company_id=company_id,
            fecha_inicio=fecha_inicio,
            fecha_fin=now,
            incluir_emitidos=True,
            incluir_recibidos=True,
            tipo_comprobante='',
        )
        return {'status': 'success', 'rfc': rfc, 'result': result}
    except Exception as e:
        logger.error(f"  SAT CIEC sync error RFC={rfc}: {e}")
        return {'status': 'error', 'rfc': rfc, 'error': str(e)}


async def run_all_syncs(db):
    """Run sync for all active integrations across all companies"""
    logger.info("=" * 50)
    logger.info("INTEGRATION SYNC - Starting automatic sync cycle")
    logger.info("=" * 50)

    # BUG A fix: also load active CIEC credentials from sat_credentials collection
    integrations = await db.integrations.find(
        {'$or': [{'is_active': True}, {'active': True}]},
        {'_id': 0}
    ).to_list(100)

    ciec_creds = await db.sat_credentials.find(
        {'status': 'active'},
        {'_id': 0, 'company_id': 1, 'rfc': 1, 'last_sync': 1}
    ).to_list(200)

    if not integrations and not ciec_creds:
        logger.info("No active integrations or CIEC credentials to sync")
        return

    # Sync ERP integrations (Contalink, Alegra)
    for integration in integrations:
        itype = integration.get('integration_type', '')
        # BUG C fix: use rfc or company_id prefix when label/name are empty
        iname = (
            integration.get('label')
            or integration.get('name')
            or integration.get('rfc')
            or integration['company_id'][:8]
        )
        logger.info(f"Syncing {iname} ({itype}) for company {integration['company_id'][:8]}...")

        try:
            if itype == 'contalink':
                result = await sync_contalink_for_company(db, integration)
            elif itype == 'alegra':
                result = await sync_alegra_for_company(db, integration)
            else:
                # BUG B fix: only skip if genuinely no handler — not because of missing field
                result = {'status': 'skipped', 'message': f'No sync handler for {itype}'}

            logger.info(f"  Result: {result.get('status', 'unknown')}")
        except Exception as e:
            logger.error(f"  Error syncing {iname}: {e}")

    # SAT CIEC deshabilitado en ciclo automático — Selenium consume ~600MB en Railway.
    # El sync manual desde la UI sigue funcionando normalmente.
    for cred in ciec_creds:
        rfc = cred.get('rfc', cred['company_id'][:8])
        logger.info(f"[SCHEDULER] SAT CIEC skipped en ciclo automático - solo sync manual (RFC={rfc})")

    # ── Syncfy: pull de respaldo espaciado (estrategia webhook-first) ──────────
    # Los datos bancarios llegan por webhook (credentials.refresh) sin pull pagado
    # de nuestro lado. Este bloque SOLO dispara un pull pagado si una credencial
    # lleva más de SYNCFY_FALLBACK_HOURS (default 72h) sin actividad — respaldo
    # por si un webhook se pierde o Syncfy deja de refrescar.
    # Requisito operativo: en el dashboard de Syncfy poner las credenciales en
    # modo on-demand (sin auto-refresh diario) para que el gasto lo controle
    # este respaldo y el botón de sync manual. Apagar con SYNCFY_FALLBACK_ENABLED=0.
    if os.environ.get('SYNCFY_FALLBACK_ENABLED', '1') != '0':
        try:
            from routes.syncfy import _run_syncfy_sync
            fallback_h = float(os.environ.get('SYNCFY_FALLBACK_HOURS', '72'))
            now = datetime.now(timezone.utc)

            # Última actividad por empresa (webhooks actualizan sync_status)
            status_rows = await db.sync_status.find(
                {'type': 'syncfy'}, {'_id': 0, 'company_id': 1, 'updated_at': 1}
            ).to_list(500)
            status_by_company = {r['company_id']: r.get('updated_at') for r in status_rows}

            def _parse(ts):
                if not ts:
                    return None
                try:
                    dt = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
                    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                except Exception:
                    return None

            sy_creds = await db.syncfy_credentials.find(
                {'activo': True}, {'_id': 0}
            ).to_list(500)
            for sc in sy_creds:
                candidatos = [
                    _parse(sc.get('last_pull')),
                    _parse(status_by_company.get(sc.get('company_id'))),
                    _parse(sc.get('created_at')),
                ]
                ultimo = max([d for d in candidatos if d], default=None)
                horas = (now - ultimo).total_seconds() / 3600 if ultimo else None
                etiqueta = str(sc.get('id_credential', ''))[:10]
                if ultimo is None or horas > fallback_h:
                    logger.info(
                        f"[SYNCFY] Fallback pull {etiqueta}… "
                        f"({'sin actividad registrada' if ultimo is None else f'{horas:.0f}h sin datos'})"
                    )
                    try:
                        await _run_syncfy_sync(sc['company_id'], sc['id_credential'],
                                               trigger_pull=True)
                    except Exception as e:
                        logger.error(f"[SYNCFY] Fallback pull error {etiqueta}: {e}")
                else:
                    logger.info(
                        f"[SYNCFY] {etiqueta}… fresca ({horas:.0f}h) — sin pull, webhook-first")
        except Exception as e:
            logger.error(f"[SYNCFY] Fallback block error: {e}")

    logger.info("INTEGRATION SYNC - Cycle complete")


async def _sync_loop(db, interval_hours: int = 6):
    """Background loop that runs syncs periodically"""
    while True:
        try:
            await run_all_syncs(db)
        except Exception as e:
            logger.error(f"Integration sync loop error: {e}")
        
        await asyncio.sleep(interval_hours * 3600)


def start_integration_scheduler(db, interval_hours: int = 6):
    """Start the background integration sync loop"""
    global _sync_task
    
    if _sync_task is not None:
        return
    
    loop = asyncio.get_event_loop()
    _sync_task = loop.create_task(_sync_loop(db, interval_hours))
    logger.info(f"Integration scheduler started (every {interval_hours}h)")


def stop_integration_scheduler():
    """Stop the integration sync loop"""
    global _sync_task
    if _sync_task:
        _sync_task.cancel()
        _sync_task = None
