import json
from typing import Optional
from io import BytesIO

from reportlab.lib.pagesizes import LETTER
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable

from app.models.export_models import ExportExecutionDTO, ExportEventDTO
from app.db.repositories.executions_repository import ExecutionsRepository
from app.db.repositories.events_repository import EventsRepository
from app.core.logging import get_logger

logger = get_logger(__name__)


class PDFExportService:
    """
    Service to aggregate execution data and generate PDF reports.
    Standardized on ReportLab for robust cross-platform compatibility (Windows/Linux).
    """

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

            # Capture final response or errors for the summary block
            if event_type == "EXECUTION_FINAL_RESPONSE" or status == "completed":
                raw_payload = event.get("raw_payload", {}) or {}
                normalized_payload = event.get("normalized_payload", {}) or {}
                resp = raw_payload.get("response", "") or normalized_payload.get("response", "")
                
                if isinstance(resp, dict):
                    final_response = json.dumps(resp, indent=2)
                elif resp:
                    final_response = str(resp)
                    
            elif status == "failed" and not error_details:
                raw_payload = event.get("raw_payload", {}) or {}
                err = raw_payload.get("response", "") or event.get("summary", "Unknown failure")
                if isinstance(err, dict):
                    error_details = json.dumps(err, indent=2)
                else:
                    error_details = str(err)

        if not final_response and execution.get("status") == "completed":
             final_response = "Execution completed successfully."
        
        return ExportExecutionDTO(
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

    @staticmethod
    async def generate_pdf(
        correlation_id: str, soeid: str, include_raw: bool = False
    ) -> Optional[bytes]:
        """Core PDF generator using ReportLab."""
        dto = await PDFExportService.build_export_dto(correlation_id, soeid, include_raw)
        if not dto:
            return None

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer, 
            pagesize=LETTER,
            rightMargin=50, leftMargin=50,
            topMargin=50, bottomMargin=50
        )
        
        styles = getSampleStyleSheet()
        
        # Custom Styles
        title_style = ParagraphStyle(
            'ReportTitle',
            parent=styles['Title'],
            fontSize=22,
            textColor=colors.HexColor("#003366"),
            spaceAfter=20,
            alignment=1 # Centered
        )
        
        heading_style = ParagraphStyle(
            'SectionHeading',
            parent=styles['Heading2'],
            fontSize=16,
            textColor=colors.HexColor("#00509d"),
            spaceBefore=15,
            spaceAfter=10,
            borderPadding=5
        )

        metadata_label_style = ParagraphStyle(
            'MetaLabel',
            parent=styles['Normal'],
            fontSize=10,
            fontName='Helvetica-Bold',
            textColor=colors.grey
        )

        elements = []

        # Header Area (Logo Placeholder + Title)
        elements.append(Paragraph("AGENTIC MIDDLEWARE", ParagraphStyle('brand', fontSize=10, textColor=colors.grey, letterSpacing=2, alignment=1)))
        elements.append(Paragraph("EXECUTION REPORT", title_style))
        elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#003366"), spaceAfter=20))

        # Metadata Summary Table
        meta_data = [
            [Paragraph("USER ID (SOEID)", metadata_label_style), Paragraph(dto.soeid, styles['Normal'])],
            [Paragraph("SESSION ID", metadata_label_style), Paragraph(dto.session_id, styles['Normal'])],
            [Paragraph("CORRELATION ID", metadata_label_style), Paragraph(dto.correlation_id, styles['Normal'])],
            [Paragraph("RUN STATUS", metadata_label_style), Paragraph(dto.status.upper(), ParagraphStyle('stat', fontName='Helvetica-Bold', textColor=colors.green if dto.status == 'completed' else colors.red if dto.status == 'failed' else colors.black))],
            [Paragraph("START TIME", metadata_label_style), Paragraph(dto.started_at or "N/A", styles['Normal'])],
            [Paragraph("END TIME", metadata_label_style), Paragraph(dto.completed_at or "N/A", styles['Normal'])]
        ]
        
        meta_table = Table(meta_data, colWidths=[1.5*inch, 4.5*inch])
        meta_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LINEBELOW', (0, 0), (-1, -2), 0.5, colors.whitesmoke),
            ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ('TOPPADDING', (0,0), (-1,-1), 8),
        ]))
        elements.append(meta_table)
        elements.append(Spacer(1, 20))

        # User Input Section
        elements.append(Paragraph("REQUEST CONTEXT", heading_style))
        elements.append(Paragraph(dto.request_context, ParagraphStyle('req', parent=styles['Normal'], leftIndent=10, rightIndent=10, backColor=colors.whitesmoke, borderPadding=10)))
        elements.append(Spacer(1, 10))

        # Timeline Section
        elements.append(Paragraph("EXECUTION TIMELINE", heading_style))
        last_idx = len(dto.events) - 1
        for i, event in enumerate(dto.events):
            # Formatted Event Block
            time_part = f"<font color='grey' size='9'>{event.timestamp}</font>"
            summary_part = f"<b>{event.summary}</b>"
            elements.append(Paragraph(f"{time_part} | {summary_part}", styles['Normal']))
            
            # Sub-details
            details = [f"Type: {event.normalized_event_type or event.event_type}"]
            if event.tool_name:
                details.append(f"Tool: {event.tool_name}")
            
            elements.append(Paragraph(" &bull; ".join(details), ParagraphStyle('ev_sub', fontSize=8, leftIndent=15, textColor=colors.grey)))
            
            if i < last_idx:
                elements.append(Spacer(1, 8))

        # Final Outcome Section
        if dto.final_response:
            elements.append(Paragraph("FINAL RESPONSE", heading_style))
            elements.append(Paragraph(dto.final_response, ParagraphStyle('resp', parent=styles['Normal'], backColor=colors.HexColor("#f0f7ff"), borderPadding=10)))

        if dto.error_details:
             elements.append(Paragraph("FAILURE DETAILS", heading_style))
             elements.append(Paragraph(dto.error_details, ParagraphStyle('fail', parent=styles['Normal'], backColor=colors.HexColor("#fff0f0"), borderPadding=10, textColor=colors.darkred)))

        # Footer
        elements.append(Spacer(1, 0.5*inch))
        elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
        elements.append(Paragraph("Generated by Recon Agent Middleware • Internal Use Only", ParagraphStyle('footer', fontSize=8, textColor=colors.grey, alignment=1)))

        doc.build(elements)
        logger.info("Generated polished PDF via ReportLab", correlation_id=correlation_id, soeid=soeid)
        return buffer.getvalue()
