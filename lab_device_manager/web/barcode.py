from __future__ import annotations

from io import BytesIO

import numpy
import zxingcpp
from PIL import Image, ImageOps, UnidentifiedImageError


MAX_BARCODE_IMAGE_BYTES = 2 * 1024 * 1024
MAX_BARCODE_IMAGE_EDGE = 1600
MAX_BARCODE_IMAGE_PIXELS = 16_000_000


class BarcodeImageError(ValueError):
    pass


def decode_barcode_image(data: bytes) -> dict | None:
    if not data:
        raise BarcodeImageError("image is required")
    if len(data) > MAX_BARCODE_IMAGE_BYTES:
        raise BarcodeImageError("image exceeds 2 MB")
    try:
        with Image.open(BytesIO(data)) as source:
            if source.width * source.height > MAX_BARCODE_IMAGE_PIXELS:
                raise BarcodeImageError("image dimensions are too large")
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGB")
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
        raise BarcodeImageError("image format is invalid") from exc
    image.thumbnail(
        (MAX_BARCODE_IMAGE_EDGE, MAX_BARCODE_IMAGE_EDGE),
        Image.Resampling.LANCZOS,
    )
    barcodes = zxingcpp.read_barcodes(numpy.asarray(image))
    for barcode in barcodes:
        text = str(barcode.text or "").strip()
        if text:
            return {
                "code": text,
                "format": str(barcode.format),
                "content_type": str(barcode.content_type),
            }
    return None
