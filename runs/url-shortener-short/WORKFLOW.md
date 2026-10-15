# Workflow - URL shortener

Run: `url-shortener-short` | Project: **new-project** | Status: **SUCCEEDED**

Each box is a stage; arrows mean 'must finish before'. A lock marks a high-impact stage that needs a recorded human approval. Independent stages (no arrow between them) run in parallel. Colour = current state.

```mermaid
flowchart LR
  plan["plan"]
  requirements["requirements"]
  impact["impact"]
  risk["risk"]
  design["design"]
  implement["implement 🔒"]
  tests["tests"]
  security["security"]
  docs["docs"]
  release["release 🔒"]
  clarify["clarify"]
  plan --> requirements
  requirements --> impact
  requirements --> risk
  requirements --> design
  impact --> design
  risk --> design
  clarify --> design
  requirements --> implement
  design --> implement
  implement --> tests
  design --> tests
  implement --> security
  design --> security
  risk --> security
  implement --> docs
  design --> docs
  tests --> release
  security --> release
  docs --> release
  risk --> release
  implement --> release
  design --> release
  requirements --> clarify
  classDef done fill:#d4edda,stroke:#555
  classDef waived fill:#e2e3e5,stroke:#555
  classDef running fill:#cce5ff,stroke:#555
  classDef pending fill:#ffffff,stroke:#555
  classDef stale fill:#fff3cd,stroke:#555
  classDef failed fill:#f8d7da,stroke:#555
  classDef rolledback fill:#f5c6cb,stroke:#555
  classDef skipped fill:#f0f0f0,stroke:#555
  classDef rejected fill:#f8d7da,stroke:#555
  classDef triage fill:#ffe5b4,stroke:#555
  class plan done
  class requirements done
  class impact waived
  class risk done
  class design done
  class implement done
  class tests done
  class security done
  class docs done
  class release done
  class clarify done
```

States: `DONE` `WAIVED` `RUNNING` `PENDING` `STALE` `FAILED` `ROLLED_BACK` `SKIPPED` `REJECTED` `AWAITING_TRIAGE`

| stage | agent | kind | depends on | entry gate | exit gate | approval | optional | retries | fallback | state |
|---|---|---|---|---|---|---|---|---|---|---|
| plan | workflow-planner | agent | - | - | - | no | no | 1 | - | **DONE** |
| requirements | requirements-analyst | agent | plan | - | - | no | no | 1 | - | **DONE** |
| impact | impact-analyst | agent | requirements | - | - | no | yes | 1 | - | **WAIVED** |
| risk | risk-analyst | agent | requirements | - | - | no | no | 1 | - | **DONE** |
| design | solution-designer | agent | requirements, impact, risk, clarify | ambiguities_resolved | design_complete | no | no | 1 | - | **DONE** |
| implement | implementer | agent | requirements, design | ambiguities_resolved | files_written | yes | no | 1 | - | **DONE** |
| tests | test-engineer | agent | implement, design | - | tests_green | no | no | 1 | - | **DONE** |
| security | security-reviewer | agent | implement, design, risk | - | no_high_findings | no | no | 1 | - | **DONE** |
| docs | technical-writer | agent | implement, design | - | - | no | no | 1 | technical-writer-lite | **DONE** |
| release | kernel | kernel | tests, security, docs, risk, implement, design | approvals_recorded | release_go | yes | no | 0 | - | **DONE** |
| clarify | human | human | requirements | - | - | no | no | 0 | - | **DONE** |

Stages added or waived while the run was in progress (for example `clarify`, or a waived `impact`) appear here as they happen. Full history: `audit.jsonl`; final report: `FINAL_SUMMARY.md`.
