"""PDF → PNG rendering (first page) for vision-model input."""

from __future__ import annotations

from pathlib import Path


def pdf_to_png_bytes(
    pdf_path: str | Path, dpi: int = 200, page_index: int = 0
) -> tuple[bytes, str]:
    """Render a PDF page to PNG bytes; returns ``(png_bytes, mime_type)``."""
    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    try:
        if page_index >= doc.page_count:
            page_index = 0
        pix = doc[page_index].get_pixmap(dpi=dpi)
        return pix.tobytes("png"), "image/png"
    finally:
        doc.close()
