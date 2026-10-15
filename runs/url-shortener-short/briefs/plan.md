# Task brief: plan (subagent: workflow-planner)

Stage: Orchestrator-side planning: waive optional stages, add guidance, flag risks to watch
Workspace state: **new-project** (new-project = it started empty; existing-project = it started with code in `C:\Users\pavan\Documents\repos\urlShortner\urlShortner\runs\url-shortener-short\project`; this is fixed for the whole run - the code you may now see is what earlier stages produced)

## Requirement

# URL shortener

- Create a short link for a long http(s) URL; return the short code and short URL.
- Redirect a short link to its original URL; unknown codes return 404.
- Optional custom alias (3-32 letters, digits, `_` or `-`); a taken alias returns 409.
- Show a link's click count.
- Reject invalid URLs with 422.
- Tech stack: Java with Spring Boot (Maven), JUnit for tests.
- Provide documented commands to run the service and its tests.

## Upstream artifacts (read the ones you need)

- none

## Workflow stages (you may only waive stages marked optional)

```json
[
 {
  "id": "plan",
  "agent": "workflow-planner",
  "deps": [],
  "optional": false,
  "high_impact": false,
  "description": "Orchestrator-side planning: waive optional stages, add guidance, flag risks to watch"
 },
 {
  "id": "requirements",
  "agent": "requirements-analyst",
  "deps": [
   "plan"
  ],
  "optional": false,
  "high_impact": false,
  "description": "Normalise requirement, detect ambiguity, decompose into tasks"
 },
 {
  "id": "impact",
  "agent": "impact-analyst",
  "deps": [
   "requirements"
  ],
  "optional": true,
  "high_impact": false,
  "description": "Codebase reasoning: impacted modules, ripple, data flows"
 },
 {
  "id": "risk",
  "agent": "risk-analyst",
  "deps": [
   "requirements"
  ],
  "optional": false,
  "high_impact": false,
  "description": "Risk register with mitigations and validation plan"
 },
 {
  "id": "design",
  "agent": "solution-designer",
  "deps": [
   "requirements",
   "impact",
   "risk"
  ],
  "optional": false,
  "high_impact": false,
  "description": "Design record: stack, commands, API, schema, ADRs, decisions, rollback plan"
 },
 {
  "id": "implement",
  "agent": "implementer",
  "deps": [
   "requirements",
   "design"
  ],
  "optional": false,
  "high_impact": true,
  "description": "HIGH IMPACT: implement the design with tests, in a staging copy; the diff is applied after policy checks"
 },
 {
  "id": "tests",
  "agent": "test-engineer",
  "deps": [
   "implement",
   "design"
  ],
  "optional": false,
  "high_impact": false,
  "description": "Run the full test-suite (re-verified independently by the kernel)"
 },
 {
  "id": "security",
  "agent": "security-reviewer",
  "deps": [
   "implement",
   "design",
   "risk"
  ],
  "optional": false,
  "high_impact": false,
  "description": "Security review merged with a policy pattern scan"
 },
 {
  "id": "docs",
  "agent": "technical-writer",
  "deps": [
   "implement",
   "design"
  ],
  "optional": false,
  "high_impact": false,
  "description": "Documentation written in a staging copy, policy-checked, applied"
 },
 {
  "id": "release",
  "agent": "kernel",
  "deps": [
   "tests",
   "security",
   "docs",
   "risk",
   "implement",
   "design"
  ],
  "optional": false,
  "high_impact": true,
  "description": "HIGH IMPACT: release readiness computed from evidence; a human signs off"
 }
]
```

## Terminology

Say `new project` / `existing project` for the workspace state; do not use other labels for these scopes.

## Output

Write ONE JSON file to `C:\Users\pavan\Documents\repos\urlShortner\urlShortner\runs\url-shortener-short\results\plan.json` that validates against the schema in `C:\Users\pavan\Documents\repos\urlShortner\urlShortner\schemas\plan.json` (read it first). No other output file is read. Then reply with a single line: what you did.

