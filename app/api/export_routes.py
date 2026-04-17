from fastapi import APIRouter, Header, Query, HTTPException, Request
from fastapi.responses import Response

from app.services.pdf_export_service import PDFExportService
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/chat/export")

@router.get("/pdf/{correlation_id}")
async def export_execution_pdf(
    request: Request,
    correlation_id: str,
    x_soeid: str = Header(...),
    include_timestamps: bool = Query(True, description="Currently formatting relies on Jinja, param kept for spec alignment"),
    include_raw: bool = Query(False, description="Whether to include raw JSON payload excerpts in the PDF timeline"),
    download: bool = Query(True, description="Whether to force Content-Disposition: attachment for direct download")
):
    """
    Export the full lifetime of an execution to a clean, readable PDF.
    This reconstructs the timeline from MongoDB, it does not rely on transient WebSockets.
    """
    try:
        pdf_bytes = await PDFExportService.generate_pdf(
            correlation_id=correlation_id, 
            soeid=x_soeid, 
            include_raw=include_raw
        )
        
        if not pdf_bytes:
            # Match existing backend behavior of obscuring existence on mismatch
            logger.warning("PDF export denied or missing", correlation_id=correlation_id, soeid=x_soeid)
            raise HTTPException(status_code=404, detail="Execution not found or access denied.")

        headers = {
            "Content-Type": "application/pdf"
        }
        
        # Decide between forcing an immediate download vs letting the browser render it inline
        if download:
            headers["Content-Disposition"] = f'attachment; filename="agentic_execution_{correlation_id}.pdf"'
        else:
            headers["Content-Disposition"] = f'inline; filename="agentic_execution_{correlation_id}.pdf"'

        return Response(content=pdf_bytes, headers=headers)
        
    except HTTPException as he:
        raise he
    except RuntimeError as e:
        logger.error("PDF engine failure", error=str(e))
        raise HTTPException(status_code=500, detail="Cannot generate PDF on this server.")
    except Exception as e:
        logger.error("Unexpected error generating PDF", error=str(e), correlation_id=correlation_id)
        raise HTTPException(status_code=500, detail="An error occurred building the export.")
