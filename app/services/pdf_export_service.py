import json
from typing import Optional
from jinja2 import Environment, FileSystemLoader

try:
    from weasyprint import HTML
except (ImportError, OSError) as e:
    HTML = None
    # Weasyprint requires GTK3 C-libraries (Pango/Cairo/GObject).
    # If missing (especially on Windows), it throws OSError via cffi.

try:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from io import BytesIO
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

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
        """Generate PDF bytes for an execution, with fallback to ReportLab if WeasyPrint is missing."""
        dto = await PDFExportService.build_export_dto(correlation_id, soeid, include_raw)
        if not dto:
            return None

        if HTML is not None:
            try:
                # Primary: WeasyPrint (HTML-to-PDF)
                env = Environment(loader=FileSystemLoader("app/templates"))
                template = env.get_template("execution_report.html")
                html_content = template.render(report=dto.model_dump())
                pdf_bytes = HTML(string=html_content).write_pdf()
                logger.info("Generated PDF via WeasyPrint", correlation_id=correlation_id)
                return pdf_bytes
            except Exception as e:
                logger.error("WeasyPrint rendering failed, trying fallback", error=str(e))

        if REPORTLAB_AVAILABLE:
            # Fallback: ReportLab (Direct PDF generation)
            logger.info("Generating PDF via ReportLab fallback", correlation_id=correlation_id)
            return PDFExportService._generate_pdf_reportlab(dto)

        raise RuntimeError("No PDF generation engine available (WeasyPrint and ReportLab both failed or missing).")

    @staticmethod
    def _generate_pdf_reportlab(dto: ExportExecutionDTO) -> bytes:
        """Fallback PDF generator using ReportLab for environments without GTK3."""
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=LETTER)
        styles = getSampleStyleSheet()
        elements = []

        # Title
        elements.append(Paragraph("Agentic Execution Report (Fallback)", styles['Title']))
        elements.append(Spacer(1, 12))

        # Metadata
        meta_data = [
            ["SOEID:", dto.soeid],
            ["Session ID:", dto.session_id],
            ["Correlation ID:", dto.correlation_id],
            ["Status:", dto.status.upper()],
            ["Started At:", dto.started_at or "N/A"],
            ["Completed At:", dto.completed_at or "N/A"]
        ]
        t = Table(meta_data, colWidths=[120, 350])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.lightgrey),
            ('TEXTCOLOR', (0, 0), (0, -1), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey)
        ]))
        elements.append(t)
        elements.append(Spacer(1, 20))

        # Request
        elements.append(Paragraph("User Request", styles['Heading2']))
        elements.append(Paragraph(dto.request_context, styles['BodyText']))
        elements.append(Spacer(1, 20))

        # Timeline
        elements.append(Paragraph("Execution Timeline", styles['Heading2']))
        for event in dto.events:
            time_str = f"<b>{event.timestamp}</b> - {event.summary}"
            elements.append(Paragraph(time_str, styles['BodyText']))
            detail_str = f"Type: {event.event_type} | Status: {event.status}"
            if event.tool_name:
                detail_str += f" | Tool: {event.tool_name}"
            elements.append(Paragraph(detail_str, ParagraphStyle('sub', fontSize=8, leftIndent=10, textColor=colors.grey)))
            elements.append(Spacer(1, 5))

        # Final Response
        if dto.final_response:
            elements.append(Spacer(1, 15))
            elements.append(Paragraph("Final Response", styles['Heading2']))
            elements.append(Paragraph(dto.final_response, styles['BodyText']))

        # Error Details
        if dto.error_details:
            elements.append(Spacer(1, 15))
            elements.append(Paragraph("Error Details", styles['Heading2']))
            elements.append(Paragraph(dto.error_details, ParagraphStyle('err', textColor=colors.red)))

        doc.build(elements)
        return buffer.getvalue()
