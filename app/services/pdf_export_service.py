import json
import re
from typing import Optional, List, Dict, Any
from datetime import datetime
from io import BytesIO

from reportlab.lib.pagesizes import LETTER
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable

from app.models.export_models import ExportExecutionDTO, ExportEventDTO
from app.models.kafka_events import TOOL_BUSINESS_CONTEXT_MAP, AGENT_BUSINESS_CONTEXT_MAP
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
            tool_name = event.get("tool_name")
            agent_name = event.get("agent_name")
            
            excerpt = None
            if include_raw:
                excerpt = event.get("raw_payload")

            # Generate business-friendly description
            business_desc = PDFExportService._get_business_description(
                event_type, tool_name, agent_name
            )

            ev_dto = ExportEventDTO(
                event_type=event_type or "unknown",
                normalized_event_type=event.get("normalized_event_type", ""),
                status=status or "unknown",
                summary=event.get("summary", ""),
                business_description=business_desc,
                timestamp=event.get("timestamp", ""),
                tool_name=tool_name,
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
    def _get_business_description(
        event_type: Optional[str], tool_name: Optional[str], agent_name: Optional[str]
    ) -> Optional[str]:
        """Generate a business-friendly description for a given event."""
        if not event_type:
            return None

        # Tool events: use the business context map or generate from tool name
        if event_type in ("TOOL_INPUT_EVENT", "TOOL_OUTPUT_EVENT", "TOOL_ERROR_EVENT") and tool_name:
            mapped = TOOL_BUSINESS_CONTEXT_MAP.get(tool_name)
            if mapped:
                if event_type == "TOOL_OUTPUT_EVENT":
                    return f"Completed: {mapped}"
                elif event_type == "TOOL_ERROR_EVENT":
                    return f"Failed: {mapped}"
                return mapped
            
            # Fallback: generate readable name from tool_name
            readable = tool_name.replace("_", " ").title()
            if event_type == "TOOL_OUTPUT_EVENT":
                return f"Completed: {readable}"
            elif event_type == "TOOL_ERROR_EVENT":
                return f"Failed: {readable}"
            return f"Executing: {readable}"

        # Agent events
        if event_type in ("AGENT_START_EVENT", "AGENT_COMPLETION_EVENT", "AGENT_ERROR_EVENT") and agent_name:
            friendly = AGENT_BUSINESS_CONTEXT_MAP.get(agent_name, agent_name.replace("_", " ").title())
            if event_type == "AGENT_START_EVENT":
                return f"{friendly} has begun analyzing the request"
            elif event_type == "AGENT_COMPLETION_EVENT":
                return f"{friendly} has finished its analysis"
            elif event_type == "AGENT_ERROR_EVENT":
                return f"{friendly} encountered an issue during analysis"

        if event_type == "EXECUTION_FINAL_RESPONSE":
            return "Investigation complete"

        return None

    @staticmethod
    def _consolidate_timeline(events: List[ExportEventDTO]) -> List[Dict[str, Any]]:
        """
        Consolidate raw events into a concise business-oriented timeline.
        
        - Merges TOOL_INPUT + TOOL_OUTPUT pairs into a single step with duration.
        - Groups repeated calls to the same tool.
        - Keeps agent start/completion and final response as bookends.
        """
        steps: List[Dict[str, Any]] = []
        # Ordered dict to track tool calls in order of first appearance
        tool_order: List[str] = []
        tool_tracker: Dict[str, Dict] = {}

        for event in events:
            et = event.event_type

            # Agent bookend events
            if et == "AGENT_START_EVENT":
                agent_raw = event.summary.split(": ", 1)[-1] if ": " in event.summary else "Agent"
                friendly = AGENT_BUSINESS_CONTEXT_MAP.get(
                    agent_raw.lower().replace(" ", "_"),
                    agent_raw.replace("_", " ").title()
                )
                steps.append({
                    "icon": "START",
                    "label": f"{friendly} — Analysis Started",
                    "timestamp": event.timestamp,
                    "type": "agent_start",
                })

            elif et == "AGENT_COMPLETION_EVENT":
                agent_raw = event.summary.split(": ", 1)[-1] if ": " in event.summary else "Agent"
                friendly = AGENT_BUSINESS_CONTEXT_MAP.get(
                    agent_raw.lower().replace(" ", "_"),
                    agent_raw.replace("_", " ").title()
                )
                steps.append({
                    "icon": "DONE",
                    "label": f"{friendly} — Analysis Complete",
                    "timestamp": event.timestamp,
                    "type": "agent_done",
                })

            elif et == "AGENT_ERROR_EVENT":
                steps.append({
                    "icon": "FAIL",
                    "label": "Agent encountered an error",
                    "timestamp": event.timestamp,
                    "type": "agent_error",
                })

            # Tool input: record start time
            elif et == "TOOL_INPUT_EVENT" and event.tool_name:
                tn = event.tool_name
                if tn not in tool_tracker:
                    tool_order.append(tn)
                    tool_tracker[tn] = {
                        "call_count": 0,
                        "first_start": event.timestamp,
                        "last_end": None,
                        "status": "running",
                    }
                tool_tracker[tn]["call_count"] += 1

            # Tool output: record end time
            elif et == "TOOL_OUTPUT_EVENT" and event.tool_name:
                tn = event.tool_name
                if tn in tool_tracker:
                    tool_tracker[tn]["last_end"] = event.timestamp
                    tool_tracker[tn]["status"] = "completed"

            # Tool error
            elif et == "TOOL_ERROR_EVENT" and event.tool_name:
                tn = event.tool_name
                if tn in tool_tracker:
                    tool_tracker[tn]["last_end"] = event.timestamp
                    tool_tracker[tn]["status"] = "failed"

            # Execution final response
            elif et == "EXECUTION_FINAL_RESPONSE":
                steps.append({
                    "icon": "REPORT",
                    "label": "Final response generated",
                    "timestamp": event.timestamp,
                    "type": "final",
                })

        # Build consolidated tool steps (preserve order of first appearance)
        tool_steps = []
        for tn in tool_order:
            info = tool_tracker[tn]
            mapped = TOOL_BUSINESS_CONTEXT_MAP.get(tn, tn.replace("_", " ").title())
            
            # Calculate duration
            duration_str = ""
            if info.get("first_start") and info.get("last_end"):
                try:
                    start = datetime.fromisoformat(info["first_start"].replace("Z", "+00:00"))
                    end = datetime.fromisoformat(info["last_end"].replace("Z", "+00:00"))
                    secs = (end - start).total_seconds()
                    if secs < 60:
                        duration_str = f" ({secs:.0f}s)"
                    else:
                        duration_str = f" ({secs / 60:.1f}m)"
                except (ValueError, TypeError):
                    pass

            count_str = ""
            if info["call_count"] > 1:
                count_str = f" [{info['call_count']} calls]"
            
            status_icon = "OK" if info["status"] == "completed" else "FAIL"

            tool_steps.append({
                "icon": status_icon,
                "label": f"{mapped}{count_str}{duration_str}",
                "timestamp": info["first_start"],
                "type": "tool",
            })

        # Insert tool steps after first agent_start (index 1 position)
        insert_pos = 1 if steps and steps[0]["type"] == "agent_start" else 0
        for i, ts in enumerate(tool_steps):
            steps.insert(insert_pos + i, ts)

        return steps

    @staticmethod
    def _markdown_to_elements(text: str, styles) -> list:
        """
        Convert a markdown-formatted string into ReportLab flowable elements.
        Handles: ### headings, **bold**, *italic*, bullet points, key:value pairs.
        """
        elements = []

        section_heading_style = ParagraphStyle(
            'ResponseHeading',
            parent=styles['Normal'],
            fontSize=13,
            fontName='Helvetica-Bold',
            textColor=colors.HexColor("#003366"),
            spaceBefore=12,
            spaceAfter=6,
        )
        body_style = ParagraphStyle(
            'ResponseBody',
            parent=styles['Normal'],
            fontSize=10,
            leading=14,
            spaceAfter=6,
        )
        bullet_style = ParagraphStyle(
            'ResponseBullet',
            parent=styles['Normal'],
            fontSize=10,
            leading=14,
            leftIndent=20,
            spaceAfter=4,
        )
        kv_style = ParagraphStyle(
            'KVLine',
            parent=styles['Normal'],
            fontSize=10,
            leading=14,
            leftIndent=10,
            spaceAfter=3,
        )

        # Normalize separators: some responses use " * " as line breaks
        if "\n" not in text and " * " in text:
            text = text.replace(" * ", "\n")

        lines = text.split("\n")
        current_para = []

        def flush_para():
            if current_para:
                joined = " ".join(current_para).strip()
                if joined:
                    joined = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', joined)
                    joined = re.sub(r'(?<!\*)\*([^*]+?)\*(?!\*)', r'<i>\1</i>', joined)
                    elements.append(Paragraph(joined, body_style))
                current_para.clear()

        for line in lines:
            stripped = line.strip()

            if not stripped:
                flush_para()
                continue

            # Heading: ### or ##
            if stripped.startswith("##"):
                flush_para()
                heading_text = stripped.lstrip("#").strip()
                heading_text = re.sub(r'\*\*(.+?)\*\*', r'\1', heading_text)
                elements.append(Spacer(1, 6))
                elements.append(Paragraph(heading_text, section_heading_style))
                continue

            # Bullet point
            if stripped.startswith("- ") or stripped.startswith("* "):
                flush_para()
                bullet_text = stripped[2:].strip()
                bullet_text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', bullet_text)
                bullet_text = re.sub(r'(?<!\*)\*([^*]+?)\*(?!\*)', r'<i>\1</i>', bullet_text)
                elements.append(Paragraph(f"\u2022 {bullet_text}", bullet_style))
                continue

            # Key: Value pattern (e.g. **PACT Case:** 545658)
            kv_match = re.match(r'\*\*(.+?):\*\*\s*(.*)', stripped)
            if kv_match:
                flush_para()
                key = kv_match.group(1)
                val = kv_match.group(2)
                val = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', val)
                elements.append(Paragraph(f"<b>{key}:</b>  {val}", kv_style))
                continue

            # Regular text
            current_para.append(stripped)

        flush_para()
        return elements

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
            alignment=1
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

        # Header
        elements.append(Paragraph("OPSUI - AGENTIC CHAT", ParagraphStyle('brand', fontSize=10, textColor=colors.grey, letterSpacing=2, alignment=1)))
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

        # Request Context
        elements.append(Paragraph("REQUEST CONTEXT", heading_style))
        elements.append(Paragraph(dto.request_context, ParagraphStyle('req', parent=styles['Normal'], leftIndent=10, rightIndent=10, backColor=colors.whitesmoke, borderPadding=10)))
        elements.append(Spacer(1, 10))

        # --- Consolidated Investigation Steps ---
        elements.append(Paragraph("INVESTIGATION STEPS", heading_style))
        
        consolidated = PDFExportService._consolidate_timeline(dto.events)

        step_label_style = ParagraphStyle('StepLbl', parent=styles['Normal'], fontSize=10)

        step_data = []
        for idx, step in enumerate(consolidated, 1):
            # Determine icon/color
            stype = step["type"]
            if stype == "agent_start":
                icon_text = '<font color="#00509d"><b>&#9654;</b></font>'
            elif stype == "agent_done":
                icon_text = '<font color="#2d6a4f"><b>&#10003;</b></font>'
            elif stype == "agent_error":
                icon_text = '<font color="#cc0000"><b>&#10007;</b></font>'
            elif stype == "tool" and "FAIL" in step.get("icon", ""):
                icon_text = '<font color="#cc0000"><b>&#10007;</b></font>'
            elif stype == "tool":
                icon_text = '<font color="#2d6a4f"><b>&#10003;</b></font>'
            elif stype == "final":
                icon_text = '<font color="#003366"><b>&#9632;</b></font>'
            else:
                icon_text = '<font color="grey">&#8226;</font>'
            
            # Format timestamp to HH:MM:SS
            ts = step.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                ts_display = dt.strftime("%H:%M:%S")
            except (ValueError, TypeError):
                ts_display = ts[:8] if len(ts) > 8 else ts
            
            step_data.append([
                Paragraph(str(idx), ParagraphStyle('snum', fontSize=9, alignment=1, textColor=colors.grey)),
                Paragraph(icon_text, ParagraphStyle('sicon', fontSize=11, alignment=1)),
                Paragraph(f"<b>{step['label']}</b>", step_label_style),
                Paragraph(ts_display, ParagraphStyle('sts', fontSize=8, alignment=2, textColor=colors.grey)),
            ])

        if step_data:
            step_table = Table(step_data, colWidths=[0.3*inch, 0.3*inch, 4.2*inch, 1.0*inch])
            step_table.setStyle(TableStyle([
                ('ALIGN', (0, 0), (0, -1), 'CENTER'),
                ('ALIGN', (1, 0), (1, -1), 'CENTER'),
                ('ALIGN', (3, 0), (3, -1), 'RIGHT'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('LINEBELOW', (0, 0), (-1, -2), 0.5, colors.HexColor("#eeeeee")),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
                ('TOPPADDING', (0, 0), (-1, -1), 7),
                ('LEFTPADDING', (2, 0), (2, -1), 8),
            ]))
            elements.append(step_table)
        
        elements.append(Spacer(1, 15))

        # --- Findings & Response (markdown rendered) ---
        if dto.final_response:
            elements.append(Paragraph("FINDINGS &amp; RESPONSE", heading_style))
            elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#00509d"), spaceAfter=10))
            
            response_elements = PDFExportService._markdown_to_elements(dto.final_response, styles)
            elements.extend(response_elements)

        if dto.error_details:
             elements.append(Paragraph("FAILURE DETAILS", heading_style))
             elements.append(Paragraph(dto.error_details, ParagraphStyle('fail', parent=styles['Normal'], backColor=colors.HexColor("#fff0f0"), borderPadding=10, textColor=colors.darkred)))

        # Footer
        elements.append(Spacer(1, 0.5*inch))
        elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
        elements.append(Paragraph("Generated by Recon Agent Middleware &bull; Internal Use Only", ParagraphStyle('footer', fontSize=8, textColor=colors.grey, alignment=1)))

        doc.build(elements)
        logger.info("Generated polished PDF via ReportLab", correlation_id=correlation_id, soeid=soeid)
        return buffer.getvalue()
