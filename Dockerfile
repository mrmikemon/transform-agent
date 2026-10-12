FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.11-slim

WORKDIR /app

# System dependencies: git for cloning, curl for health check
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

# Install AWS CLI (needed to register botocore service models)
RUN pip install --no-cache-dir awscli

# Install AWS Transform Agent Builder SDK
RUN pip install --no-cache-dir \
    agent-builder-sdk-aws-transform \
    agent-builder-agentic-mcp-aws-transform \
    agent-builder-types-aws-transform \
    agent-builder-mcp-client-aws-transform

# Register botocore service models required by the SDK
RUN SDK_MODELS=$(python -c "from importlib.resources import files; print(files('agent_builder_sdk').joinpath('botocore_models'))") && \
    aws configure add-model --service-name atxagentregistryexternal \
        --service-model "file://${SDK_MODELS}/atxagentregistryexternal/2022-07-26/service-2.json" && \
    aws configure add-model --service-name transformagenticservice \
        --service-model "file://${SDK_MODELS}/transformagenticservice/2018-05-10/service-2.json"

# Copy and install project dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Keep strands-agents at latest
RUN pip install --no-cache-dir --upgrade strands-agents

# Create the MCP server wrapper binary expected by the SDK
RUN mkdir -p /home/amazon/AgentBuilderAgenticMCP/bin && \
    printf '#!/bin/bash\npython -m agent_builder_agentic_mcp "$@"\n' \
        > /home/amazon/AgentBuilderAgenticMCP/bin/agent-builder-agentic-mcp && \
    chmod +x /home/amazon/AgentBuilderAgenticMCP/bin/agent-builder-agentic-mcp

# Copy agent source
COPY orchestrator_cli.py .
COPY tools/ ./tools/
COPY prompts/ ./prompts/

# Working directories for agent state and git clones
RUN mkdir -p /tmp/java_symbol_mapper /tmp/agent_queue

# Verify SDK import
RUN python -c "from agent_builder_sdk.server.agent_runtime_server import AgentRuntimeServer; print('SDK import OK')"

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8080/ping || exit 1

ENTRYPOINT ["python", "orchestrator_cli.py"]
