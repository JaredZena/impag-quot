"""Product image management (R2-backed).

- POST   /products/{product_id}/images        upload one image (multipart)
- DELETE /products/{product_id}/images        remove one image by key
- PUT    /products/{product_id}/images/order  reorder the image list

Images live in the private R2 bucket under product-images/{product_id}/…;
Product.images is a JSON list of object keys — list order is display order,
first key is the primary image. Every upload is normalized to WEBP (EXIF
orientation applied, alpha flattened onto white, longest side <= 1600px).

Admin/internal reads get short-lived presigned URLs (presigned_image_urls);
the storefront feed builds public URLs from R2_PUBLIC_BASE_URL instead
(see routes/storefront.py) because presigned URLs expire.
"""

import io
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from PIL import Image, ImageOps
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from auth import verify_google_token
from models import Product, get_db
from services.r2_storage import (
    delete_file as r2_delete,
    generate_presigned_view_url,
    upload_file as r2_upload,
)

router = APIRouter(
    prefix="/products",
    tags=["product-images"],
    dependencies=[Depends(verify_google_token)],
)

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB
# Pixel cap: a <10MB PNG can still decode to hundreds of MB of RAM
# (decompression bomb) — reject before any full decode happens.
MAX_IMAGE_PIXELS = 40_000_000
MAX_DIMENSION = 1600  # longest side after resize (never upscaled)
WEBP_QUALITY = 82
PRESIGNED_URL_TTL = 3600  # 1 hour


class ImageTooManyPixelsError(Exception):
    pass


class ImageDeleteRequest(BaseModel):
    key: str


class ImageOrderRequest(BaseModel):
    keys: list[str]


def presigned_image_urls(keys: list[str] | None) -> list[dict]:
    """Map R2 object keys to short-lived presigned view URLs.

    Returns [{"key": k, "url": ...}, ...] preserving list order (first =
    primary). Accepts None (column not yet populated) and returns [].
    """
    return [
        {
            "key": k,
            "url": generate_presigned_view_url(k, "image/webp", PRESIGNED_URL_TTL),
        }
        for k in (keys or [])
    ]


def primary_image_url(keys: list[str] | None) -> str | None:
    """Presigned view URL for the primary (first) image, or None if there is none."""
    if not keys:
        return None
    return generate_presigned_view_url(keys[0], "image/webp", PRESIGNED_URL_TTL)


def process_product_image(content: bytes) -> bytes:
    """Normalize an uploaded image to WEBP bytes.

    - applies EXIF orientation (phones store rotation as metadata)
    - flattens alpha onto white and converts to RGB
    - resizes so the longest side is <= MAX_DIMENSION (never upscales)
    - encodes as WEBP quality=WEBP_QUALITY

    Raises PIL.UnidentifiedImageError / OSError on undecodable input and
    ImageTooManyPixelsError on decompression-bomb-sized dimensions.
    """
    img = Image.open(io.BytesIO(content))
    # Image.open is lazy (header only) — check dimensions before decoding.
    if img.width * img.height > MAX_IMAGE_PIXELS:
        raise ImageTooManyPixelsError()
    img = ImageOps.exif_transpose(img)

    # Flatten alpha (PNG/GIF transparency) onto a white background.
    if img.mode in ("RGBA", "LA", "PA") or (
        img.mode == "P" and "transparency" in img.info
    ):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # thumbnail() preserves aspect ratio and never upscales.
    img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="WEBP", quality=WEBP_QUALITY)
    return out.getvalue()


def _get_product_or_404(product_id: int, db: Session) -> Product:
    product = (
        db.query(Product)
        .filter(Product.id == product_id, Product.archived_at.is_(None))
        .first()
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.post("/{product_id}/images")
def upload_product_image(
    product_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload one product image; appended at the end of the display order.

    Deliberately a sync `def`: Pillow decode/resize/encode and the boto3
    upload are blocking CPU/IO work — FastAPI runs sync endpoints in the
    threadpool, keeping the event loop free for other requests.
    """
    product = _get_product_or_404(product_id, db)

    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported image type. Must be one of: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}",
        )

    # Reject oversized uploads from the multipart headers BEFORE reading the
    # body into memory; the post-read check below covers a missing size.
    if file.size is not None and file.size > MAX_IMAGE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Image too large. Maximum size is {MAX_IMAGE_SIZE // (1024 * 1024)}MB",
        )

    content = file.file.read(MAX_IMAGE_SIZE + 1)
    if len(content) > MAX_IMAGE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Image too large. Maximum size is {MAX_IMAGE_SIZE // (1024 * 1024)}MB",
        )

    try:
        webp_bytes = process_product_image(content)
    except ImageTooManyPixelsError:
        raise HTTPException(
            status_code=400,
            detail=f"Image dimensions too large (max {MAX_IMAGE_PIXELS // 1_000_000}MP)",
        )
    except Exception:
        raise HTTPException(status_code=400, detail="File is not a valid image")

    key = f"product-images/{product_id}/{uuid4().hex[:12]}.webp"
    try:
        r2_upload(key, webp_bytes, "image/webp")
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to upload image to storage: {e}"
        )

    # JSON columns don't detect in-place mutation: assign a new list AND flag.
    # Known tradeoff: if this commit fails after the R2 upload succeeded, the
    # WEBP object is orphaned under product-images/{id}/ — harmless, and a
    # periodic listing sweep can reconcile; the reverse order would risk DB
    # rows pointing at missing objects, which is worse.
    product.images = (product.images or []) + [key]
    flag_modified(product, "images")
    db.commit()

    return {
        "success": True,
        "data": {
            "key": key,
            "url": generate_presigned_view_url(key, "image/webp", PRESIGNED_URL_TTL),
        },
        "error": None,
        "message": None,
    }


@router.delete("/{product_id}/images")
def delete_product_image(
    product_id: int,
    request: ImageDeleteRequest,
    db: Session = Depends(get_db),
):
    """Remove one image key from the product, then delete the R2 object."""
    product = _get_product_or_404(product_id, db)

    current = product.images or []
    if request.key not in current:
        raise HTTPException(status_code=404, detail="Image not found on this product")

    product.images = [k for k in current if k != request.key]
    flag_modified(product, "images")
    db.commit()

    # Best-effort R2 cleanup: a missing object must not fail the DB removal.
    try:
        r2_delete(request.key)
    except Exception as e:
        print(f"Warning: failed to delete R2 object {request.key}: {e}")

    return {"success": True, "data": None, "error": None, "message": None}


@router.put("/{product_id}/images/order")
def reorder_product_images(
    product_id: int,
    request: ImageOrderRequest,
    db: Session = Depends(get_db),
):
    """Set the display order. Body keys must be a permutation of the current list."""
    product = _get_product_or_404(product_id, db)

    current = product.images or []
    if sorted(request.keys) != sorted(current):
        raise HTTPException(
            status_code=400,
            detail="keys must be a permutation of the product's current image keys",
        )

    product.images = list(request.keys)
    flag_modified(product, "images")
    db.commit()

    return {
        "success": True,
        "data": {"images": product.images},
        "error": None,
        "message": None,
    }
