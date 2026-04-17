import json
from typing import Optional
from jinja2 import Environment, FileSystemLoader

try:
    from weasyprint import HTML
except ImportError:
    HTML = None

from app.models.export_models import ExportExecutionDTO, ExportEventDTO
from app.db.repositories.executions_repository import ExecutionsRepository
from app.db.repositories.events_repository import EventsRepository
from app.core.logging import get_logger

logger = get_logger(__name__)


class PDFExportService:
    """Service to aggregate execution data and generate PDF reports via WeasyPrint."""

    @staticmethod
    async def build_export_dto(
        correlation_id: str, soeid: str, include_raw: bool = False
    ) -> Optional[ExportExecutionDTO]:
        """Fetch execution and event data from MongoDB, returning an export DTO."""
        execution = await ExecutionsRepository.get_execution_by_soeid(correlation_id, soeid)
        if not execution:
            return None

        events = await EventsRepository.get_events_by_correlation(correlation_id)

        event_dtos = []
        final_response: Optional[str] = None
        error_details: Optional[str] = None

        for event in events:
            event_type = event.get("event_type")
            status = event.get("status")
            
            excerpt = None
            if include_raw:
                excerpt = event.get("raw_payload")

            ev_dto = ExportEventDTO(
                event_type=event_type or "unknown",
                normalized_event_type=event.get("normalized_event_type", ""),
                status=status or "unknown",
                summary=event.get("summary", ""),
                timestamp=event.get("timestamp", ""),
                tool_name=event.get("tool_name"),
                raw_payload_excerpt=excerpt
            )
            event_dtos.append(ev_dto)

            # Capture the final text response for the summary block
            if event_type == "EXECUTION_FINAL_RESPONSE" or status == "completed":
                raw_payload = event.get("raw_payload", {})
                normalized_payload = event.get("normalized_payload", {}) or {}
                
                resp = raw_payload.get("response", "") or normalized_payload.get("response", "")
                if isinstance(resp, dict):
                    final_response = json.dumps(resp, indent=2)
                elif resp:
                    final_response = str(resp)
                    
            # Capture the first failure reason for the summary block
            elif status == "failed" and not error_details:
                raw_payload = event.get("raw_payload", {})
                err = raw_payload.get("response", "") or event.get("summary", "Unknown failure")
                if isinstance(err, dict):
                    error_details = json.dumps(err, indent=2)
                else:
                    error_details = str(err)

        # Fallback if no specific payload contained a response string
        if not final_response and execution.get("status") == "completed":
             final_response = "Execution completed successfully."
        
        dto = ExportExecutionDTO(
            soeid=soeid,
            session_id=execution.get("session_id", ""),
            correlation_id=correlation_id,
            status=execution.get("status", "unknown"),
            request_context=execution.get("request_context", "N/A"),
            started_at=execution.get("created_at"),
            completed_at=execution.get("completed_at"),
            latest_event_type=execution.get("latest_event_type"),
            events=event_dtos,
            final_response=final_response,
            error_details=error_details
        )
        return dto

    @staticmethod
    async def generate_pdf(
        correlation_id: str, soeid: str, include_raw: bool = False
    ) -> Optional[bytes]:
        """Generate PDF bytes for an execution."""
        if HTML is None:
             raise RuntimeError("WeasyPrint is not installed or available.")
             
        dto = await PDFExportService.build_export_dto(correlation_id, soeid, include_raw)
        if not dto:
            return None

        # Load Jinja template and render HTML string
        env = Environment(loader=FileSystemLoader("app/templates"))
        template = env.get_template("execution_report.html")

        html_content = template.render(report=dto.model_dump())
        
        # WeasyPrint renders the raw HTML into a PDF byte array
        pdf_bytes = HTML(string=html_content).write_pdf()
        
        logger.info(
            "Generated PDF execution report", 
            correlation_id=correlation_id, 
            soeid=soeid
        )
        return pdf_bytes
