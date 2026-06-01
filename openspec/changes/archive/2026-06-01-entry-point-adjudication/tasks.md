## 1. Code Index: Defer CallChain Construction

- [x] 1.1 Remove `build_call_chains` call from `build_code_index()` in `packages/core/src/shannon_core/code_index/__init__.py` — set `chains=[]`, `total_chains=0` in returned `CodeIndex`
- [x] 1.2 Update `write_index_files()` to handle empty chains in the summary output (coverage metrics section)
- [x] 1.3 Update existing tests in `packages/core/tests/code_index/test_build_code_index.py` to expect `total_chains=0` and `chains=[]`
- [x] 1.4 Update existing tests in `packages/core/tests/code_index/test_workflow_integration.py` to match new code_index shape

## 2. Entry Point Detection: async def Noise Reduction

- [x] 2.1 Add heuristic filters to `_detect_python()` in `packages/core/src/shannon_core/code_index/entry_points.py` — skip async def when: function name starts with `_`, file name contains `test_`/`_test`/`conftest`, parent directory is `tests`/`test`/`spec`
- [x] 2.2 Change async def catch-all confidence from 0.30 to 0.40 for candidates that pass filters
- [x] 2.3 Pass file path context into `_detect_python()` (currently `FuncBlock.file_path` is available via `block.file_path`)
- [x] 2.4 Add unit tests for each heuristic filter in `packages/core/tests/code_index/test_entry_points.py`

## 3. Entry Points JSON: Model and Write

- [x] 3.1 Define `AdjudicatedEntryPoint` pydantic model in `packages/core/src/shannon_core/code_index/models.py` — fields: `func_block_id`, `verdict` (confirmed/rejected/reclassified), `entry_type`, `route`, `http_method`, `evidence`, `source` (code_index/llm_discovery)
- [x] 3.2 Define `AdjudicationResult` pydantic model — fields: `repository`, `language`, `adjudicated_entry_points: list[AdjudicatedEntryPoint]`
- [x] 3.3 Add `ENTRY_POINTS` to `DeliverableType` enum in `packages/core/src/shannon_core/models/deliverables.py` with filename `entry_points.json`
- [x] 3.4 Add model tests in `packages/core/tests/code_index/test_models.py`

## 4. Rebuild CallChains Activity

- [x] 4.1 Implement `rebuild_call_chains` deterministic function in `packages/core/src/shannon_core/code_index/` — reads `entry_points.json` for confirmed entry point IDs, reads `code_index.json` for blocks/edges, calls `build_call_chains()` with confirmed IDs only, writes updated `code_index.json`
- [x] 4.2 Handle LLM-discovered entry points: match `func_block_id` against existing FuncBlock index; log and skip unmatched ones
- [x] 4.3 Add `run_rebuild_call_chains` activity in `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`
- [x] 4.4 Register the activity in `packages/whitebox/src/shannon_whitebox/worker.py`
- [x] 4.5 Add unit tests for `rebuild_call_chains` function (confirmed-only chains, rejected exclusion, unresolved LLM discovery)

## 5. Pipeline Workflow Update

- [x] 5.1 Update `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` — after PRE_RECON agent completes, call `run_rebuild_call_chains` activity within the same `pre-recon` phase, before marking pre-recon complete
- [x] 5.2 Ensure resume logic skips `rebuild_call_chains` when `pre-recon` is already in `completedAgents`
- [x] 5.3 Add integration test for the updated workflow sequence

## 6. PRE_RECON Prompt Rewrite

- [x] 6.1 Rewrite `<starting_context>` in `prompts/pre-recon-code.txt` — remove contradictory instructions, state coherent relationship: code_index produces candidates, PRE_RECON adjudicates and supplements, CallChains built after adjudication
- [x] 6.2 Add Phase 0 instructions — explicit adjudication task: read `code_index.json`, for each `needs_llm_review=True` entry point read source context, output `entry_points.json` with verdicts; auto-confirm high-confidence entries
- [x] 6.3 Modify Entry Point Mapper Agent prompt in Phase 1 — from "Find ALL network-accessible entry points" to "Find entry points the deterministic code_index may have missed: configuration-file routes, dynamic registration, unknown frameworks"; instruct to not duplicate code_index findings
- [x] 6.4 Add instruction for PRE_RECON to write `entry_points.json` using `save-deliverable --type ENTRY_POINTS` before Phase 1
- [x] 6.5 Update `<starting_context>` to also apply to `prompts/pipeline-testing/pre-recon-code.txt`

## 7. Documentation Updates

- [x] 7.1 Update `docs/AGENTS.md` pre-recon section to reflect Phase 0 adjudication and supplementary discovery role
- [x] 7.2 Update `docs/architecture.md` or relevant architecture docs to reflect the new pipeline sequence (code_index → PRE_RECON adjudicate + supplement → rebuild chains → RECON)
