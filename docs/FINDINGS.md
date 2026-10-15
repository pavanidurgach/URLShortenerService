# Findings: what is verified, what is not

"Verified" = exercised by an automated test in this repo (`make test`, no LLM) or observed in the one live run described below. The run's output files were removed in a later cleanup; re-run to regenerate them (`runs/` is git-ignored).

## Verified by automated tests (simulated subagents)

| Area | Test |
|---|---|
| Kernel refuses to skip controls: unfinished dependencies, high-impact start without a recorded approval for the *current* inputs, approval without a name | `test_governance_cannot_be_skipped`, `test_unattended_run_cannot_perform_high_impact_actions` |
| Explicit DAG, parallel batches (`impact`‖`risk`, `docs`‖`security`‖`tests`) and a synchronisation join before `release` | `test_independent_stages_are_released_as_parallel_batches_with_joins` |
| Malformed subagent output is rejected by schema and retried | `test_bad_agent_output_is_rejected_by_schema_and_retried` |
| Bounded retry -> fallback agent -> triage (rollback / escalate / one supervised retry); policy violations never retried or triaged | `test_retries_are_bounded_then_fallback_then_triage`, `test_triage_rollback_and_escalate`, `test_policy_violations_are_never_retried...` |
| Staging copy + all-or-nothing policy-checked apply; `.gitignore`'d build output stays out; exact workspace restore | rollback and workspace-restore tests |
| An agent's "tests pass" cannot override the kernel's independent re-run | `test_agent_claiming_green_cannot_override_the_independent_rerun` |
| Planner can waive only optional stages; clarify stage spawned at runtime for an unclear requirement; `amend` re-runs only affected stages and re-asks approval | `test_plan_waives_only_optional_stages...`, `test_clarify_stage_is_spawned_at_runtime...` |
| Same project folder across runs: a later run on existing code is detected as such | `test_three_requirements_in_a_row...` |
| Safe-stop, kill switch, resume without repeating work | `test_safe_stop_and_resume...`, `test_kill_switch_file_halts_the_run` |
| Hash-chained audit with tamper detection; metrics derived from the audit log | `test_audit_log_detects_tampering`, `test_metrics_*` |
| Hook guard logic (write confinement, network denial, command screening, chaining, exit code 2); tests never touch the real active-run pointer | `test_guard_*`, `test_cli_speaks_json...` |
| Policy rules are data and language-neutral; agent-authored commands are screened | `test_policy_*` |
| Agents are subagent files with least privilege and no stack names; **no Python module calls a model or launches `claude`**; the app has no named, scope-specific inputs | `test_every_workflow_agent_is_a_subagent_file...`, `test_python_never_calls_a_model_or_launches_claude`, `test_the_app_has_no_named_scope_inputs` |
| Stack-agnostic kernel: three runs complete on a shell-script project | `test_three_requirements_in_a_row...` |

## Observed in the live run (real subagents; output since removed)

Requirement: `examples/inputs/url-shortener.md`, new project, scripted reviewer. Outcome: all 10 stages done, release **GO v1.0.0** (7/8
checks), 8 subagent runs (~188k reported tokens), ~462 s wall-clock, audit chain intact, 63 tests re-verified by the kernel (exit 0).

| Observation | Detail |
|---|---|
| Subagent delegation works | Every stage was delegated to a real `.claude/agents/*.md` subagent; independent stages ran in parallel; `release` waited for `tests`, `security` and `docs` |
| Runtime re-plan | The planner waived `impact` (empty folder); the requirements agent reported 8-10 ambiguities, so `clarify` was spawned and `design` rewired; the scripted reviewer accepted the defaults (recorded as assumptions). **Over-flagging on a fairly precise requirement - prompt needs tuning** |
| Stack choice is not reproducible | Two live runs of the same requirement: the first chose stdlib-only Python (`http.server`, 38 `unittest` tests), the second FastAPI + `pytest` (63 tests). Expected non-determinism; the kernel's guarantees are about control and evidence, not repeatability |
| Security reviewer finds real defects | Both runs: non-ASCII URLs pass validation and then break the redirect with a 500. Second run also: chunked bodies bypass the size cap. The release gate blocks only *high* findings, so both released GO with medium findings listed. **Policy question for you: should medium findings block a release?** |
| Subagents self-report honestly | They state when they did not run a schema validator (the kernel validated), and the docs writer states it has no shell and did not run the commands it documents |
| More defects found by a later live run (Java Spring Boot requirement) and fixed | (1) The hook's command deny-list refused any Bash command containing an absolute path under `/Users`, but briefs give agents absolute staging paths, so the implementer's `mkdir`/`cd <staging> && mvn` were blocked; the guard now exempts only the run's own staging, results and project folders. (2) A retry rebuilt the staging copy, deleting the first attempt's code when only its result file was malformed; retries now keep the staging copy and the brief says why the last attempt was rejected. (3) Maven was on the PATH only via `~/.zshrc`, which the desktop app's agents do not read; `mvn` is now linked into `~/.local/bin`. The kernel's gates did their job: the malformed result was rejected by schema and the empty change set by `files_written`, and the run halted safely |
| Defects in *this repo* found by the live runs and fixed | The requirements brief did not list workflow stage ids; the project state was recomputed per brief (so later stages saw an empty project as "existing"); tests overwrote the shared active-run pointer. All fixed, the last two with tests |
| Terminology | Agents naturally use other labels for new/existing project; briefs now instruct "new project / existing project" |

## Not verified - read before relying on this

1. **Only one requirement has been run live** (`url-shortener`). A change to existing code (`--project` pointing at real code), a tests-or-docs-only
   requirement, a late `amend`, the docs fallback, a failed stage with triage, and a human answering clarifications/approvals through
   AskUserQuestion have **not** been exercised with real subagents. They are covered only by the simulated tests.
2. There is no saved example output in the repo any more: the live-run observations above are my record of one run, not a file you can inspect. Re-run to regenerate evidence.
3. **Claims taken from documentation, not tested end to end** (from the Claude Code guide subagent reading the official docs): hook payload
   fields incl. `agent_id` for subagent calls, exit-code-2 blocking, project hooks firing for subagent calls. The hook was registered in this
   session, but I did not run an adversarial test of it live. If your version differs the symptom is a hook that does not block (the guard
   logic itself is tested).
4. **The kernel cannot prove a human approved.** It records who the orchestrator reports (`human:<name>`), refuses to start a high-impact stage
   without a record for the current inputs, and labels simulated reviewers `(scripted)`. `--scripted` was used for the live run, so no real human decision
   was exercised. Subagents are blocked from `approve/stop/amend/triage` by the hook only if the hook receives `agent_id`.
5. **Containment is not a sandbox.** The implementer has Bash (it ran `pip install` and the tests). The hook confines file-tool writes and screens
   command text, and the staging diff is policy-checked, but commands can do things a text screen does not recognise. Use a container/VM for untrusted requirements.
6. The kernel executes the design's setup/test commands itself after a deny-list screen (the live run installed dependencies from the network).
7. Token figures are as reported by the harness's subagent completion notices and were passed through by the orchestrator; they are not measured by the kernel.

## Suggested next steps

Run an existing-project change (`--project <repo>`), a tests-or-docs-only requirement and a drill with `--inject` in a fresh session and add the results here;
decide whether medium findings should block; tune the requirements prompt against over-flagging; add a container wrapper for the implementer's commands.
