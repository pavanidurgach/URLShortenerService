# Task brief: requirements (subagent: requirements-analyst)

Stage: Normalise requirement, detect ambiguity, decompose into tasks
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

- `C:\Users\pavan\Documents\repos\urlShortner\urlShortner\runs\url-shortener-short\artifacts\plan.json` - plan: New project: Spring Boot (Maven) URL shortener with JUnit tests. Waive impact analysis since there is no existing code to analyse; run all other stages.

## Workflow stage ids (use these in `tasks[].stage`; `clarify` is added if you report ambiguities)

plan, requirements, impact, risk, design, implement, tests, security, docs, release

## Orchestrator guidance for this stage

- Pin down ambiguities: allowed URL schemes (http/https only), max URL length, alias case sensitivity, whether alias may collide with reserved paths, whether a redirect counts as a click, redirect status (302 vs 301), and 422 vs 400 for malformed JSON.

## Terminology

Say `new project` / `existing project` for the workspace state; do not use other labels for these scopes.

## Output

Write ONE JSON file to `C:\Users\pavan\Documents\repos\urlShortner\urlShortner\runs\url-shortener-short\results\requirements.json` that validates against the schema in `C:\Users\pavan\Documents\repos\urlShortner\urlShortner\schemas\requirements.json` (read it first). No other output file is read. Then reply with a single line: what you did.

