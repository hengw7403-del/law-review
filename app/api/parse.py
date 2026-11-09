"""Unauthenticated, stateless document preview for the pre-login phase."""

from typing import List, Optional

from fastapi import APIRouter, File, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.services.parser import MAX_FILE_SIZE, ParseError
from app.services.preview import build_preview


router = APIRouter(prefix="/parse", tags=["parse"])


class ClauseResponse(BaseModel):
    seq: int
    original_no: Optional[str] = None
    content: str
    char_start: int
    char_end: int
    location: str


class PreviewResponse(BaseModel):
    filename: str
    preamble: str
    text_length: int
    clause_count: int
    warnings: List[str]
    clauses: List[ClauseResponse]


@router.post("/preview", response_model=PreviewResponse)
async def preview(file: UploadFile = File(...)):
    try:
        data = bytearray()
        while True:
            chunk = await file.read(64 * 1024)
            if not chunk:
                break
            if len(data) + len(chunk) > MAX_FILE_SIZE:
                raise ParseError("FILE_TOO_LARGE", "单文件不得超过 20 MB", 413)
            data.extend(chunk)
        return await run_in_threadpool(build_preview, file.filename or "", bytes(data))
    except ParseError as exc:
        return JSONResponse(status_code=exc.status_code, content={
            "error": {"code": exc.code, "message": exc.message},
        })
    finally:
        await file.close()
