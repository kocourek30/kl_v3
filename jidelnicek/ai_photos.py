from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass
from io import BytesIO
from uuid import uuid4

import requests
from django.core.files.base import ContentFile
from django.utils import timezone
from django.utils.text import slugify
from PIL import Image

from .models import Jidlo, JidloPhotoProposal


logger = logging.getLogger(__name__)


class FoodPhotoGenerationError(Exception):
    """Raised when photo generation fails for a food item."""


@dataclass
class FoodPhotoResult:
    food_id: int
    food_name: str
    status: str  # "updated" | "skipped" | "failed"
    detail: str = ""


def _build_prompt(food: Jidlo) -> str:
    kind_name = food.druh.nazev if food.druh_id and food.druh else "jídlo"
    return (
        "Photorealistic food photography, single plated meal, natural kitchen lighting, "
        "high detail, no text, no watermark, no logo, no people, clean background. "
        f"Meal type: {kind_name}. Meal name: {food.nazev}. "
        "Style: canteen meal photo suitable for Czech school cafeteria menu."
    )


def _resolve_api_settings() -> tuple[str, str, str]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise FoodPhotoGenerationError("Chybí OPENAI_API_KEY v prostředí.")

    model = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1").strip() or "gpt-image-1"
    size = os.getenv("OPENAI_IMAGE_SIZE", "1024x1024").strip() or "1024x1024"
    quality = os.getenv("OPENAI_IMAGE_QUALITY", "medium").strip() or "medium"
    return model, size, quality


def _generate_with_openai(prompt: str, *, timeout: int = 90) -> bytes:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model, size, quality = _resolve_api_settings()

    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "quality": quality,
        "response_format": "b64_json",
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    response = requests.post(
        "https://api.openai.com/v1/images/generations",
        json=payload,
        headers=headers,
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise FoodPhotoGenerationError(
            f"OpenAI API chyba {response.status_code}: {response.text[:220]}"
        )

    data = response.json().get("data") or []
    if not data:
        raise FoodPhotoGenerationError("OpenAI API nevrátila žádná image data.")

    encoded = data[0].get("b64_json")
    if not encoded:
        raise FoodPhotoGenerationError("OpenAI API nevrátila b64_json.")

    try:
        return base64.b64decode(encoded)
    except Exception as exc:  # pragma: no cover - defensive parse guard
        raise FoodPhotoGenerationError("Nepodařilo se dekódovat image payload.") from exc


def _normalize_image_bytes(image_bytes: bytes) -> bytes:
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    image.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
    output = BytesIO()
    image.save(output, format="JPEG", quality=86, optimize=True)
    return output.getvalue()


def _save_food_photo(food: Jidlo, image_bytes: bytes) -> None:
    safe_slug = slugify(food.nazev)[:60] or f"jidlo-{food.pk}"
    filename = f"jidla/auto/{safe_slug}-{uuid4().hex[:8]}.jpg"
    food.foto.save(filename, ContentFile(image_bytes), save=False)
    food.save(update_fields=["foto"])


def _save_photo_proposal(food: Jidlo, image_bytes: bytes, *, prompt: str, model_name: str) -> JidloPhotoProposal:
    safe_slug = slugify(food.nazev)[:60] or f"jidlo-{food.pk}"
    filename = f"jidla/proposals/{safe_slug}-{uuid4().hex[:8]}.jpg"
    proposal = JidloPhotoProposal(
        jidlo=food,
        prompt=prompt,
        model_name=model_name,
        status=JidloPhotoProposal.STATUS_PENDING,
    )
    proposal.image.save(filename, ContentFile(image_bytes), save=False)
    proposal.save()
    return proposal


def apply_photo_proposal(
    proposal: JidloPhotoProposal,
    *,
    reviewed_by=None,
) -> FoodPhotoResult:
    if proposal.status == JidloPhotoProposal.STATUS_APPLIED:
        return FoodPhotoResult(
            food_id=proposal.jidlo_id,
            food_name=proposal.jidlo.nazev,
            status="skipped",
            detail="Návrh už byl dříve použit.",
        )
    if not proposal.image:
        return FoodPhotoResult(
            food_id=proposal.jidlo_id,
            food_name=proposal.jidlo.nazev,
            status="failed",
            detail="Návrh neobsahuje soubor obrázku.",
        )

    try:
        with proposal.image.open("rb") as image_file:
            image_bytes = image_file.read()

        _save_food_photo(proposal.jidlo, image_bytes)
        proposal.status = JidloPhotoProposal.STATUS_APPLIED
        proposal.reviewed_at = timezone.now()
        proposal.reviewed_by = reviewed_by
        proposal.save(update_fields=["status", "reviewed_at", "reviewed_by"])
        return FoodPhotoResult(
            food_id=proposal.jidlo_id,
            food_name=proposal.jidlo.nazev,
            status="updated",
            detail="Návrh schválen a propsán do karty jídla.",
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Schválení návrhu fotky selhalo pro jídlo %s", proposal.jidlo_id)
        return FoodPhotoResult(
            food_id=proposal.jidlo_id,
            food_name=proposal.jidlo.nazev,
            status="failed",
            detail=f"Schválení selhalo: {exc}",
        )


def reject_photo_proposal(proposal: JidloPhotoProposal, *, reviewed_by=None) -> None:
    proposal.status = JidloPhotoProposal.STATUS_REJECTED
    proposal.reviewed_at = timezone.now()
    proposal.reviewed_by = reviewed_by
    proposal.save(update_fields=["status", "reviewed_at", "reviewed_by"])


def generate_food_photo(
    food: Jidlo,
    *,
    overwrite: bool = False,
    dry_run: bool = False,
    timeout: int = 90,
) -> FoodPhotoResult:
    if food.foto and not overwrite:
        return FoodPhotoResult(food_id=food.pk, food_name=food.nazev, status="skipped", detail="Jídlo už má fotku.")

    if dry_run:
        return FoodPhotoResult(food_id=food.pk, food_name=food.nazev, status="skipped", detail="Dry run.")

    try:
        prompt = _build_prompt(food)
        raw_bytes = _generate_with_openai(prompt, timeout=timeout)
        normalized = _normalize_image_bytes(raw_bytes)
        _save_food_photo(food, normalized)
        return FoodPhotoResult(food_id=food.pk, food_name=food.nazev, status="updated", detail="Fotka vygenerována.")
    except FoodPhotoGenerationError as exc:
        logger.warning("Auto-foto selhalo pro jídlo %s (%s): %s", food.pk, food.nazev, exc)
        return FoodPhotoResult(food_id=food.pk, food_name=food.nazev, status="failed", detail=str(exc))
    except Exception as exc:  # pragma: no cover - defensive top-level guard
        logger.exception("Neočekávaná chyba při auto-fotce jídla %s", food.pk)
        return FoodPhotoResult(food_id=food.pk, food_name=food.nazev, status="failed", detail=f"Neočekávaná chyba: {exc}")


def generate_food_photo_proposal(
    food: Jidlo,
    *,
    overwrite: bool = False,
    dry_run: bool = False,
    timeout: int = 90,
) -> FoodPhotoResult:
    if food.foto and not overwrite:
        return FoodPhotoResult(
            food_id=food.pk,
            food_name=food.nazev,
            status="skipped",
            detail="Jídlo už má fotku.",
        )

    if food.photo_proposals.filter(status=JidloPhotoProposal.STATUS_PENDING).exists():
        return FoodPhotoResult(
            food_id=food.pk,
            food_name=food.nazev,
            status="skipped",
            detail="U jídla už čeká návrh na schválení.",
        )

    if dry_run:
        return FoodPhotoResult(food_id=food.pk, food_name=food.nazev, status="skipped", detail="Dry run.")

    try:
        prompt = _build_prompt(food)
        model_name, _, _ = _resolve_api_settings()
        raw_bytes = _generate_with_openai(prompt, timeout=timeout)
        normalized = _normalize_image_bytes(raw_bytes)
        _save_photo_proposal(food, normalized, prompt=prompt, model_name=model_name)
        return FoodPhotoResult(
            food_id=food.pk,
            food_name=food.nazev,
            status="updated",
            detail="Návrh AI fotky uložen ke schválení.",
        )
    except FoodPhotoGenerationError as exc:
        logger.warning("Auto-foto návrh selhal pro jídlo %s (%s): %s", food.pk, food.nazev, exc)
        return FoodPhotoResult(food_id=food.pk, food_name=food.nazev, status="failed", detail=str(exc))
    except Exception as exc:  # pragma: no cover - defensive top-level guard
        logger.exception("Neočekávaná chyba při tvorbě návrhu AI fotky jídla %s", food.pk)
        return FoodPhotoResult(food_id=food.pk, food_name=food.nazev, status="failed", detail=f"Neočekávaná chyba: {exc}")
