# NGU architecture

Clean-architecture layering introduced without changing runtime behaviour.
Legacy import paths (`app.store`, `app.api`, `app.llm`, …) remain as thin
compatibility shims so CLI, Docker entrypoints, and tests keep working.

## Layers

```
app/
  domain/                 # pure types + ports (no I/O)
    task.py               # TaskRecord, TaskStatus
    ports.py              # TaskRepositoryPort, AgentRunnerPort, LLMGatewayPort
  application/            # use cases
    task_service.py       # create/list/run/cancel + attachment jail
  infrastructure/         # adapters out
    persistence/          # SQLite task store
    llm/                  # llama.cpp runtime + HTTP client (wrapped by models/backends)
    models/               # GGUF catalog (app/models package also holds ModelBackend)
  interfaces/             # adapters in
    http/                 # FastAPI board + REST (+ auth middleware)
    mcp/                  # MCP tool surface
  tools/                  # agent toolbelt (fs, shell, memory, …)
  agent.py / runner.py    # agent loop (application-ish, kept stable)
  cli.py                  # interactive CLI entry (stable path)
  config.py               # settings
```

### Dependency rule

- **domain** depends on nothing inside the app.
- **application** depends on **domain** ports only (injected implementations).
- **infrastructure** implements ports; may use config and OS/network.
- **interfaces** call **application** services and wire infrastructure.
- Compatibility shims at `app/*.py` re-export infrastructure/interfaces so
  existing `from app.store import …` continues to work.

### Task creation path

```
HTTP POST /api/tasks  ─┐
MCP assign_task       ─┼─► TaskService.normalize_attachments
                       │         └─ resolve_attachment_path (UPLOAD_DIR | workspace)
                       └─► TaskRepository.create_task → AgentRunner.submit
```

### Auth boundary

`interfaces.http.auth.api_token_middleware` gates all routes when
`LA_API_TOKEN` is set; `/api/health` stays public for probes.
