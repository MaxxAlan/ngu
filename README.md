# NGU — Local Long-Horizon Agent & Specialized Model

[![CI](https://github.com/MaxxAlan/ngu/actions/workflows/ci.yml/badge.svg)](https://github.com/MaxxAlan/ngu/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/MaxxAlan/ngu)](https://github.com/MaxxAlan/ngu/releases)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**NGU is evolving from a local LLM agent into a local-first, long-horizon agent system powered by a specialized model of its own.**

The goal is not another chatbot that receives one prompt, produces one answer, and forgets the work. NGU should take complex tasks, understand large bodies of information, plan and execute many steps, preserve decisions and progress, recover after interruption, and finish reliably.

The model may start from an existing open-weight model. The long-term objective is to own the resulting model artifacts, training pipeline, task-specific behavior, memory architecture, evaluation suite, and runtime integration.

> **Project principle:** Optimize for successful long-running work, not for the smallest possible model file. Model size is a deployment trade-off, not the definition of the project.

---

## 1. The problem NGU is trying to solve

Local agents face two competing problems:

1. Small local models are light enough to run, but often fail at planning, tool selection, structured output, multi-step execution, and consistency across a long task.
2. Larger models are more capable, but can be too expensive in RAM/VRAM, too slow, or impractical on the machines where NGU should run.

Increasing a conventional context window alone does not solve this. A model can accept a large prompt and still lose the task objective, forget a decision, repeat completed work, misuse tools, or fail to resume after a process restart.

NGU therefore treats **model capability, context handling, memory, and durable execution as one system**.

### Product goals

| Area | Goal |
|---|---|
| Long tasks | Execute multi-stage work lasting hours or days, not just short chat turns |
| Long context | Work with projects containing approximately 2M–3M tokens of relevant source material |
| Native long context | Research architectures and training that can genuinely process million-token sequences |
| Persistent memory | Preserve facts, decisions, entities, procedures, and task history across sessions |
| Recovery | Resume after process, machine, or service restart without repeating completed work |
| Specialization | Train for planning, tool use, retrieval, state updates, verification, and task completion |
| Model ownership | Produce NGU-branded checkpoints and a reproducible training/evaluation pipeline |
| Hardware flexibility | Run on CPU-only systems, low-VRAM GPUs, stronger consumer GPUs, and servers |
| Deployment | Support quantized local inference and optional remote inference/API deployment |

### What “small specialized model” means here

“Small” means **purpose-built and deployable**, not “must be exactly 1B parameters” or “must fit in a 500 MB file.”

- 1B parameters is a useful experimental starting point, not a hard ceiling.
- A 2B, 3B, or larger checkpoint is acceptable when the gain in task success justifies its runtime cost.
- Quantization, CPU offload, memory placement, and hardware-aware execution should make the model usable on weaker devices.
- Model weights may be larger than originally expected. The primary metric is whether the complete system can finish long tasks reliably on the intended hardware.

---

## 2. Context is not the same as memory or task duration

NGU measures three separate capabilities. They must never be conflated in documentation or benchmarks.

### 2.1 Native context length

The number of tokens the neural model can accept in one model invocation under a defined positional/attention configuration.

**Research target: 2M–3M tokens.**

This is an ambitious target. It must be demonstrated with the actual model, tokenizer, runtime, memory use, and long-context evaluation. A large configured position limit by itself is not proof of usable long context.

### 2.2 Effective project context

The total information available to a task through the model's current prompt, retrieved passages, structured memory, event history, and stored artifacts.

**Product target: 2M–3M tokens or more per project/task corpus.**

This can be supported before the neural model has true 3M native attention, using hierarchical memory and retrieval. However, effective context must not be advertised as native context.

### 2.3 Long-horizon execution

The ability to keep a task alive across many model calls, tool executions, checkpoints, and restarts.

A task can last hours or days even when each individual model invocation uses a much smaller context window. NGU must preserve the task's objective, current state, completed work, unresolved questions, and next action.

### Required terminology

| Term | Meaning | How NGU reports it |
|---|---|---|
| Native context | Tokens supplied to one model invocation | Actual supported and tested sequence length |
| Effective context | Source material accessible through memory/retrieval | Corpus size, retrieval coverage, recall and precision |
| Working context | Information assembled for the current model call | Actual tokens sent to the model |
| Persistent memory | Information retained beyond one model call or session | Storage size, memory types, update and retrieval quality |
| Task horizon | Duration and number of actions in a task | Runtime, steps, restarts, completion rate |

A system that indexes a 3M-token book and retrieves 8K relevant tokens has 3M-token **effective corpus access**, not a 3M-token native context window. Both are useful, but they are different engineering achievements.

---

## 3. Target architecture

NGU consists of four cooperating layers. The model is important, but it is not expected to act as the database, scheduler, file system, and recovery mechanism by itself.

~~~text
                         USER / API / CLI / MCP
                                  |
                                  v
                      LONG-HORIZON TASK RUNTIME
                                  |
              +-------------------+-------------------+
              |                   |                   |
              v                   v                   v
        TASK PLANNER         STATE MANAGER       TOOL EXECUTOR
        Goal / subgoals      Task graph          Files / shell
        Constraints          Progress            Web / MCP
        Acceptance tests     Decisions           External tools
              |                   |                   |
              +-------------------+-------------------+
                                  |
                                  v
                        CONTEXT & MEMORY ENGINE
                                  |
          +-----------------------+-----------------------+
          |             |           |          |          |
          v             v           v          v          v
      Working       Episodic     Semantic   Procedural  Artifact
      Memory        Memory       Memory     Memory     Store
          |             |           |          |          |
          +-----------------------+-----------------------+
                                  |
                                  v
                       SPECIALIZED MODEL RUNTIME
                                  |
                    +-------------+-------------+
                    |             |             |
                    v             v             v
                Base/NGU      Long-context   Model/tool
                 Model        architecture    adapters
                    |             |             |
                    +-------------+-------------+
                                  |
                                  v
                    VERIFY / EVALUATE / UPDATE STATE
                                  |
                                  +----> checkpoint
                                  +----> memory update
                                  +----> next action
                                  +----> final result
~~~

### 3.1 Specialized neural model

The model should be trained to perform the work NGU actually needs:

- Understand and restate the task objective and constraints.
- Break large tasks into executable subgoals.
- Decide when to inspect, retrieve, reason, call a tool, verify, or stop.
- Produce reliable structured tool requests.
- Read tool results and detect failures.
- Maintain consistency between prior decisions and new information.
- Update structured task state and memory.
- Recognize uncertainty, missing evidence, and conflicting information.
- Verify deliverables against explicit completion criteria.

This is not a requirement to build a general-purpose model that knows everything. The model should be excellent at the target families of tasks.

### 3.2 Long-context architecture research

The preferred research direction is a **hybrid long-sequence model**, rather than assuming that a conventional dense Transformer is the only option.

Candidate building blocks:

- **Local/dense attention:** accurate token-to-token interaction in the current working region.
- **Sparse/retrieval attention:** selective access to important positions in long sequences.
- **Linear attention or SSM blocks:** efficient processing of long streams and recurrent state.
- **Memory/compression modules:** preserve useful information from earlier segments.
- **Position and length generalization:** train and evaluate beyond the short sequences used in early experiments.

A possible experimental layout:

~~~text
Tokens / Segments
       |
       v
Embedding + Position Mechanism
       |
       +---- Local Attention ------------------+
       |                                       |
       +---- Linear Attention / SSM -----------+--> Fusion
       |                                       |
       +---- Sparse Long-range Retrieval ------+
                                               |
                                      Memory / State Update
                                               |
                                        Task-conditioned
                                          Generation
~~~

The exact mix of Transformer, SSM, sparse attention, and neural memory is an experimental question. Do not combine techniques only because they are fashionable. Keep implementations replaceable and compare them on the same NGU benchmark.

### 3.3 Hierarchical persistent memory

Memory is not just a vector database and is not just a longer prompt.

| Memory type | Stores | Example |
|---|---|---|
| Working | Current subgoal, immediate evidence, active constraints | “Inspect these three files before editing” |
| Episodic | What happened during previous task steps | “Test X failed after change Y” |
| Semantic | Stable facts, entities, concepts, relationships | Project architecture, character identities |
| Procedural | Proven workflows and reusable task methods | Build, test, deploy, translate, review procedures |
| Artifact | Large files and outputs, with source references | Reports, source files, chapters, datasets |
| Task state | Goal, plan, status, decisions, blockers, next action | The durable execution record |

Every important memory record should support provenance and lifecycle metadata:

- Originating task, file, URL, or tool result.
- Source span or exact artifact reference when applicable.
- Timestamp and last verification time.
- Confidence or verification status.
- Entity and relationship links where relevant.
- Superseded or conflicting versions.
- Retention and deletion rules.

Do not silently turn a model guess into a permanent fact. Memory writes should be validated, attributable, and reversible.

### 3.4 Long-horizon execution runtime

The runtime owns task continuity. It must not depend on a model remembering everything in its chat transcript.

A durable task record should contain:

- Task ID, objective, constraints, and success criteria.
- Plan or dependency graph with stable subtask IDs.
- Status of each subtask.
- Completed actions and their outputs.
- Decisions and their rationale summaries.
- Files/artifacts created or modified.
- Open questions, blockers, and errors.
- Current checkpoint and next recommended action.
- Model/runtime version and relevant configuration.

The execution loop:

~~~text
Create Task
    |
    v
Plan + Acceptance Criteria
    |
    v
Persist Initial State
    |
    v
Select Next Runnable Subtask
    |
    v
Build Context from State + Memory + Sources
    |
    v
Model Decision
    |
    v
Execute Tool / Produce Artifact
    |
    v
Validate Result
    |
    +---- failed ----> Diagnose / Retry / Replan
    |
    +---- success ---> Persist Event + Update Memory
                              |
                              v
                     More work remaining?
                         /         \
                       yes          no
                        |            |
                   Next Subtask   Final Verification
                                     |
                                     v
                                  Complete
~~~

The task must be resumable from the durable state, not merely from a raw list of previous chat messages.

---

## 4. Model strategy: own the resulting model, do not insist on training from zero

NGU may use existing open-weight models as a foundation. This is intentional: the project should spend effort on capabilities and differentiation, not reimplementing every foundation-model component unnecessarily.

### 4.1 Candidate starting points

Candidate backbones must be benchmarked rather than selected by parameter count or popularity alone.

| Candidate | Why evaluate it | Important caveat |
|---|---|---|
| Qwen small models | Small checkpoints, broad tooling ecosystem, useful baseline for local agent behavior | Must measure actual tool reliability and long-task performance |
| MiniCPM5-1B | Small model candidate with published deployment and tool-calling recipes | Standard model architecture; does not automatically provide million-token context |
| MiniCPM-SALA | Strong reference for sparse + linear hybrid attention and million-token modeling | Treat as an architecture/training reference, not a ready-made low-VRAM 1B checkpoint |
| Mamba-2 / Mamba-3 | SSM research path for long-sequence processing | Recurrent efficiency does not guarantee exact retrieval or strong agent/tool behavior |
| Other open-weight models | May provide better language, code, or planning capabilities | Check license, tokenizer, runtime support, and adaptation cost |

The benchmark decides the base. NGU is not committed to Qwen, MiniCPM, Mamba, or any single vendor.

### 4.2 Recommended training path

~~~text
Open-weight Base Checkpoint
           |
           v
Baseline Evaluation
           |
           v
Continued Pretraining / Domain Adaptation
           |
           v
Agent SFT
  - task planning
  - structured state updates
  - tool selection and tool arguments
  - error recovery
  - evidence-grounded completion
           |
           v
Preference / Outcome Optimization
           |
           v
Long-context Adaptation
           |
           v
Distillation + Quantization
           |
           v
NGU Specialized Checkpoint
~~~

Training stages:

1. **Baseline first:** measure tool-call validity, planning, task completion, recovery, and long-context behavior before modifying the model.
2. **Continued pretraining:** adapt the base model to the target data and task distribution where useful.
3. **Supervised fine-tuning:** train on complete task trajectories, including tool inputs, tool outputs, state updates, corrections, and verified outcomes.
4. **Outcome/preference training:** reward completed, correct, grounded work rather than verbosity or plausible-looking reasoning.
5. **Long-context training:** progressively increase sequence length and train on tasks that require information from distant positions.
6. **Distillation:** optionally transfer behavior from a stronger teacher into a smaller deployable student.
7. **Quantization and deployment:** compare Q8, Q6, Q5, Q4, and other suitable formats using the same task benchmark.

Training from scratch remains an option for a later architecture experiment. It is not the default starting point because a capable pretrained base can reduce data and compute requirements substantially.

### 4.3 What the model should learn

Training data should be task trajectories, not only prompt/answer pairs.

A useful trajectory contains:

- Initial user objective and constraints.
- Plan and subtask decomposition.
- Relevant context and retrieved evidence.
- Tool call with validated arguments.
- Tool result, including errors.
- Model's interpretation of the result.
- State/memory update.
- Verification result.
- Final outcome and evaluator feedback.

Include negative examples: repeated tool calls, fabricated completion, ignored errors, stale memory, contradictory decisions, and premature stopping. Train the model to recover rather than merely imitate successful conversations.

---

## 5. Research projects and implementations to learn from

These projects cover different parts of the problem. They are references and possible dependencies, not a requirement to merge all their code into NGU.

| Project / paper | Relevant contribution | Role in NGU |
|---|---|---|
| [MiniCPM-SALA](https://github.com/OpenBMB/MiniCPM/tree/main/minicpm_sala) | Sparse Attention + Linear Attention hybrid; its published recipe targets million-token context | Main long-context architecture reference |
| [Titans paper](https://research.google/pubs/titans-learning-to-memorize-at-test-time/) | Neural long-term memory and test-time memorization; reports experiments beyond 2M context | Main neural-memory research reference |
| [Titans PyTorch implementation](https://github.com/lucidrains/titans-pytorch) | Community implementation useful for experimentation | Prototype/reference code; validate against paper and tests |
| [Mamba repository](https://github.com/state-spaces/mamba) | Selective State Space Models, Mamba-2 and Mamba-3 research implementations | SSM blocks and sequence-modeling experiments |
| [Chidori](https://github.com/ThousandBirdsInc/chidori) | Durable, replayable, resumable agent execution | Reference for task runtime, checkpoints, and recovery |
| [Infini-attention paper](https://arxiv.org/abs/2404.07143) | Combines local attention and compressed memory for long-context processing | Alternative memory/attention design to benchmark |

### Important interpretation

- MiniCPM-SALA's published 1M-context results are not evidence that a 1B model will run 1M tokens on a 4GB GPU. Its official material describes high-memory GPU test configurations. NGU must reproduce its own hardware measurements.
- Titans is a research architecture, not a drop-in persistent task database or a complete agent runtime.
- Mamba's linear-time sequence processing is attractive, but exact recall, copying, tool-use reliability, and long-range factual consistency still need dedicated evaluation.
- Chidori solves durable execution concerns, not the neural-model problem. NGU can adopt its execution ideas without replacing the current Python application wholesale.
- No single one of these projects provides the entire NGU product.

---

## 6. Current repository: what exists today

The current codebase is a useful execution shell. It should be evolved, not discarded just to start over.

### Existing capabilities

| Area | Current implementation |
|---|---|
| Application | Python 3.11+, CLI, FastAPI web board, SSE events, MCP server |
| Inference | llama.cpp server with GGUF model discovery and on-demand model loading |
| Models | Qwen2.5-Coder 1.5B/3B/7B, Qwen2.5-VL 3B, Gemma 4 E4B, Nemotron Nano 4B (see scripts/models.json) |
| Task storage | SQLite with WAL; task status, messages, steps, results, errors, and attachments |
| Execution | Queue + worker + agent tool loop |
| Tools | Filesystem, shell, search/web, and other registered tools |
| Memory | SQLite FTS5 keyword-search memory with save/search/list/delete operations |
| Buffering | Response/tool-result cache and heuristic conversation compression |
| Recovery | Running task messages can be checkpointed and queued for resume after restart |
| Novel workflow | Plan → write → review → retry → persist, with story.meta.json |
| Deployment | Windows setup script, Docker, GHCR workflow, Kubernetes manifests |

Useful code entry points:

- **app/agent.py** — model/tool loop and message handling.
- **app/runner.py** — task queue, execution, checkpoint/resume, events.
- **app/store.py** — SQLite task persistence.
- **app/tools/memory.py** — current persistent keyword memory.
- **app/buffer_station.py** — cache and heuristic context compression.
- **app/novel.py** — long-form writing workflow.
- **app/llama_runtime.py** and **app/llm.py** — local model runtime and API client.
- **app/router.py** — model routing.
- **app/tools/** — tools and tool registry.
- **scripts/models.json** — model download manifest.
- **.config** — runtime and model settings.

### Current limitations to address

This is an audit of the current main branch, not a claim that these features do not exist in any future branch.

1. **The agent loop is step-bounded.** The default LA_AGENT_MAX_STEPS is 12. A task that needs more work can stop because of the loop budget even if the task is not complete.
2. **Conversation retention is heuristic.** The agent compresses older messages and trims the history to a head/tail window. The buffer compressor uses character lengths, not tokenizer-accurate token budgets or semantic importance.
3. **Current memory is lightweight keyword memory.** SQLite FTS5 is useful and avoids a separate embedding model, but it is not yet a hybrid semantic/lexical retrieval system, temporal knowledge graph, or neural memory.
4. **Checkpointing is message-oriented.** The runner stores conversation messages and steps, and saves a progress note periodically. It does not yet maintain a complete durable task graph with explicit dependencies, artifact lineage, typed decisions, and verified acceptance criteria.
5. **The novel workflow is specialized but isolated.** Its plan/write/review/retry pattern is a good proof of concept; the same lifecycle should become reusable for coding, research, data work, and other long tasks.
6. **Tool-use behavior depends heavily on the selected model.** Current small local models can emit malformed tool requests, repeat calls, misunderstand results, or claim completion without adequate verification.
7. **The current configured context is not million-token context.** The .config file currently sets LA_LLAMA_CTX=32768 with an 8192 fallback. This is a conventional local inference configuration, not the 2M–3M target.
8. **Model/runtime coupling needs an abstraction boundary.** The current path is GGUF through llama.cpp. Research architectures may require Transformers, vLLM, SGLang, custom kernels, or a different export/runtime path. Do not assume every architecture can be exported to GGUF or run through the existing binary.

### Existing settings are not product limits

The current values in .config—context, max steps, token budget, concurrency, and model aliases—describe the implementation today. They are not the target architecture and should become hardware/task profiles rather than permanent global limits.

---

## 7. Development roadmap

The work should be incremental. Keep the CLI, web board, MCP surface, tools, and working deployment usable while replacing the weak internals.

### Phase 0 — Baseline and instrumentation

- Freeze a known-good revision of the current agent.
- Create reproducible task datasets and test fixtures.
- Record model name, quantization, runtime, prompt/context size, steps, tool calls, elapsed time, RAM, VRAM, and final outcome.
- Build benchmark tasks that expose current failures: repeated tool calls, invalid arguments, ignored errors, lost constraints, and false completion.

**Exit condition:** every proposed improvement can be compared against a repeatable baseline.

### Phase 1 — Durable task state

- Separate task state from the chat transcript.
- Introduce stable task and subtask IDs, dependencies, status transitions, acceptance criteria, and artifact references.
- Store execution events and checkpoints transactionally.
- Resume from the last valid state after process or machine restart.
- Make tool execution idempotent where possible; record side effects to prevent duplicate work.

**Exit condition:** a multi-stage task can be interrupted and resumed without losing its objective or repeating completed side effects.

### Phase 2 — Context and memory engine

- Add tokenizer-aware context budgeting.
- Store source documents as addressable chunks with source IDs and offsets.
- Combine lexical search, vector retrieval, metadata filters, and entity/relationship lookup.
- Add episodic, semantic, procedural, and working memory types.
- Add memory provenance, conflict detection, expiration, updates, and deletion.
- Build context from the current subtask instead of blindly appending the entire conversation.
- Preserve important facts and decisions through verified summaries; never replace source artifacts with summaries.

**Exit condition:** NGU can work over a 2M–3M-token project corpus and retrieve evidence from distant sections with measured recall and precision.

### Phase 3 — Specialized-model baseline

- Evaluate several open-weight base models using the same NGU agent benchmark.
- Choose a base based on task success, tool calling, language/code quality, context behavior, inference cost, license, and runtime compatibility.
- Train task-trajectory SFT data.
- Add preference/outcome optimization and optional teacher distillation.
- Keep model training and agent runtime independently versioned.

**Exit condition:** an NGU-specialized checkpoint beats the selected base model on the project benchmark, not just on generic chat prompts.

### Phase 4 — Long-context model research

- Establish a 32K baseline, then progress through 128K, 512K, 1M, and 2M+ sequences.
- Compare sparse/linear hybrid attention, SSM blocks, recurrent memory, and compression-based attention.
- Measure prefill time, decode speed, peak memory, retrieval accuracy, and degradation by evidence position.
- Train on tasks where relevant facts are far apart; do not rely solely on synthetic “needle” tests.
- Keep native context results separate from memory-assisted corpus access.

**Exit condition:** the model demonstrates reliable long-range retrieval and reasoning at the claimed native sequence length within documented hardware limits.

### Phase 5 — Hardware-aware deployment

- Maintain profiles for CPU-only, low-VRAM GPU, mid-range GPU, and server.
- Benchmark Q8/Q6/Q5/Q4 and suitable mixed-precision formats.
- Support CPU/GPU layer placement and memory offload where the backend allows it.
- Use quantized KV/state caches only where supported and quality-tested.
- Swap specialist models on demand rather than requiring every model to remain resident.
- Keep a remote inference endpoint as an optional path, not a mandatory dependency.

**Exit condition:** users can choose a device profile and understand the quality, speed, and memory trade-offs before starting a task.

### Phase 6 — Consolidation and continual improvement

- Collect task outcomes, evaluator feedback, corrections, and failure cases.
- Curate them into versioned training datasets.
- Run offline fine-tuning/distillation between model releases.
- Promote a new checkpoint only after regression tests.
- Keep online memory updates separate from weight updates; do not mutate model weights on every task by default.

**Exit condition:** each model release has reproducible training data lineage, evaluation results, and a rollback path.

---

## 8. Evaluation: success means finishing the task

NGU must not optimize only for tokens per second, model size, or benchmark scores unrelated to real work.

### Long-context evaluation

- Evidence retrieval at the beginning, middle, and end of a long document.
- Multi-hop questions requiring evidence from distant sections.
- Entity and relationship consistency across a long corpus.
- Timeline and version conflict resolution.
- Distractor-heavy retrieval.
- Exact quotation/source-span attribution.
- Long-context degradation as sequence length increases.

### Long-horizon evaluation

- Complete tasks with 20, 50, 100, and more execution steps.
- Interrupt the process at random points and restart it.
- Verify no completed side effect is performed twice.
- Verify constraints and decisions survive multiple checkpoints.
- Inject tool failures and check recovery/replanning.
- Confirm the agent stops only when acceptance criteria pass.
- Measure success rate, rework, invalid tool calls, repeated calls, and unsupported completion claims.

### Hardware evaluation

For every run, record:

- Model and quantization.
- Runtime/backend and version.
- Native sequence length and actual prompt tokens.
- Peak system RAM and VRAM.
- Model load time, prefill latency, decode throughput.
- KV/state/memory footprint.
- Total task duration and evaluator score.
- Final task success.

A model that accepts 2M tokens but cannot retrieve a fact at token 1.8M is not a successful 2M-context model.

---

## 9. Proposed repository direction

The following is the target organization, not a description of all folders that already exist.

~~~text
ngu/
├── app/                         # Existing product interfaces and runtime
│   ├── cli.py
│   ├── api.py
│   ├── mcp_server.py
│   ├── agent.py                 # Gradually replaced by orchestration services
│   ├── runner.py
│   └── tools/
├── core/
│   ├── task_engine/              # Durable task graph and state transitions
│   ├── execution/                # Tool execution, retries, idempotency
│   ├── context/                  # Token-aware context assembly and policies
│   ├── memory/                   # Episodic/semantic/procedural memory
│   ├── retrieval/                # Lexical/vector/graph retrieval
│   └── evaluation/                # Task and model evaluation
├── models/
│   ├── adapters/                  # Model-provider and runtime adapters
│   ├── architectures/             # Experimental Transformer/SSM/hybrid blocks
│   ├── training/                  # Pretraining, SFT, preference, distillation
│   ├── configs/
│   ├── checkpoints/               # Not committed to Git
│   └── benchmarks/
├── datasets/
│   ├── raw/                       # Not committed unless explicitly licensed
│   ├── processed/
│   ├── trajectories/
│   └── manifests/
├── runtime/
│   ├── llama.cpp/                 # Existing local GGUF backend
│   └── profiles/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── long_context/
│   └── long_horizon/
├── scripts/
├── docs/
└── README.md
~~~

Do not perform a large folder migration before the corresponding interfaces and tests exist. Extract modules when a real boundary is established.

---

## 10. Engineering principles

1. **Task state is the source of truth.** Conversation history is context, not the database of record.
2. **Memory must be inspectable.** Important memories need provenance, timestamps, and correction paths.
3. **No fake context claims.** Native context, effective corpus access, and working context are measured separately.
4. **No model-size dogma.** Increase model capacity when it improves task success; reduce deployment cost through quantization and hardware-aware execution.
5. **Reuse open research.** Prefer adapting tested architectures and runtimes over rewriting mature components without a measurable reason.
6. **Keep model and runtime decoupled.** NGU should not depend permanently on one model family or inference backend.
7. **Verify deliverables.** A successful tool call is not proof that the user’s objective is complete.
8. **Recover safely.** Checkpoints must be durable, versioned, and resistant to duplicate side effects.
9. **Evaluate real tasks.** Long-context and long-horizon claims require reproducible test suites.
10. **Respect licenses and data rights.** Audit the model, code, tokenizer, training data, and derivative distribution terms before shipping.

---

## 11. Current setup and usage

The current application remains usable while the architecture evolves.

### Windows

~~~powershell
git clone https://github.com/MaxxAlan/ngu.git
cd ngu
setup.bat
~~~

For the full Windows, Docker, Kubernetes, and development setup, see [SETUP.md](SETUP.md).

### Common commands

~~~powershell
ngu                         # interactive local agent
ngu run "inspect this repo and run its tests"
ngu models                  # list local GGUF models
ngu status                  # runtime and task status
ngu tasks                   # recent tasks
ngu serve                   # web board + MCP server
~~~

The current web board is available at http://127.0.0.1:8765/; the MCP endpoint is http://127.0.0.1:8765/mcp.

### Current development checks

~~~powershell
uv sync --group dev
uv run pytest
uv run ruff check .
~~~

---

## 12. Project status

**Current state:** local agent runtime and basic task persistence exist. The long-horizon architecture, robust hierarchical memory, specialized model training pipeline, and native 2M–3M context model are research/development goals, not completed features.

| Capability | Status |
|---|---|
| Local CLI / web board / MCP | Implemented |
| GGUF + llama.cpp inference | Implemented |
| Tool execution | Implemented; reliability depends on model and task |
| SQLite task persistence | Implemented |
| Basic interruption recovery | Implemented; message/checkpoint based |
| Basic keyword memory | Implemented |
| Novel plan/write/review workflow | Implemented |
| Durable task graph and event-sourced recovery | Planned |
| Hierarchical memory with provenance | Planned |
| Semantic + lexical + graph retrieval | Planned |
| NGU-specific model checkpoint | Planned |
| Task-trajectory training and evaluation pipeline | Planned |
| Hybrid long-context architecture experiments | Planned |
| Native 2M–3M token context | Research target |
| Reliable long-horizon completion over hours/days | Product target; not yet established |

The immediate priority is not to rewrite the whole application or train a foundation model blindly. First make task state, context assembly, memory, and evaluation explicit. Then train and adapt a model against those interfaces. This gives every future model a measurable environment in which to improve.

---

## References

- [NGU source repository](https://github.com/MaxxAlan/ngu)
- [MiniCPM-SALA implementation and cookbook](https://github.com/OpenBMB/MiniCPM/tree/main/minicpm_sala)
- [MiniCPM model family](https://github.com/OpenBMB/MiniCPM)
- [Titans: Learning to Memorize at Test Time](https://research.google/pubs/titans-learning-to-memorize-at-test-time/)
- [Titans PyTorch community implementation](https://github.com/lucidrains/titans-pytorch)
- [Mamba research implementation](https://github.com/state-spaces/mamba)
- [Chidori durable agent runtime](https://github.com/ThousandBirdsInc/chidori)
- [Infini-attention paper](https://arxiv.org/abs/2404.07143)
