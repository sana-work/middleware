import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from fastapi.testclient import TestClient
from app.main import app
from app.models.export_models import ExportExecutionDTO

client = TestClient(app)

# --- Service Tests ---

@pytest.mark.asyncio
async def test_build_export_dto_success():
    """Verify the DTO builds correctly with timeline and correct final response extraction."""
    from app.services.pdf_export_service import PDFExportService
    
    mock_execution = {
        "soeid": "TEST001",
        "session_id": "session-123",
        "correlation_id": "corr-456",
        "status": "completed",
        "request_context": "Run recon",
        "created_at": "2024-05-01T10:00:00Z",
        "completed_at": "2024-05-01T10:01:00Z",
        "latest_event_type": "EXECUTION_FINAL_RESPONSE"
    }

    mock_events = [
        {
            "event_type": "AGENT_START_EVENT",
            "normalized_event_type": "agent_started",
            "status": "running",
            "summary": "Agent started",
            "timestamp": "2024-05-01T10:00:01Z"
        },
        {
            "event_type": "EXECUTION_FINAL_RESPONSE",
            "normalized_event_type": "agent_completed",
            "status": "completed",
            "summary": "Final response generated",
            "timestamp": "2024-05-01T10:01:00Z",
            "raw_payload": {"response": "All systems nominal. Recon complete."}
        }
    ]

    with patch("app.services.pdf_export_service.ExecutionsRepository.get_execution_by_soeid", new_callable=AsyncMock) as mock_get_exec, \
         patch("app.services.pdf_export_service.EventsRepository.get_events_by_correlation", new_callable=AsyncMock) as mock_get_events:
        
        mock_get_exec.return_value = mock_execution
        mock_get_events.return_value = mock_events

        dto = await PDFExportService.build_export_dto("corr-456", "TEST001")
        
        assert dto is not None
        assert dto.correlation_id == "corr-456"
        assert dto.status == "completed"
        assert len(dto.events) == 2
        
        # Verify ordering is preserved
        assert dto.events[0].event_type == "AGENT_START_EVENT"
        
        # Verify final response extraction
        assert dto.final_response == "All systems nominal. Recon complete."
        assert dto.error_details is None


@pytest.mark.asyncio
async def test_build_export_dto_failed_run():
    """Verify the DTO extracts failure details correctly on a failed run."""
    from app.services.pdf_export_service import PDFExportService
    
    mock_execution = {
        "soeid": "TEST001",
        "session_id": "session-123",
        "correlation_id": "corr-456",
        "status": "failed",
        "request_context": "Run recon",
        "created_at": "2024-05-01T10:00:00Z",
        "completed_at": "2024-05-01T10:00:10Z"
    }

    mock_events = [
        {
            "event_type": "TOOL_ERROR_EVENT",
            "status": "failed",
            "summary": "Timeout waiting for external API",
            "timestamp": "2024-05-01T10:00:10Z",
            "raw_payload": {"response": "API Gateway 504 Timeout"}
        }
    ]

    with patch("app.services.pdf_export_service.ExecutionsRepository.get_execution_by_soeid", new_callable=AsyncMock) as mock_get_exec, \
         patch("app.services.pdf_export_service.EventsRepository.get_events_by_correlation", new_callable=AsyncMock) as mock_get_events:
        
        mock_get_exec.return_value = mock_execution
        mock_get_events.return_value = mock_events

        dto = await PDFExportService.build_export_dto("corr-456", "TEST001")
        
        assert dto is not None
        assert dto.status == "failed"
        assert dto.final_response is None
        assert "API Gateway 504 Timeout" in dto.error_details


@pytest.mark.asyncio
async def test_build_export_dto_not_found():
    """Verify unauthorized or missing execution returns None."""
    from app.services.pdf_export_service import PDFExportService
    
    with patch("app.services.pdf_export_service.ExecutionsRepository.get_execution_by_soeid", new_callable=AsyncMock) as mock_get_exec:
        mock_get_exec.return_value = None
        dto = await PDFExportService.build_export_dto("corr-missing", "TEST-HACKER")
        assert dto is None


# --- Route Tests ---

@patch("app.api.export_routes.PDFExportService.generate_pdf", new_callable=AsyncMock)
def test_export_pdf_route_unauthorized(mock_generate_pdf):
    """404 should be returned if missing execution or wrong SOEID."""
    mock_generate_pdf.return_value = None
    response = client.get(
        "/api/v1/chat/export/pdf/corr-456",
        headers={"X-SOEID": "WRONG-USER"}
    )
    # We rely on the missing data to trigger 404 naturally since the mock isn't patched here
    # assuming Mongo is empty or mock is empty
    assert response.status_code == 404
    assert "Execution not found" in response.json()["detail"]


@patch("app.api.export_routes.PDFExportService.generate_pdf", new_callable=AsyncMock)
def test_export_pdf_route_success(mock_generate_pdf):
    """Correct headers are returned for a successful PDF export."""
    mock_generate_pdf.return_value = b"%PDF-1.4 mock bytes"
    
    response = client.get(
        "/api/v1/chat/export/pdf/corr-777?download=true",
        headers={"X-SOEID": "VALID-USER"}
    )
    
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/pdf"
    assert "attachment; filename=\"agentic_execution_corr-777.pdf\"" in response.headers["Content-Disposition"]
    assert response.content == b"%PDF-1.4 mock bytes"


@patch("app.api.export_routes.PDFExportService.generate_pdf", new_callable=AsyncMock)
def test_export_pdf_route_inline(mock_generate_pdf):
    """If download is false, content disposition should be inline."""
    mock_generate_pdf.return_value = b"%PDF-1.4 mock bytes"
    
    response = client.get(
        "/api/v1/chat/export/pdf/corr-777?download=false",
        headers={"X-SOEID": "VALID-USER"}
    )
    
    assert response.status_code == 200
    assert "inline; filename=\"agentic_execution_corr-777.pdf\"" in response.headers["Content-Disposition"]
