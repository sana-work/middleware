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
from app.db.repositories.executions_repository import ExecutionsRepository
from app.db.repositories.events_repository import EventsRepository
from app.core.logging import get_logger
from app.utils.business_logic import get_business_description

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
            business_desc = get_business_description(
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
    def _consolidate_timeline(events: List[ExportEventDTO]) -> List[Dict[str, Any]]:
        """
        Consolidate raw events into a hierarchical timeline.
        
        Structure:
        [
            {
                "type": "agent",
                "label": "Agent Name — Analysis Started",
                "timestamp": "...",
                "tools": [
                    {
                        "label": "Tool Name (Duration)",
                        "status": "completed|failed|running",
                        "start_ts": "...",
                        "end_ts": "..."
                    }
                ]
            },
            {
                "type": "final",
                "label": "Final response generated",
                "timestamp": "..."
            }
        ]
        """
        hierarchy = []
        current_agent = None
        tool_trackers = {} # correlation_id -> tool_name -> info

        for event in events:
            et = event.event_type

            if et == "AGENT_START_EVENT":
                agent_raw = event.summary.split(": ", 1)[-1] if ": " in event.summary else "Agent"
                from app.models.kafka_events import AGENT_BUSINESS_CONTEXT_MAP
                friendly = AGENT_BUSINESS_CONTEXT_MAP.get(
                    agent_raw.lower().replace(" ", "_"),
                    agent_raw.replace("_", " ").title()
                )
                current_agent = {
                    "type": "agent",
                    "label": f"{friendly} — Analysis Started",
                    "timestamp": event.timestamp,
                    "tools": [],
                    "tool_map": {} # tool_name -> tool_obj
                }
                hierarchy.append(current_agent)

            elif et == "AGENT_COMPLETION_EVENT" or et == "AGENT_ERROR_EVENT":
                # We stay in the context of the last agent for now as tools usually finish before the agent does
                pass

            elif et in ("TOOL_INPUT_EVENT", "TOOL_OUTPUT_EVENT", "TOOL_ERROR_EVENT"):
                if not current_agent:
                    # Fallback for tools called outside of explicit agent start
                    current_agent = {
                        "type": "agent",
                        "label": "System Process",
                        "timestamp": event.timestamp,
                        "tools": [],
                        "tool_map": {}
                    }
                    hierarchy.append(current_agent)
                
                tn = event.tool_name or "unknown_tool"
                if tn not in current_agent["tool_map"]:
                    from app.models.kafka_events import TOOL_BUSINESS_CONTEXT_MAP
                    mapped = TOOL_BUSINESS_CONTEXT_MAP.get(tn, tn.replace("_", " ").title())
                    tool_obj = {
                        "name": tn,
                        "label": mapped,
                        "start_ts": None,
                        "end_ts": None,
                        "status": "running",
                        "call_count": 0
                    }
                    current_agent["tools"].append(tool_obj)
                    current_agent["tool_map"][tn] = tool_obj
                
                tool = current_agent["tool_map"][tn]
                if et == "TOOL_INPUT_EVENT":
                    if not tool["start_ts"]:
                        tool["start_ts"] = event.timestamp
                    tool["call_count"] += 1
                elif et == "TOOL_OUTPUT_EVENT":
                    tool["end_ts"] = event.timestamp
                    tool["status"] = "completed"
                elif et == "TOOL_ERROR_EVENT":
                    tool["end_ts"] = event.timestamp
                    tool["status"] = "failed"

            elif et == "EXECUTION_FINAL_RESPONSE":
                hierarchy.append({
                    "type": "final",
                    "label": "Final response generated",
                    "timestamp": event.timestamp
                })

        # Final cleanup: Calculate durations and labels
        for item in hierarchy:
            if item["type"] == "agent":
                for tool in item["tools"]:
                    duration_str = ""
                    if tool["start_ts"] and tool["end_ts"]:
                        try:
                            start = datetime.fromisoformat(tool["start_ts"].replace("Z", "+00:00"))
                            end = datetime.fromisoformat(tool["end_ts"].replace("Z", "+00:00"))
                            secs = (end - start).total_seconds()
                            duration_str = f" ({secs:.0f}s)" if secs < 60 else f" ({secs / 60:.1f}m)"
                        except (ValueError, TypeError):
                            pass
                    
                    count_str = f" [{tool['call_count']} calls]" if tool["call_count"] > 1 else ""
                    tool["display_label"] = f"{tool['label']}{count_str}{duration_str}"
                
                # We don't need the internal tool_map anymore
                del item["tool_map"]

        return hierarchy

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

        # --- Hierarchical Styles ---
        agent_step_style = ParagraphStyle(
            'AgentStep',
            parent=styles['Normal'],
            fontSize=11,
            fontName='Helvetica-Bold',
            textColor=colors.HexColor("#003366"),
            spaceBefore=10,
            spaceAfter=4,
            leftIndent=0
        )
        
        tool_step_style = ParagraphStyle(
            'ToolStep',
            parent=styles['Normal'],
            fontSize=10,
            fontName='Helvetica-Bold',
            textColor=colors.HexColor("#00509d"),
            leftIndent=20,
            spaceBefore=4,
            spaceAfter=2,
        )

        detail_step_style = ParagraphStyle(
            'DetailStep',
            parent=styles['Normal'],
            fontSize=9,
            textColor=colors.grey,
            leftIndent=40,
            spaceAfter=1,
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

        # --- Hierarchical Investigation Steps ---
        elements.append(Paragraph("INVESTIGATION STEPS", heading_style))
        
        consolidated = PDFExportService._consolidate_timeline(dto.events)

        def format_ts_display(ts: str) -> str:
            if not ts: return ""
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                return dt.strftime("%H:%M:%S")
            except (ValueError, TypeError):
                return ts[:8] if len(ts) > 8 else ts

        for item in consolidated:
            if item["type"] == "agent":
                # 1. Agent Heading
                agent_lbl = f"<font color='#00509d'><b>&#9654;</b></font>  {item['label']}"
                elements.append(Paragraph(agent_lbl, agent_step_style))
                
                # Add bookmark for Agent
                elements.append(Paragraph(f'<a name="{item["label"]}"/>', styles['Normal']))

                for tool in item["tools"]:
                    # 2. Tool Heading (Nested)
                    status_color = "#2d6a4f" if tool["status"] == "completed" else "#cc0000" if tool["status"] == "failed" else "#00509d"
                    icon = "&#10003;" if tool["status"] == "completed" else "&#10007;" if tool["status"] == "failed" else "&#8226;"
                    tool_lbl = f"<font color='{status_color}'><b>{icon}</b></font>  {tool['display_label']}"
                    elements.append(Paragraph(tool_lbl, tool_step_style))

                    # 3. Tool Details (Double Nested)
                    if tool["start_ts"]:
                        elements.append(Paragraph(f"&#8212; Started: {format_ts_display(tool['start_ts'])}", detail_step_style))
                    if tool["end_ts"]:
                        elements.append(Paragraph(f"&#8212; Completed: {format_ts_display(tool['end_ts'])}", detail_step_style))
                    
                    elements.append(Spacer(1, 4))

            elif item["type"] == "final":
                elements.append(Spacer(1, 10))
                final_lbl = f"<font color='#003366'><b>&#9632;</b></font>  {item['label']}"
                elements.append(Paragraph(final_lbl, agent_step_style))
                elements.append(Paragraph(f"<i>Time: {format_ts_display(item['timestamp'])}</i>", detail_step_style))

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
