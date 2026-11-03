"""Minimal stand-in for schoolsoft-mcp's attachments module.

Only ``guess_content_type`` is needed by the news parser; the upstream
module's PDF/DOCX text extraction is not used by the Home Assistant
integration and is left out to avoid those dependencies.
"""

from __future__ import annotations

import mimetypes


def guess_content_type(filename: str) -> str:
    """Best-effort content-type from filename. Falls back to octet-stream."""
    ctype, _ = mimetypes.guess_type(filename)
    return ctype or "application/octet-stream"
