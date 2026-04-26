"""
SDD Router — FastAPI REST endpoints for spec access.

Gemini-cli execution is handled by the independent gemini_service (port 9001).
This router only provides REST endpoints for spec file access and status.

All endpoints are prefixed with ``/besser_api/sdd/``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from besser.utilities.web_modeling_editor.backend.services.sdd.sdd_service import (
    get_sdd_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/besser_api/sdd", tags=["SDD"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class InstallRequest(BaseModel):
    language: str = Field("es", description="Idioma para templates")


# ---------------------------------------------------------------------------
# REST Endpoints
# ---------------------------------------------------------------------------

@router.get("/status")
async def get_status():
    """Get the current SDD pipeline status and available specs."""
    sdd = get_sdd_service()
    return sdd.get_status()


@router.post("/install")
async def install_sdd(req: InstallRequest):
    """Install cc-sdd skills in the workspace."""
    sdd = get_sdd_service()
    result = await sdd.install_skills(language=req.language)
    status_code = 200 if result["status"] != "error" else 500
    return JSONResponse(content=result, status_code=status_code)


@router.get("/specs")
async def list_specs():
    """List all available specifications."""
    sdd = get_sdd_service()
    return {"specs": sdd.list_specs()}


@router.get("/specs/{spec_name}")
async def get_spec(spec_name: str):
    """Get all files for a specific spec."""
    sdd = get_sdd_service()
    files = sdd.get_all_spec_files(spec_name)
    if not files:
        return JSONResponse(
            status_code=404,
            content={"detail": f"Spec '{spec_name}' no encontrada."},
        )
    return {"spec_name": spec_name, "files": files}


@router.get("/specs/{spec_name}/{filename}")
async def get_spec_file(spec_name: str, filename: str):
    """Get a specific file from a spec."""
    sdd = get_sdd_service()
    result = sdd.get_spec_file_content(spec_name, filename)
    if result is None:
        return JSONResponse(
            status_code=404,
            content={"detail": f"Archivo '{filename}' no encontrado en spec '{spec_name}'."},
        )
    return result


# ---------------------------------------------------------------------------
# Shutdown hook
# ---------------------------------------------------------------------------

async def shutdown_sdd():
    """Shutdown the SDD service (called on app shutdown)."""
    sdd = get_sdd_service()
    await sdd.shutdown()
