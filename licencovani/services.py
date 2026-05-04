import base64
import json
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from django.conf import settings
from django.utils.formats import date_format
from django.utils import timezone

from .models import LicenseConfig, LicenseEvent

LICENSABLE_MODULE_SLUGS = {"sklad", "fakturace", "finance", "pokladna", "ankety"}


@dataclass
class LicenseCheckResult:
    is_valid: bool
    status: str
    message: str
    payload: dict


def get_license_config():
    config, _ = LicenseConfig.objects.get_or_create(singleton_key=1)
    return config


def canonicalize_payload(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def get_public_key_path():
    return Path(getattr(settings, "LICENSE_PUBLIC_KEY_PATH", settings.BASE_DIR / "data" / "licencovani" / "public_key.pem"))


def get_private_key_path():
    return Path(getattr(settings, "LICENSE_PRIVATE_KEY_PATH", settings.BASE_DIR / "data" / "licencovani" / "private_key.pem"))


def load_public_key():
    public_key_text = getattr(settings, "LICENSE_PUBLIC_KEY", "").strip()
    if public_key_text:
        key_bytes = public_key_text.encode("utf-8")
    else:
        key_path = get_public_key_path()
        if not key_path.exists():
            raise FileNotFoundError(f"Veřejný klíč licence nebyl nalezen: {key_path}")
        key_bytes = key_path.read_bytes()
    return serialization.load_pem_public_key(key_bytes)


def serialize_date(value):
    if not value:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def format_czech_date(value):
    if not value:
        return ""
    if isinstance(value, str):
        value = _date_or_none(value)
    if not value:
        return ""
    return date_format(value, "j. F Y", use_l10n=True)


def parse_license_blob(raw_text):
    if not raw_text.strip():
        raise ValueError("Licence je prázdná.")
    data = json.loads(raw_text)
    if not isinstance(data, dict):
        raise ValueError("Licence musí být JSON objekt.")
    payload = data.get("payload")
    signature = data.get("signature")
    if not isinstance(payload, dict):
        raise ValueError("Licence neobsahuje platný objekt payload.")
    if not signature:
        raise ValueError("Licence neobsahuje podpis.")
    return data, payload, signature


def verify_license_blob(raw_text, instance_id=None):
    _, payload, signature = parse_license_blob(raw_text)
    public_key = load_public_key()
    try:
        signature_bytes = base64.b64decode(signature)
    except Exception as exc:
        raise ValueError("Podpis licence není validní base64 řetězec.") from exc

    try:
        public_key.verify(signature_bytes, canonicalize_payload(payload))
    except InvalidSignature as exc:
        raise ValueError("Podpis licence nesouhlasí s obsahem licence.") from exc

    bound_instance = payload.get("instance_id")
    if instance_id and bound_instance and str(bound_instance) != str(instance_id):
        raise ValueError("Licence je vázaná na jinou instalaci aplikace.")

    return payload


def _date_or_none(value):
    if not value:
        return None
    return timezone.datetime.fromisoformat(value).date()


def evaluate_payload_status(payload):
    today = timezone.localdate()
    valid_from = _date_or_none(payload.get("valid_from"))
    valid_until = _date_or_none(payload.get("valid_until"))
    support_until = _date_or_none(payload.get("support_until"))
    grace_until = _date_or_none(payload.get("grace_until"))

    if valid_from and today < valid_from:
        return LicenseConfig.STATUS_INVALID, f"Licence je platná až od {valid_from:%d.%m.%Y}."

    if valid_until and today > valid_until:
        if grace_until and today <= grace_until:
            return LicenseConfig.STATUS_GRACE, f"Licence vypršela {valid_until:%d.%m.%Y}, běží ochranná lhůta."
        return LicenseConfig.STATUS_EXPIRED, f"Licence vypršela {valid_until:%d.%m.%Y}."

    if support_until and today > support_until:
        return LicenseConfig.STATUS_SUPPORT, f"Licence je aktivní, ale podpora skončila {support_until:%d.%m.%Y}."

    if valid_until:
        return LicenseConfig.STATUS_ACTIVE, f"Licence je aktivní do {valid_until:%d.%m.%Y}."
    return LicenseConfig.STATUS_ACTIVE, "Licence je aktivní bez pevného data konce."


def activate_license_blob(raw_text, actor=None):
    config = get_license_config()
    payload = verify_license_blob(raw_text, instance_id=config.instance_id)
    status, message = evaluate_payload_status(payload)

    config.license_blob = raw_text
    config.license_id = payload.get("license_id", "")
    config.customer_name = payload.get("customer_name", "")
    config.organization_name = payload.get("organization_name", "")
    config.license_type = payload.get("license_type", "")
    config.valid_from = _date_or_none(payload.get("valid_from"))
    config.valid_until = _date_or_none(payload.get("valid_until"))
    config.support_until = _date_or_none(payload.get("support_until"))
    config.last_verified_at = timezone.now()
    config.status = status
    config.status_message = message
    config.cached_payload = payload
    config.activated_at = timezone.now()
    config.activated_by = actor
    config.save()

    LicenseEvent.objects.create(
        config=config,
        event_type=LicenseEvent.EVENT_ACTIVATE,
        status=status,
        message=message,
        details={"payload": payload},
        actor=actor,
    )
    return config


def refresh_license_status(config=None):
    config = config or get_license_config()
    if not config.license_blob.strip():
        config.status = LicenseConfig.STATUS_MISSING
        config.status_message = "V systému zatím není nahraná žádná licence."
        config.cached_payload = {}
        config.last_verified_at = timezone.now()
        config.save(update_fields=["status", "status_message", "cached_payload", "last_verified_at", "updated_at"])
        return LicenseCheckResult(False, config.status, config.status_message, {})

    try:
        payload = verify_license_blob(config.license_blob, instance_id=config.instance_id)
        status, message = evaluate_payload_status(payload)
        config.license_id = payload.get("license_id", "")
        config.customer_name = payload.get("customer_name", "")
        config.organization_name = payload.get("organization_name", "")
        config.license_type = payload.get("license_type", "")
        config.valid_from = _date_or_none(payload.get("valid_from"))
        config.valid_until = _date_or_none(payload.get("valid_until"))
        config.support_until = _date_or_none(payload.get("support_until"))
        config.status = status
        config.status_message = message
        config.cached_payload = payload
        config.last_verified_at = timezone.now()
        config.save()
        return LicenseCheckResult(status in {LicenseConfig.STATUS_ACTIVE, LicenseConfig.STATUS_GRACE, LicenseConfig.STATUS_SUPPORT}, status, message, payload)
    except Exception as exc:
        config.status = LicenseConfig.STATUS_INVALID
        config.status_message = str(exc)
        config.cached_payload = {}
        config.last_verified_at = timezone.now()
        config.save(update_fields=["status", "status_message", "cached_payload", "last_verified_at", "updated_at"])
        LicenseEvent.objects.create(
            config=config,
            event_type=LicenseEvent.EVENT_REJECT,
            status=config.status,
            message=str(exc),
        )
        return LicenseCheckResult(False, config.status, str(exc), {})


def get_license_modules(payload):
    if payload.get("all_modules"):
        return set(LICENSABLE_MODULE_SLUGS)
    modules = set(payload.get("modules", []) or [])
    if "*" in modules:
        return set(LICENSABLE_MODULE_SLUGS)
    return modules


def is_license_enforced():
    return bool(getattr(settings, "LICENSE_ENFORCEMENT", not settings.DEBUG))


def is_license_operational(status):
    return status in {LicenseConfig.STATUS_ACTIVE, LicenseConfig.STATUS_GRACE, LicenseConfig.STATUS_SUPPORT}


def is_module_licensed(slug):
    if slug not in LICENSABLE_MODULE_SLUGS:
        return True
    if not is_license_enforced():
        return True
    result = refresh_license_status()
    if not is_license_operational(result.status):
        return False
    return slug in get_license_modules(result.payload)


def get_license_footer_context():
    result = refresh_license_status()
    config = get_license_config()
    tone_map = {
        LicenseConfig.STATUS_ACTIVE: ("success", "fas fa-shield-alt"),
        LicenseConfig.STATUS_SUPPORT: ("warning", "fas fa-life-ring"),
        LicenseConfig.STATUS_GRACE: ("warning", "fas fa-hourglass-half"),
        LicenseConfig.STATUS_EXPIRED: ("danger", "fas fa-ban"),
        LicenseConfig.STATUS_INVALID: ("danger", "fas fa-triangle-exclamation"),
        LicenseConfig.STATUS_MISSING: ("neutral", "fas fa-key"),
    }
    tone, icon = tone_map.get(result.status, ("neutral", "fas fa-key"))
    payload = result.payload or config.cached_payload or {}
    module_count = len(get_license_modules(payload))
    valid_until = payload.get("valid_until") or serialize_date(config.valid_until)
    warning_message = ""
    warning_tone = "warning"
    if config.valid_until:
        days_remaining = (config.valid_until - timezone.localdate()).days
        if 0 <= days_remaining <= 30:
            warning_message = f"Licence vyprší za {days_remaining} dní. Doporučujeme prodloužení bez odkladu."
        elif result.status == LicenseConfig.STATUS_GRACE:
            warning_message = "Licence je v ochranné lhůtě. Po jejím konci zůstane admin přístupný jen superadminovi."
        elif result.status in {LicenseConfig.STATUS_EXPIRED, LicenseConfig.STATUS_INVALID, LicenseConfig.STATUS_MISSING}:
            warning_message = "Licence není provozně platná. Licencované moduly jsou uzavřené a admin bude omezený."
            warning_tone = "danger"
    return {
        "status": result.status,
        "status_label": dict(LicenseConfig.STATUS_CHOICES).get(result.status, result.status),
        "message": result.message,
        "tone": tone,
        "icon": icon,
        "customer_name": payload.get("customer_name") or config.customer_name,
        "valid_until": valid_until,
        "valid_until_display": format_czech_date(valid_until),
        "support_until": payload.get("support_until") or serialize_date(config.support_until),
        "module_count": module_count,
        "enforced": is_license_enforced(),
        "warning_message": warning_message,
        "warning_tone": warning_tone,
    }


def get_license_summary_cards():
    result = refresh_license_status()
    payload = result.payload or {}
    modules = sorted(get_license_modules(payload))
    return {
        "status": result.status,
        "status_label": dict(LicenseConfig.STATUS_CHOICES).get(result.status, result.status),
        "message": result.message,
        "payload": payload,
        "modules": modules,
        "is_operational": is_license_operational(result.status),
        "instance_id": str(get_license_config().instance_id),
        "enforced": is_license_enforced(),
    }


def is_admin_superadmin_only_mode():
    if not is_license_enforced():
        return False
    result = refresh_license_status()
    return result.status in {
        LicenseConfig.STATUS_EXPIRED,
        LicenseConfig.STATUS_INVALID,
        LicenseConfig.STATUS_MISSING,
    }


def build_signed_license(payload, private_key_path):
    private_key_bytes = Path(private_key_path).read_bytes()
    private_key = serialization.load_pem_private_key(private_key_bytes, password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("Soukromý klíč musí být typu Ed25519.")
    signature = private_key.sign(canonicalize_payload(payload))
    return {
        "version": 1,
        "payload": payload,
        "signature": base64.b64encode(signature).decode("ascii"),
    }


def generate_keypair(output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_path = output_dir / "private_key.pem"
    public_path = output_dir / "public_key.pem"

    private_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_path, public_path


def can_issue_local_license():
    return get_private_key_path().exists()


def issue_local_license(
    *,
    license_id,
    customer_name,
    organization_name="",
    license_type="annual-onprem",
    valid_from=None,
    valid_until=None,
    support_until=None,
    grace_until=None,
    modules=None,
    allowed_terminals=1,
    notes="",
    actor=None,
):
    config = get_license_config()
    payload = {
        "license_id": license_id,
        "customer_name": customer_name,
        "organization_name": organization_name,
        "license_type": license_type,
        "instance_id": str(config.instance_id),
        "valid_from": serialize_date(valid_from),
        "valid_until": serialize_date(valid_until),
        "support_until": serialize_date(support_until),
        "grace_until": serialize_date(grace_until),
        "modules": list(modules or []),
        "allowed_terminals": allowed_terminals,
        "notes": notes,
    }
    signed = build_signed_license(payload, get_private_key_path())
    return activate_license_blob(json.dumps(signed, ensure_ascii=False, indent=2), actor=actor)
