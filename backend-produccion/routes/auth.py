"""Authentication routes"""
from fastapi import APIRouter, Depends, HTTPException, Query, Form
from pydantic import BaseModel, EmailStr
from typing import Dict, Optional
from datetime import datetime, timezone, timedelta
import re
import secrets
import os
import logging
import asyncio
import httpx
import resend

from core.database import db
from core.auth import (
    get_current_user, hash_password, verify_password, create_token
)
from models.auth import User, UserCreate, UserLogin, TokenResponse

router = APIRouter(prefix="/auth")
logger = logging.getLogger(__name__)


# RFC mexicano: 3-4 letras (persona moral 3, física 4) + fecha AAMMDD + homoclave
RFC_REGEX = re.compile(r'^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$')


def _validar_rfc(rfc: str) -> str:
    """Normaliza y valida el RFC. Lanza 400 si es inválido."""
    rfc = (rfc or '').strip().upper().replace(' ', '').replace('-', '')
    if not rfc:
        raise HTTPException(status_code=400, detail="El RFC de la empresa es obligatorio")
    if not RFC_REGEX.match(rfc):
        raise HTTPException(
            status_code=400,
            detail="RFC inválido. Formato: 3-4 letras + fecha (AAMMDD) + homoclave, ej. COT080703U40"
        )
    if rfc in ('XAXX010101000', 'XEXX010101000'):
        raise HTTPException(
            status_code=400,
            detail="Ese es el RFC genérico del SAT (público en general). "
                   "Ingresa el RFC real de tu empresa para crear la cuenta."
        )
    return rfc


async def _verificar_captcha(token: Optional[str]) -> None:
    """Verifica el token de Google reCAPTCHA v2 (siteverify).

    Solo se aplica si RECAPTCHA_SECRET_KEY está configurada — así el sitio no
    se rompe antes de crear las llaves en Google. Con la llave configurada,
    un token ausente o inválido bloquea la operación.
    """
    secret = os.environ.get('RECAPTCHA_SECRET_KEY', '')
    if not secret:
        return
    if not token:
        raise HTTPException(status_code=400, detail="Completa el captcha para continuar")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                'https://www.google.com/recaptcha/api/siteverify',
                data={'secret': secret, 'response': token},
            )
            result = resp.json()
    except Exception as exc:
        logger.error("[RECAPTCHA] error verificando token: %s", exc)
        raise HTTPException(status_code=503, detail="No se pudo verificar el captcha, intenta de nuevo")
    if not result.get('success'):
        logger.warning("[RECAPTCHA] token rechazado: %s", result.get('error-codes'))
        raise HTTPException(status_code=400, detail="Captcha inválido, intenta de nuevo")


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


async def _send_reset_email(email: str, reset_link: str, token: str) -> None:
    """Send reset email via Resend. Logs the link to console if RESEND_API_KEY not set."""
    api_key = os.environ.get('RESEND_API_KEY', '')
    logger.info("[RESEND] api_key present=%s key_prefix=%s", bool(api_key), api_key[:8] if api_key else 'NONE')

    if not api_key:
        from core.config import settings as _settings
        if _settings.ENVIRONMENT == 'production':
            # En producción esto es un ERROR operativo: el usuario ve
            # "recibirás instrucciones" pero el correo jamás sale.
            logger.error(
                "[RESEND] RESEND_API_KEY NO configurada en PRODUCCIÓN — "
                "el email de reset para %s NO se envió. Configura la variable en Railway.",
                email,
            )
        else:
            logger.warning(
                "\n" + "=" * 60 + "\n"
                "DEV MODE — Password Reset Link for %s:\n%s\n"
                "Token: %s\n" + "=" * 60,
                email, reset_link, token
            )
        return

    resend.api_key = api_key
    from_address = os.environ.get('RESEND_FROM', 'TaxnFin <noreply@taxnfin.com>')
    logger.info("[RESEND] from=%s to=%s", from_address, email)

    text_body = (
        f"Recibimos una solicitud para restablecer tu contraseña.\n\n"
        f"Haz clic en el siguiente enlace (válido por 1 hora):\n{reset_link}\n\n"
        f"Si no solicitaste esto, ignora este mensaje."
    )
    html_body = f"""
    <html><body style="font-family:sans-serif;color:#0F172A;max-width:480px;margin:auto">
      <h2>Restablece tu contraseña</h2>
      <p>Recibimos una solicitud para restablecer la contraseña de tu cuenta TaxnFin.</p>
      <p style="margin:24px 0">
        <a href="{reset_link}"
           style="background:#0F172A;color:white;padding:12px 28px;text-decoration:none;
                  border-radius:6px;display:inline-block;font-weight:600">
          Restablecer contraseña
        </a>
      </p>
      <p style="color:#64748B;font-size:13px">
        Este enlace expira en <strong>1 hora</strong>.<br>
        Si no solicitaste esto, puedes ignorar este mensaje.
      </p>
    </body></html>
    """

    params = {
        "from": from_address,
        "to": [email],
        "subject": "Restablece tu contraseña — TaxnFin Cashflow",
        "html": html_body,
        "text": text_body,
    }

    async def _call_resend() -> object:
        return await asyncio.wait_for(
            asyncio.to_thread(resend.Emails.send, params),
            timeout=60.0,
        )

    try:
        logger.info("[RESEND] calling resend.Emails.send (attempt 1) ...")
        result = await _call_resend()
        logger.info("[RESEND] send OK — response: %s", result)
    except Exception as exc:
        logger.warning("[RESEND] attempt 1 failed (%s: %s), retrying ...", type(exc).__name__, exc)
        try:
            result = await _call_resend()
            logger.info("[RESEND] retry OK — response: %s", result)
        except Exception as exc2:
            logger.error("[RESEND] send FAILED after retry — %s: %s", type(exc2).__name__, exc2)
            raise


@router.get("/recaptcha/config")
async def recaptcha_config():
    """Config pública del reCAPTCHA. enabled=True solo si AMBAS llaves existen,
    para que el frontend nunca muestre un captcha que el backend no validará."""
    site_key = os.environ.get('RECAPTCHA_SITE_KEY', '')
    secret = os.environ.get('RECAPTCHA_SECRET_KEY', '')
    return {'enabled': bool(site_key and secret), 'site_key': site_key if secret else ''}


@router.post("/register", response_model=User)
async def register(user_data: UserCreate):
    """Register a new user.
    
    If company_id is not provided, automatically creates a new company
    using company_name (required) and assigns the new user as admin.
    If company_id IS provided, joins the existing company with the given role.
    """
    import uuid as _uuid
    from models.enums import UserRole

    await _verificar_captcha(user_data.captcha_token)

    existing = await db.users.find_one({'email': user_data.email}, {'_id': 0})
    if existing:
        raise HTTPException(status_code=400, detail="Email ya registrado")
    
    company_id = user_data.company_id
    role = user_data.role
    
    if company_id:
        # Joining an existing company
        company_exists = await db.companies.find_one({'id': company_id}, {'_id': 0})
        if not company_exists:
            raise HTTPException(status_code=400, detail="Empresa no encontrada")
        # SECURITY: Self-registration into an existing company always grants
        # the lowest privilege (viewer). Promotion must be done by an admin
        # via the admin panel. This prevents privilege escalation via the
        # public /register endpoint.
        role = UserRole.VIEWER
    else:
        # Auto-create a new company for this user
        if not user_data.company_name or not user_data.company_name.strip():
            raise HTTPException(
                status_code=400,
                detail="Debes proporcionar el nombre de tu empresa"
            )
        # RFC obligatorio y con formato válido (antes se permitía 'PENDIENTE')
        rfc_valido = _validar_rfc(user_data.company_rfc)
        # Evitar empresas duplicadas por RFC
        rfc_existente = await db.companies.find_one({'rfc': rfc_valido}, {'_id': 0, 'id': 1})
        if rfc_existente:
            raise HTTPException(
                status_code=400,
                detail="Este RFC ya tiene una cuenta en TaxnFin. Si es tu empresa, "
                       "pide al administrador de la cuenta que te agregue como usuario "
                       "desde el módulo de Usuarios, o escríbenos a hola@taxnfin.com "
                       "y con gusto te ayudamos."
            )
        
        company_id = str(_uuid.uuid4())
        company_doc = {
            'id': company_id,
            'nombre': user_data.company_name.strip(),
            'rfc': rfc_valido,
            'moneda_base': 'MXN',
            'pais': 'México',
            'activo': True,
            'inicio_semana': 1,
            'logo_url': None,
            'created_at': datetime.now(timezone.utc).isoformat()
        }
        await db.companies.insert_one(company_doc)
        # First user of a brand-new company becomes CFO (not admin)
        # Only platform admin (hola@taxnfin.com) should have admin role
        role = UserRole.CFO
    
    password_hash = hash_password(user_data.password)
    user = User(
        email=user_data.email,
        nombre=user_data.nombre,
        role=role,
        company_id=company_id,
        company_ids=[company_id],
        empresas_asignadas=[company_id],
    )
    
    doc = user.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    doc['password_hash'] = password_hash

    # ── Aprobación previa (pre-lanzamiento) ──────────────────────────────
    # Todo registro público queda PENDIENTE: la cuenta existe pero no puede
    # iniciar sesión hasta que el administrador de plataforma la apruebe en
    # el Panel de Administración (botón "Aprobar"). Para abrir el registro
    # sin aprobación, definir REQUIRE_SIGNUP_APPROVAL=0 en el entorno.
    if os.environ.get('REQUIRE_SIGNUP_APPROVAL', '1') != '0':
        doc['activo'] = False
        doc['estado'] = 'pendiente'

    await db.users.insert_one(doc)
    
    return user


@router.post("/login", response_model=TokenResponse)
async def login(credentials: UserLogin):
    """Login and get access token"""
    await _verificar_captcha(credentials.captcha_token)
    user = await db.users.find_one({'email': credentials.email}, {'_id': 0})
    if not user:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    if not verify_password(credentials.password, user['password_hash']):
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    # Cuenta registrada pero aún no aprobada por el admin de plataforma
    if user.get('estado') == 'pendiente':
        raise HTTPException(
            status_code=403,
            detail="Tu cuenta está pendiente de aprobación. "
                   "Te avisaremos por correo cuando sea activada."
        )
    if not user.get('activo'):
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    
    token = create_token(user['id'], user['company_id'], user['role'])
    user.pop('password_hash', None)
    
    if isinstance(user.get('created_at'), str):
        user['created_at'] = datetime.fromisoformat(user['created_at'])
    
    return TokenResponse(access_token=token, user=User(**user))


@router.get("/me", response_model=User)
async def get_me(current_user: Dict = Depends(get_current_user)):
    """Get current authenticated user"""
    if isinstance(current_user.get('created_at'), str):
        current_user['created_at'] = datetime.fromisoformat(current_user['created_at'])
    return User(**current_user)


@router.post("/forgot-password")
async def forgot_password(payload: ForgotPasswordRequest):
    """Generate a password-reset token and email the link.

    Always returns a generic success message to avoid user enumeration.
    """
    logger.info("[FORGOT-PW] request received for email=%s", payload.email)

    frontend_url = os.environ.get('FRONTEND_URL', 'http://localhost:3000')
    logger.info("[FORGOT-PW] FRONTEND_URL=%s", frontend_url)

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    reset_link = f"{frontend_url}/reset-password?token={token}"

    user = await db.users.find_one({'email': payload.email}, {'_id': 0, 'id': 1})
    logger.info("[FORGOT-PW] user found=%s", bool(user))

    if user:
        await db.password_resets.insert_one({
            'token': token,
            'user_id': user['id'],
            'email': payload.email,
            'expires_at': expires_at.isoformat(),
            'used': False,
            'created_at': datetime.now(timezone.utc).isoformat(),
        })
        logger.info("[FORGOT-PW] reset token saved to DB, calling _send_reset_email ...")
        try:
            await _send_reset_email(payload.email, reset_link, token)
            logger.info("[FORGOT-PW] _send_reset_email completed without exception")
        except Exception as exc:
            logger.error("[FORGOT-PW] _send_reset_email raised %s: %s", type(exc).__name__, exc)

    return {"message": "Si el email existe, recibirás instrucciones para restablecer tu contraseña."}


@router.post("/reset-password")
async def reset_password(payload: ResetPasswordRequest):
    """Consume a reset token and update the user's password."""
    if len(payload.new_password) < 8:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 8 caracteres")

    record = await db.password_resets.find_one(
        {'token': payload.token, 'used': False}, {'_id': 0}
    )
    if not record:
        raise HTTPException(status_code=400, detail="Token inválido o ya utilizado")

    expires_at = datetime.fromisoformat(record['expires_at'])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        raise HTTPException(status_code=400, detail="El token ha expirado. Solicita uno nuevo.")

    new_hash = hash_password(payload.new_password)
    await db.users.update_one(
        {'id': record['user_id']},
        {'$set': {'password_hash': new_hash}}
    )
    await db.password_resets.update_one(
        {'token': payload.token},
        {'$set': {'used': True, 'used_at': datetime.now(timezone.utc).isoformat()}}
    )

    return {"message": "Contraseña actualizada correctamente. Ya puedes iniciar sesión."}


@router.get("/auth0/config")
async def get_auth0_config():
    """Get Auth0 configuration for frontend"""
    from auth0_service import get_auth0_service
    
    service = get_auth0_service()
    
    if not service.is_configured():
        return {
            'enabled': False,
            'message': 'Auth0 no está configurado'
        }
    
    return {
        'enabled': True,
        'domain': service.domain,
        'client_id': service.client_id,
        'audience': service.audience
    }


@router.get("/auth0/login-url")
async def get_auth0_login_url_endpoint(redirect_uri: str = Query(...)):
    """Get Auth0 login URL for redirect"""
    from auth0_service import get_auth0_login_url, get_auth0_service
    
    service = get_auth0_service()
    if not service.is_configured():
        raise HTTPException(status_code=400, detail="Auth0 no está configurado")
    
    import secrets
    state = secrets.token_urlsafe(32)
    login_url = get_auth0_login_url(redirect_uri, state)
    
    return {
        'login_url': login_url,
        'state': state
    }


@router.post("/auth0/callback")
async def auth0_callback(code: str = Form(...), redirect_uri: str = Form(...)):
    """Exchange Auth0 authorization code for tokens and create/update local user"""
    from auth0_service import exchange_code_for_tokens, get_auth0_service
    import uuid
    import jwt
    from datetime import timedelta
    
    # Get JWT config from environment
    import os
    JWT_SECRET = os.environ.get('JWT_SECRET', 'taxnfin-secret-key-change-in-production')
    
    service = get_auth0_service()
    if not service.is_configured():
        raise HTTPException(status_code=400, detail="Auth0 no está configurado")
    
    try:
        # Exchange code for tokens
        tokens = await exchange_code_for_tokens(code, redirect_uri)
        access_token = tokens.get('access_token')
        id_token = tokens.get('id_token')
        
        # Get user info
        user_info = await service.get_user_info(access_token)
        auth0_id = user_info.get('sub')
        email = user_info.get('email')
        name = user_info.get('name', email.split('@')[0] if email else 'Usuario')
        
        # Look for existing user
        existing_user = await db.users.find_one(
            {'$or': [{'auth0_id': auth0_id}, {'email': email}]},
            {'_id': 0}
        )
        
        if existing_user:
            # Misma regla que /login: sin token para cuentas pendientes o desactivadas
            if existing_user.get('estado') == 'pendiente':
                raise HTTPException(
                    status_code=403,
                    detail="Tu cuenta está pendiente de aprobación. "
                           "Te avisaremos por correo cuando sea activada."
                )
            if not existing_user.get('activo', True):
                raise HTTPException(status_code=403, detail="Cuenta desactivada. Escríbenos a hola@taxnfin.com")
            # Update existing user with Auth0 info
            await db.users.update_one(
                {'id': existing_user['id']},
                {'$set': {
                    'auth0_id': auth0_id,
                    'auth0_last_login': datetime.now(timezone.utc).isoformat()
                }}
            )
            user = existing_user
        else:
            # Create new user
            user_id = str(uuid.uuid4())
            new_user = {
                'id': user_id,
                'email': email,
                'nombre': name,
                'password_hash': '',  # No password for Auth0 users
                'rol': 'user',
                'activo': True,
                'auth0_id': auth0_id,
                'auth0_last_login': datetime.now(timezone.utc).isoformat(),
                'created_at': datetime.now(timezone.utc).isoformat()
            }
            # Aprobación previa: los registros por Auth0/Google también quedan
            # PENDIENTES hasta que el admin de plataforma los apruebe en el panel.
            if os.environ.get('REQUIRE_SIGNUP_APPROVAL', '1') != '0':
                new_user['activo'] = False
                new_user['estado'] = 'pendiente'
            await db.users.insert_one(new_user)
            if new_user.get('estado') == 'pendiente':
                raise HTTPException(
                    status_code=403,
                    detail="Tu cuenta fue creada y está pendiente de aprobación. "
                           "Te avisaremos por correo cuando sea activada."
                )
            user = new_user
        
        # Generate internal JWT token
        internal_token = jwt.encode(
            {
                'user_id': user['id'],
                'email': user['email'],
                'auth_method': 'auth0',
                'exp': datetime.now(timezone.utc) + timedelta(days=7)
            },
            JWT_SECRET,
            algorithm='HS256'
        )
        
        return {
            'access_token': internal_token,
            'auth0_token': access_token,
            'user': {
                'id': user['id'],
                'email': user['email'],
                'nombre': user.get('nombre', name),
                'rol': user.get('rol', 'user')
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error en autenticación Auth0: {str(e)}")


@router.post("/auth0/verify")
async def verify_auth0_token(token: str = Form(...)):
    """Verify an Auth0 token"""
    from auth0_service import get_auth0_service
    
    service = get_auth0_service()
    if not service.is_configured():
        raise HTTPException(status_code=400, detail="Auth0 no está configurado")
    
    result = await service.verify_token(token)
    
    if not result.get('valid'):
        raise HTTPException(status_code=401, detail="Token inválido")
    return result


@router.post("/admin-reset-password")
async def admin_reset_password(
    payload: dict,
    current_user: Dict = Depends(get_current_user),
):
    """Admin/CFO puede generar un link de reset para usuarios DE SU EMPRESA.
    Fix seguridad 2026-07-04: antes cualquier usuario autenticado (incluso
    viewer) podía generar y recibir el link de reset de CUALQUIER cuenta de
    la plataforma — vector de robo de cuentas entre empresas."""
    # 1) Solo admin o cfo
    if current_user.get('role') not in ('admin', 'cfo'):
        raise HTTPException(status_code=403, detail="Solo admin o CFO pueden resetear contraseñas")

    email = (payload.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email requerido")

    # Verificar que el usuario target existe
    user = await db.users.find_one(
        {"email": email},
        {"_id": 0, "id": 1, "nombre": 1, "email": 1, "company_id": 1, "company_ids": 1}
    )
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    # 2) El target debe compartir al menos una empresa con el solicitante
    #    (el admin de plataforma hola@taxnfin.com está exento)
    _PLATFORM_ADMIN = 'hola@taxnfin.com'
    if current_user.get('email') != _PLATFORM_ADMIN:
        _caller_companies = set(current_user.get('company_ids') or [])
        if current_user.get('company_id'):
            _caller_companies.add(current_user['company_id'])
        _target_companies = set(user.get('company_ids') or [])
        if user.get('company_id'):
            _target_companies.add(user['company_id'])
        if not (_caller_companies & _target_companies):
            raise HTTPException(
                status_code=403,
                detail="Solo puedes resetear contraseñas de usuarios de tu empresa"
            )

    # Generar token de reset
    token_reset = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    frontend_url = os.environ.get('FRONTEND_URL', 'https://cashflow.taxnfin.com')
    reset_link = f"{frontend_url}/reset-password?token={token_reset}"

    await db.password_resets.insert_one({
        'token':      token_reset,
        'user_id':    user['id'],
        'email':      email,
        'expires_at': expires_at.isoformat(),
        'used':       False,
        'created_at': datetime.now(timezone.utc).isoformat(),
    })

    # Intentar enviar email
    try:
        await _send_reset_email(email, reset_link, token_reset)
        email_enviado = True
    except Exception as e:
        logger.error(f"[ADMIN-RESET] Error enviando email: {e}")
        email_enviado = False

    return {
        "success":      True,
        "nombre":       user["nombre"],
        "email":        email,
        "reset_link":   reset_link,
        "email_enviado": email_enviado,
        "expira":       "24 horas",
    }
