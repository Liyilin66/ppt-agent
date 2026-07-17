"""Dev-only server: ppt-agent API with the deterministic mock LLM injected."""

import tempfile
from pathlib import Path

import uvicorn

from ppt_agent import api
from ppt_agent.v2.mock import MockLLMClient

api._create_v2_model_client = lambda: MockLLMClient()

data_dir = Path(tempfile.gettempdir()) / "ppt-agent-mock-ui-verify"
app = api.create_app(data_dir)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8766)
