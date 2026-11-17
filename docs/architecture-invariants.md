# Runtime Architecture Invariants

This document defines the state and side-effect rules that runtime changes must preserve. The tests named below are the executable form of these rules.

## 1. Message and Turn Lifecycle

- Every inbound message ends in one explicit state: `merged`, `skipped`, `dropped`, `failed`, `sent`, or `committed`.
- A merged message points to the batch that owns the reply turn.
- Trace IDs must survive queue and worker boundaries.
- Record-only paths must not emit messages.

Enforced by:

- `tests/test_trace_id.py`
- `tests/test_group_pipeline.py`
- `tests/test_group_decider_state.py`

## 2. Task Ownership

- A `job_id` identifies exactly one task record.
- A normal submit cannot replace an existing `job_id`; recovery uses `resume`.
- Only a conditional state transition may move a task to `running`, `done`, `failed`, `timeout`, or `cancelled`.
- Late worker completion cannot overwrite a terminal state.
- Mirrors of ticket, outbox, and AIGC records must follow their source state.

Enforced by:

- `tests/test_task_center.py`
- `tests/test_job_persistence.py`
- `tests/test_job_wait.py`

## 3. Workflow Execution

- A node marked as having a handler must fail if the handler is missing after restart.
- A `run_id` identifies one run and cannot silently overwrite an older run.
- Parallel nodes receive isolated data containers and merge deterministically.
- A fallback source is visible to the fallback handler before it executes.
- Cancellation prevents later nodes from starting and remains the final state.
- Temporary state files use unique names.

Enforced by:

- `tests/test_workflow.py`
- `tests/test_autonomy_devloop.py`
- `tests/test_plugin_forge_verify.py`

## 4. Subagents

- Queue timeout and execution timeout are both observable.
- A timed-out or cancelled task cannot later become `done`.
- Executors may accept `cancel_event` or `should_cancel` for cooperative shutdown.
- Resuming without a new executor leaves the task `paused`.

Enforced by:

- `tests/test_subagent.py`
- `tests/test_subagent_resume.py`

## 5. Outbound Side Effects

- History, cooldowns, counters, and pending topics are committed only after confirmed delivery.
- A partial multi-bubble send commits only delivered bubbles.
- Failed sends release temporary deduplication reservations.
- Unknown delivery outcomes must remain distinguishable from confirmed failure.

Enforced by:

- `tests/test_proactive_deliver.py`
- `tests/test_outbound.py`
- `tests/test_outbound_verify.py`

## 6. Plugin Routing and State

- One behavior has one active owner. Recall feedback cannot be emitted by both the core and a plugin.
- `recall_feedback_by` has exactly one owner: `core`, `plugin`, or `proactivity`.
  A burst of recall events for one conversation becomes one feedback episode.
- Plugin routing checks must use the same configuration value as the host.
- Decision caches include group, sender, message identity, and retry mode.
- Per-group decision state is serialized; independent groups may proceed in parallel.
- Image versus meme classification uses protocol `sub_type`, meme-library identity, or
  collected-index identity. File extension and GIF headers alone are not semantic evidence.
- Group images invoke vision only when the bot is addressed, quoted, or the text discusses
  the image. An unaddressed bare photo stays in context without a vision call.
- Automatic meme replies use existing collection tags; an unknown meme falls back to a
  random reaction instead of paying for a fresh vision request.
- Visual failure, download failure, and model fallback details stay internal. User-facing
  replies must not expose phrases such as "read failed", "cannot see the image", or
  "the image is blurry".
- Runtime `tags.json` and `descriptions.json` are data, not source defaults; deployment
  synchronization must not replace them with repository test fixtures.

Enforced by:

- `tests/test_recall_manager_plugin.py`
- `tests/test_recall_ab.py`
- `tests/test_group_decider_logic.py`
- `tests/test_group_decider_state.py`
- `tests/test_recall_contract.py`
- `tests/test_packaging.py`
- `tests/test_meme_back.py`

## Change Checklist

For changes touching a state machine, queue, thread, plugin event, or outbound path:

1. Identify the invariant affected.
2. Add or update a regression test that exercises the failure transition.
3. Run the focused tests and Python syntax compilation.
4. Explain the state transition and failure behavior in the commit body.
5. Keep one authoritative source for routing, deduplication, and delivery confirmation.

## Limits

Python threads cannot forcibly stop arbitrary synchronous code. A timeout or cancellation is complete only when the executor cooperates through `cancel_event` or `should_cancel`; otherwise the task state records that the execution may still be running.

Crash safety covers the transitions tested by the current fake backends and SQLite state machines. It does not prove safety for arbitrary third-party plugins that perform non-idempotent external actions.
