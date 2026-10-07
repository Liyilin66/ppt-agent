"""Local stdio MCP entry point; deck execution remains in deck_jobs."""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import sys

from mcp.server.fastmcp import Context, FastMCP

from ppt_agent import __version__, deck_jobs
from ppt_agent.runtime import load_dotenv_file

logger = logging.getLogger(__name__)


@dataclass
class MCPContext:
    data_dir: Path
    store: deck_jobs.JobStore


def create_server() -> FastMCP:
    """Load the same environment and data-directory convention as the web app."""
    load_dotenv_file()
    data_dir = Path(os.getenv('PPT_AGENT_DATA_DIR', 'data')).resolve()

    @asynccontextmanager
    async def lifespan(server: FastMCP):
        store = deck_jobs.JobStore(data_dir / 'jobs.sqlite3')
        # JobStore opens short-lived connections per operation. This also ensures
        # the shared schema exists; there is no persistent connection to close.
        store.get_latest_job()
        logger.info('ppt-agent MCP ready: %s', data_dir)
        yield MCPContext(data_dir=data_dir, store=store)

    server = FastMCP('ppt-agent', lifespan=lifespan)

    @server.tool()
    def ping(ctx: Context) -> dict[str, str]:
        """Check the server version and absolute shared data directory."""
        context: MCPContext = ctx.request_context.lifespan_context
        return {'version': __version__, 'data_dir': str(context.data_dir)}

    return server


def run_server() -> None:
    # Only the MCP command reconfigures logging; other CLI commands keep their
    # existing output. Do not redirect stdout: the SDK owns that protocol stream.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, force=True)
    create_server().run(transport='stdio')
