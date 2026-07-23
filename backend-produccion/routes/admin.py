# TaxnFin Admin Routes - v2
"""
TaxnFin — Panel de Administración de Plataforma
Solo accesible para hola@taxnfin.com (role=admin).
No expone ningún dato financiero de los clientes.
"""
import uuid
from datetime import datetime, timezone
from typing import Dict, List
from fastapi import APIRouter, Depends, HTTPException, Request

from core.auth import get_current_user, hash_password
from core.database import db

router = APIRouter(prefix="/admin", tags=["Admin Plataforma"])

PLATFORM_ADMIN_EMAIL = "hola@taxnfin.com"

PLAN_PRICES_MXN = {
    "STARTER": 999,
    "GROWTH":  2499,
    "PRO":     4999,
}

VALID_PLANS = set(PLAN_PRICES_MXN.keys())


async def _find_cfo(user_id: str):
    """Busca un despacho (role=cfo) tolerando registros legacy:
    acepta el campo id, el email o el _id de Mongo. Devuelve _id, id y plan."""
    if not user_id or user_id in ("null", "undefined"):
        return None
    ors = [{"id": user_id}, {"email": user_id}]
    try:
        from bson import ObjectId
        ors.append({"_id": ObjectId(user_id)})
    except Exception:
        pass
    return await db.users.find_one({"role": "cfo", "$or": ors}, {"_id": 1, "id": 1, "plan": 1})


def _require_platform_admin(current_user: Dict) -> None:
    if current_user.get("role") != "admin" or current_user.get("email") != PLATFORM_ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Acceso exclusivo del administrador de plataforma")


def _user_estado(user: Dict) -> str:
    if user.get("estado") == "eliminado":
        return "eliminado"
    if user.get("estado") == "pendiente":
        return "pendiente"          # registro nuevo, aún sin aprobar
    if not user.get("activo", True):
        return "pausado"
    return "activo"


# ── GET /admin/stats ───────────────────────────────────────────────────────────

@router.get("/stats")
async def admin_stats(current_user: Dict = Depends(get_current_user)):
    _require_platform_admin(current_user)

    now = datetime.now(timezone.utc)
    mes_inicio = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    cfos = await db.users.find({"role": "cfo"}, {"_id": 0, "activo": 1, "estado": 1, "plan": 1, "created_at": 1}).to_list(2000)

    activos   = [u for u in cfos if _user_estado(u) == "activo"]
    pausados  = [u for u in cfos if _user_estado(u) == "pausado"]
    eliminados= [u for u in cfos if _user_estado(u) == "eliminado"]
    pendientes= [u for u in cfos if _user_estado(u) == "pendiente"]

    mrr = sum(PLAN_PRICES_MXN.get(u.get("plan", "STARTER"), 999) for u in activos)

    nuevos = sum(
        1 for u in activos
        if (u.get("created_at") or "") >= mes_inicio
    )

    empresas_activas = await db.companies.count_documents({"activo": True})

    return {
        "despachos_activos":   len(activos),
        "despachos_pausados":  len(pausados),
        "despachos_pendientes":len(pendientes),
        "despachos_eliminados":len(eliminados),
        "empresas_activas":    empresas_activas,
        "mrr_mxn":             mrr,
        "nuevos_este_mes":     nuevos,
    }


# ── GET /admin/despachos ───────────────────────────────────────────────────────

@router.get("/despachos")
async def listar_despachos(current_user: Dict = Depends(get_current_user)):
    _require_platform_admin(current_user)

    cfos = await db.users.find(
        {"role": "cfo"},
        {"password_hash": 0},
    ).sort("created_at", 1).to_list(2000)

    result = []
    for cfo in cfos:
        # Backfill: registros legacy sin campo "id" causaban "Despacho no encontrado"
        # al pausar/cambiar plan/eliminar (el frontend mandaba user_id nulo).
        if not cfo.get("id"):
            cfo["id"] = str(cfo["_id"])
            await db.users.update_one({"_id": cfo["_id"]}, {"$set": {"id": cfo["id"]}})
        cfo_id = cfo["id"]

        # Count empresas activas (companies in their company_ids that are activo=True)
        cfo_company_ids = cfo.get("company_ids") or ([cfo.get("company_id")] if cfo.get("company_id") else [])
        empresas_activas = await db.companies.count_documents({"id": {"$in": cfo_company_ids}, "activo": True})

        # Count usuarios activos
        usuarios_activos = await db.users.count_documents({"invited_by": cfo_id, "activo": True})

        result.append({
            "user_id":          cfo_id,
            "nombre":           cfo.get("nombre"),
            "email":            cfo.get("email"),
            "plan":             cfo.get("plan", "STARTER"),
            "fecha_vencimiento_plan": cfo.get("fecha_vencimiento_plan"),
            "empresas_activas": empresas_activas,
            "usuarios_activos": usuarios_activos,
            "fecha_registro":   cfo.get("created_at"),
            "estado":           _user_estado(cfo),
            "motivo_pausa":     cfo.get("motivo_pausa"),
            "fecha_pausa":      cfo.get("fecha_pausa"),
            "ultimo_acceso":    cfo.get("ultimo_acceso"),
        })

    return {"despachos": result}


# ── PUT /admin/despachos/{user_id}/pausar ─────────────────────────────────────

@router.put("/despachos/{user_id}/pausar")
async def pausar_despacho(
    user_id: str,
    request: Request,
    current_user: Dict = Depends(get_current_user),
):
    _require_platform_admin(current_user)

    cfo = await _find_cfo(user_id)
    if not cfo:
        raise HTTPException(status_code=404, detail="Despacho no encontrado")
    cfo_id = cfo.get("id") or str(cfo["_id"])

    body   = await request.json()
    motivo = (body.get("motivo") or "").strip() or "Sin motivo especificado"
    now    = datetime.now(timezone.utc).isoformat()

    # Pause CFO
    await db.users.update_one(
        {"_id": cfo["_id"]},
        {"$set": {
            "activo":       False,
            "estado":       "pausado",
            "motivo_pausa": motivo,
            "fecha_pausa":  now,
        }},
    )
    # Pause all team members invited by this CFO
    await db.users.update_many(
        {"invited_by": cfo_id},
        {"$set": {
            "activo":       False,
            "estado":       "pausado",
            "motivo_pausa": f"Despacho pausado: {motivo}",
            "fecha_pausa":  now,
        }},
    )

    return {"success": True, "motivo": motivo}


# ── PUT /admin/despachos/{user_id}/reactivar ──────────────────────────────────

@router.put("/despachos/{user_id}/reactivar")
async def reactivar_despacho(
    user_id: str,
    current_user: Dict = Depends(get_current_user),
):
    _require_platform_admin(current_user)

    cfo = await _find_cfo(user_id)
    if not cfo:
        raise HTTPException(status_code=404, detail="Despacho no encontrado")
    cfo_id = cfo.get("id") or str(cfo["_id"])

    # Reactivate CFO
    await db.users.update_one(
        {"_id": cfo["_id"]},
        {"$set": {"activo": True, "estado": "activo"}, "$unset": {"motivo_pausa": "", "fecha_pausa": ""}},
    )
    # Reactivate team members (only those paused by the CFO's pause, not manually deactivated)
    await db.users.update_many(
        {"invited_by": cfo_id, "estado": "pausado"},
        {"$set": {"activo": True, "estado": "activo"}, "$unset": {"motivo_pausa": "", "fecha_pausa": ""}},
    )

    return {"success": True}


# ── PUT /admin/despachos/{user_id}/plan ───────────────────────────────────────

@router.put("/despachos/{user_id}/plan")
async def cambiar_plan(
    user_id: str,
    request: Request,
    current_user: Dict = Depends(get_current_user),
):
    _require_platform_admin(current_user)

    body              = await request.json()
    nuevo_plan: str   = (body.get("plan") or "").upper()
    fecha_venc: str   = body.get("fecha_vencimiento", "")

    if nuevo_plan not in VALID_PLANS:
        raise HTTPException(status_code=400, detail=f"Plan inválido. Use: {', '.join(VALID_PLANS)}")

    cfo = await _find_cfo(user_id)
    if not cfo:
        raise HTTPException(status_code=404, detail="Despacho no encontrado")
    cfo_id = cfo.get("id") or str(cfo["_id"])

    plan_anterior = cfo.get("plan", "STARTER")
    now           = datetime.now(timezone.utc).isoformat()

    await db.users.update_one(
        {"_id": cfo["_id"]},
        {"$set": {
            "plan":                    nuevo_plan,
            "fecha_vencimiento_plan":  fecha_venc or None,
        }},
    )

    await db.plan_historial.insert_one({
        "user_id":          cfo_id,
        "plan_anterior":    plan_anterior,
        "plan_nuevo":       nuevo_plan,
        "fecha_vencimiento":fecha_venc or None,
        "changed_by":       current_user["id"],
        "created_at":       now,
    })

    return {"success": True, "plan": nuevo_plan}


# ── DELETE /admin/despachos/{user_id} (soft delete) ───────────────────────────

@router.delete("/despachos/{user_id}")
async def eliminar_despacho(
    user_id: str,
    current_user: Dict = Depends(get_current_user),
):
    _require_platform_admin(current_user)

    cfo = await _find_cfo(user_id)
    if not cfo:
        raise HTTPException(status_code=404, detail="Despacho no encontrado")
    cfo_id = cfo.get("id") or str(cfo["_id"])

    now = datetime.now(timezone.utc).isoformat()

    await db.users.update_one(
        {"_id": cfo["_id"]},
        {"$set": {"activo": False, "estado": "eliminado", "fecha_eliminado": now}},
    )
    # Soft-delete all team members
    await db.users.update_many(
        {"invited_by": cfo_id},
        {"$set": {"activo": False, "estado": "eliminado", "fecha_eliminado": now}},
    )

    return {"success": True}


# ── GET /admin/debug-cxc (temporary diagnostic) ───────────────────────────────

@router.get("/debug-cxc")
async def debug_cxc(
    company_id: str = "89cda61e",
    current_user: Dict = Depends(get_current_user),
):
    # SEGURIDAD (2026-07-08): antes no exigía autenticación — cualquiera podía
    # leer agregados de CFDIs de cualquier empresa. Ahora solo platform admin.
    _require_platform_admin(current_user)
    match_base = {"company_id": {"$regex": company_id}, "source": "alegra", "tipo_cfdi": "ingreso"}

    # Total docs
    total_docs = await db.cfdis.count_documents(match_base)

    # Por estado_conciliacion
    pipeline_estado = [
        {"$match": match_base},
        {"$group": {"_id": {"$ifNull": ["$estado_conciliacion", "null"]}, "count": {"$sum": 1}}}
    ]
    estado_raw = await db.cfdis.aggregate(pipeline_estado).to_list(20)
    por_estado = {r["_id"]: r["count"] for r in estado_raw}

    # Por estatus
    pipeline_estatus = [
        {"$match": match_base},
        {"$group": {"_id": {"$ifNull": ["$estatus", "null"]}, "count": {"$sum": 1}}}
    ]
    estatus_raw = await db.cfdis.aggregate(pipeline_estatus).to_list(20)
    known = {"vigente", "cancelado", "pagado"}
    por_estatus = {"activo": 0, "cancelado": 0, "pagado": 0, "otros": {}}
    for r in estatus_raw:
        k = r["_id"]
        if k == "cancelado":
            por_estatus["cancelado"] += r["count"]
        elif k in ("pagado", "paid", "closed"):
            por_estatus["pagado"] += r["count"]
        elif k in ("vigente", "active", "open", None, "null"):
            por_estatus["activo"] += r["count"]
        else:
            por_estatus["otros"][str(k)] = r["count"]

    # Saldo cero vs positivo
    con_saldo_cero = await db.cfdis.count_documents({
        **match_base,
        "$or": [{"saldo_pendiente": {"$lte": 0.01}}, {"saldo_pendiente": None}, {"saldo_pendiente": {"$exists": False}}]
    })
    con_saldo_positivo = await db.cfdis.count_documents({
        **match_base,
        "saldo_pendiente": {"$gt": 0.01}
    })

    # Muestra 5 docs
    samples = await db.cfdis.find(
        match_base,
        {"_id": 0, "referencia": 1, "receptor_nombre": 1, "total": 1,
         "saldo_pendiente": 1, "monto_cobrado": 1, "estado_conciliacion": 1,
         "estatus": 1, "fecha_emision": 1, "moneda": 1, "alegra_status": 1}
    ).limit(5).to_list(5)

    return {
        "total_docs_alegra": total_docs,
        "por_estado_conciliacion": por_estado,
        "por_estatus": por_estatus,
        "con_saldo_cero": con_saldo_cero,
        "con_saldo_positivo": con_saldo_positivo,
        "muestra_5": samples
    }


# ── SEGURIDAD ─────────────────────────────────────────────────────────────────
# Los endpoints GET /admin/reset-admin-password y GET /admin/fix-admin-role
# fueron ELIMINADOS (2026-07-04): permitían a cualquier persona sin autenticación
# resetear la contraseña del admin de plataforma a un valor conocido y escalar
# roles. El reset de contraseñas debe hacerse por el flujo normal de
# /auth/reset-password o directamente en la base de datos.


# ── GET /admin/setup-platform-admin (bootstrap, protegido) ────────────────────

@router.get("/setup-platform-admin")
async def setup_platform_admin():
    """
    One-time bootstrap endpoint — creates hola@taxnfin.com with role=admin.
    Returns 409 if the user already exists.
    En producción requiere la variable de entorno ALLOW_ADMIN_BOOTSTRAP=1.
    """
    import os as _os
    from core.config import settings as _settings
    if _settings.ENVIRONMENT == 'production' and not _os.environ.get('ALLOW_ADMIN_BOOTSTRAP'):
        raise HTTPException(
            status_code=403,
            detail="Bootstrap deshabilitado en producción. Define ALLOW_ADMIN_BOOTSTRAP=1 temporalmente para usarlo."
        )
    existing = await db.users.find_one({"email": PLATFORM_ADMIN_EMAIL}, {"_id": 0, "id": 1})
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"El administrador {PLATFORM_ADMIN_EMAIL} ya existe (id={existing['id']}). Endpoint desactivado.",
        )

    TEMP_PASSWORD = "TaxnFin2026!"
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    await db.users.insert_one({
        "id":                 user_id,
        "email":              PLATFORM_ADMIN_EMAIL,
        "nombre":             "TaxnFin Admin",
        "password_hash":      hash_password(TEMP_PASSWORD),
        "role":               "admin",
        "company_id":         user_id,
        "company_ids":        [],
        "empresas_asignadas": [],
        "activo":             True,
        "created_at":         now,
    })

    return {
        "success":  True,
        "message":  "Admin creado correctamente. Cambia la contraseña después del primer login.",
        "email":    PLATFORM_ADMIN_EMAIL,
        "password": TEMP_PASSWORD,
    }
