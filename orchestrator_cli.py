"""
Entry point for the Java Symbol Mapper Agent.

3-phase workflow:
  1. Clone   — Pull the public Git repository
  2. Confirm — HITL: user validates repo looks correct before full scan
  3. Scan    — Extract symbols from all Java files
  4. Deps    — Build cross-reference dependency map
  5. Output  — Write CSV files and report a summary

The agent is wired to AWS Transform via AgentRuntimeServer from the
agent-builder-sdk-aws-transform toolkit.
"""
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Suppress noisy libraries
for lib in ("urllib3", "botocore", "boto3", "git", "gitdb"):
    logging.getLogger(lib).setLevel(logging.WARNING)

from agent_builder_sdk.server.agent_runtime_server import AgentRuntimeServer

from tools.clone_repo import clone_repository
from tools.java_scanner import scan_java_symbols
from tools.dependency_builder import build_dependency_map
from tools.csv_writer import write_csvs
from tools.summary import get_scan_summary

# ---------------------------------------------------------------------------
# System prompt loader
# ---------------------------------------------------------------------------

PROMPT_PATH = Path(__file__).parent / "prompts" / "system_prompt.md"


def load_system_prompt() -> str:
    if PROMPT_PATH.exists():
        content = PROMPT_PATH.read_text(encoding="utf-8")
        # Strip YAML front-matter if present
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                return parts[2].strip()
        return content
    return (
        "You are the Java Symbol Mapper Agent. "
        "Clone the provided public Git repository, scan all Java source files, "
        "extract symbols and dependencies, and produce CSV output files."
    )


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def agent_factory(mcp_client=None, storage_dir=None):
    """
    Create the Java Symbol Mapper orchestrator.

    Registered with AgentRuntimeServer so AWS Transform can invoke it.
    """
    print("[AGENT_FACTORY] Creating Java Symbol Mapper orchestrator…", flush=True)

    from agent_builder_sdk.agent_factory import create_default_async_orchestrator
    from strands.models.bedrock import BedrockModel
    import botocore.config

    bedrock_config = botocore.config.Config(
        read_timeout=600,
        retries={"max_attempts": 3},
    )

    model = BedrockModel(
        model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        max_tokens=16384,
        streaming=False,
        boto_client_config=bedrock_config,
    )

    custom_tools = [
        clone_repository,
        scan_java_symbols,
        build_dependency_map,
        write_csvs,
        get_scan_summary,
    ]

    system_prompt = load_system_prompt()

    agent = create_default_async_orchestrator(
        mcp_client=mcp_client,
        storage_dir=storage_dir or "/tmp/java_symbol_mapper",
        system_prompt=system_prompt,
        custom_tools=custom_tools,
        model=model,
    )

    print(f"[AGENT_FACTORY] Orchestrator created: {type(agent).__name__}", flush=True)
    return agent


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("Starting Java Symbol Mapper Agent runtime server")
    server = AgentRuntimeServer(
        agent_factory=agent_factory,
        delayed_timeout=3600,
    )
    server.start()
