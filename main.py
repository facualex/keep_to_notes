import subprocess
import sys
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response

TMP_DIR = Path("tmp")
TMP_DIR.mkdir(exist_ok=True)

MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB
CONVERSION_TIMEOUT = 120  # seconds

app = FastAPI(title="Keep to Notes")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _cleanup(paths: list[Path]) -> None:
    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    content = Path("static/index.html").read_text(encoding="utf-8")
    return HTMLResponse(content=content)


@app.post("/convert")
async def convert(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> Response:
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(
            status_code=400,
            detail="El archivo debe ser un .zip exportado de Google Takeout.",
        )

    # Read and enforce size limit
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="El archivo supera el límite de 200 MB.",
        )

    file_id = uuid.uuid4().hex
    zip_path = TMP_DIR / f"{file_id}.zip"
    enex_path = TMP_DIR / f"{file_id}.enex"
    zip_path.write_bytes(content)

    try:
        result = subprocess.run(
            [
                sys.executable,
                "keep_to_enex.py",
                str(zip_path),
                "--output",
                str(enex_path),
            ],
            capture_output=True,
            text=True,
            timeout=CONVERSION_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        background_tasks.add_task(_cleanup, [zip_path, enex_path])
        raise HTTPException(
            status_code=500,
            detail=f"La conversión superó el límite de {CONVERSION_TIMEOUT} segundos.",
        )
    except Exception as exc:
        background_tasks.add_task(_cleanup, [zip_path, enex_path])
        raise HTTPException(status_code=500, detail=str(exc))

    if result.returncode != 0:
        background_tasks.add_task(_cleanup, [zip_path, enex_path])
        raise HTTPException(
            status_code=500,
            detail=result.stderr.strip() or "Error desconocido durante la conversión.",
        )

    if not enex_path.exists():
        background_tasks.add_task(_cleanup, [zip_path])
        raise HTTPException(
            status_code=500,
            detail="El script finalizó sin generar el archivo de salida.",
        )

    # Read output into memory so we can clean up files immediately
    enex_bytes = enex_path.read_bytes()
    background_tasks.add_task(_cleanup, [zip_path, enex_path])

    return Response(
        content=enex_bytes,
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="keep_export.enex"'},
    )
