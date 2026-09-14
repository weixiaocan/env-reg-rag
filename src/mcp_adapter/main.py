"""Stdio entry point for local Agent hosts."""

from pathlib import Path

from dotenv import load_dotenv

from src.mcp_adapter.server import create_mcp_server
from src.server.runtime import build_query_services


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

services = build_query_services(project_root=PROJECT_ROOT)
mcp = create_mcp_server(
    query_service=services.answer,
    source_locator_service=services.source_lookup,
    evidence_catalog=services.evidence,
)


if __name__ == "__main__":
    mcp.run()
