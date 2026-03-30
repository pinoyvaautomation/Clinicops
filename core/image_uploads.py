from io import BytesIO
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_AVATAR_UPLOAD_BYTES = 1024 * 1024
AVATAR_MAX_DIMENSION = 256
ALLOWED_AVATAR_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}


def validate_avatar_upload(uploaded_file):
    """Reject oversized or unsupported avatar files before they hit storage."""
    if not uploaded_file:
        return uploaded_file

    if uploaded_file.size > MAX_AVATAR_UPLOAD_BYTES:
        raise ValidationError("Avatar images must be 1 MB or smaller.")

    try:
        uploaded_file.seek(0)
        with Image.open(uploaded_file) as image:
            image_format = (image.format or "").upper()
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValidationError("Upload a valid JPG, PNG, or WebP image.") from exc
    finally:
        uploaded_file.seek(0)

    if image_format not in ALLOWED_AVATAR_IMAGE_FORMATS:
        raise ValidationError("Upload a valid JPG, PNG, or WebP image.")

    return uploaded_file


def optimize_avatar_upload(uploaded_file):
    """Resize avatar images to a lightweight square-friendly size for the portal."""
    uploaded_file.seek(0)
    with Image.open(uploaded_file) as image:
        image = ImageOps.exif_transpose(image)
        has_alpha = "A" in image.getbands()
        target_mode = "RGBA" if has_alpha else "RGB"
        if image.mode != target_mode:
            image = image.convert(target_mode)

        image.thumbnail((AVATAR_MAX_DIMENSION, AVATAR_MAX_DIMENSION), Image.Resampling.LANCZOS)

        buffer = BytesIO()
        stem = Path(getattr(uploaded_file, "name", "avatar")).stem or "avatar"

        if has_alpha:
            image.save(buffer, format="PNG", optimize=True)
            extension = "png"
        else:
            image = image.convert("RGB")
            image.save(buffer, format="JPEG", quality=85, optimize=True, progressive=True)
            extension = "jpg"

    buffer.seek(0)
    return ContentFile(buffer.read(), name=f"{stem}.{extension}")


def prepare_avatar_upload(uploaded_file):
    """Centralize avatar validation and optimization for easy future reuse."""
    validate_avatar_upload(uploaded_file)
    return optimize_avatar_upload(uploaded_file)
