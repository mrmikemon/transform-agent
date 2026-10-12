# Java Symbol Mapper Agent

An AWS Transform custom agent that clones a public Git repository containing a
Java project, scans all `.java` source files, and produces a set of CSV files
mapping symbols (classes, interfaces, enums, methods, fields) and their
cross-referenced dependencies.

## What it produces

| CSV file | Contents |
|---|---|
| `symbols_classes.csv` | All types: class, interface, enum, annotation — with package, modifiers, superclass, interfaces, line |
| `symbols_methods.csv` | All methods and constructors — with class, return type, parameters, modifiers |
| `symbols_fields.csv` | All fields — with class, type, modifiers |
| `deps_imports.csv` | Every import statement, flagged as project-internal or external |
| `deps_inheritance.csv` | `extends` / `implements` relationships, resolved to project types where possible |
| `deps_usage.csv` | Field-type, parameter-type, and return-type usages of other project types |
| `summary.csv` | Per-file counts of types, methods, and fields |
| `parse_errors.csv` | Files that failed to parse (written only if errors occurred) |

## Agent workflow

```
1. Clone      — clone the public Git repo (depth=1)
2. Confirm    — HITL: user validates the repo looks correct
3. Scan       — parse all .java files with javalang, extract symbol map
4. Deps       — build import/inheritance/usage cross-references
5. Output     — write CSV files, report summary
```

## Prerequisites

1. **AWS CLI** configured with credentials
2. **Docker** or **Finch** installed (targets `linux/arm64`)
3. **Kiro** with the **AWS Transform Agent Toolkit Power** installed
4. **Bedrock model access** — Claude Sonnet 4.5 (`us.anthropic.claude-sonnet-4-5-20250929-v1:0`) enabled in `us-east-1`
5. AWS account **allowlisted** for the AWS Transform composability initiative

## Deploy

### Step 1: Create IAM roles

```bash
aws cloudformation deploy \
  --template-file iam-roles.yaml \
  --stack-name java-symbol-mapper-roles \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1
```

### Step 2: Deploy from Kiro

In Kiro chat:

```
Deploy java-symbol-mapper from .
```

The toolkit builds the ARM64 Docker image, pushes it to ECR, deploys to Bedrock
AgentCore, and registers the agent with AWS Transform (~3-5 minutes).

## Run in AWS Transform

Add the agent to a workspace and start a job:

> Scan the Java project at `https://github.com/some-org/some-java-repo` and
> produce CSV files mapping all symbols and their dependencies.

**Expected output:** Eight CSV files written to the job's artifact store.

## Local development

Install dependencies:

```bash
pip install -r requirements.txt
```

Run a local scan (bypasses AWS Transform / AgentCore):

```python
from tools.clone_repo import clone_repository
from tools.java_scanner import scan_java_symbols
from tools.dependency_builder import build_dependency_map
from tools.csv_writer import write_csvs

# Clone
result = clone_repository("https://github.com/example/my-java-project")

# Scan
scan_java_symbols(result["repo_dir"], result["java_files"])

# Cross-reference
build_dependency_map(result["repo_dir"])

# Write CSVs
write_csvs("/tmp/output")
```

## Architecture

```
orchestrator_cli.py          Agent entry point, AgentRuntimeServer
  └── tools/
       ├── clone_repo.py     git clone (gitpython)
       ├── java_scanner.py   AST parsing (javalang), symbol extraction
       ├── dependency_builder.py  import/inheritance/usage cross-refs
       ├── csv_writer.py     CSV output (stdlib csv module)
       └── summary.py        human-readable stats tool
prompts/
  └── system_prompt.md       Agent persona and phase instructions
agentcore.json               AWS Transform Agent Toolkit manifest
Dockerfile                   ARM64 container definition
iam-roles.yaml               CloudFormation for required IAM roles
requirements.txt             Python dependencies
```

## Limitations

- Only processes `.java` files. Kotlin, Groovy, or Scala in the same repo are skipped.
- Parse accuracy depends on `javalang`. Complex generic expressions or unusual
  syntax may cause parse errors for individual files — those files are logged to
  `parse_errors.csv` and the scan continues.
- Dependency resolution is heuristic (import-based + same-package). Dynamic
  dispatch and reflection are not tracked.
- Test sources (`src/test/`) are excluded by default. Pass `include_tests=true`
  to the `clone_repository` tool to include them.
