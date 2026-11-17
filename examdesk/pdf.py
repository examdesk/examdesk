"""PDFs through PDFium, which allows one thread at a time."""

import io
import threading

import pypdfium2 as pdfium

PdfError = pdfium.PdfiumError
DPI = 150

_lock = threading.Lock()


def page_count(document: bytes) -> int:
    """Raises `PdfError`."""
    with _lock:
        return len(pdfium.PdfDocument(document))


def render_page(document: bytes, number: int) -> bytes:
    with _lock:
        image = pdfium.PdfDocument(document)[number - 1].render(scale=DPI / 72).to_pil()
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()
