# Task brief: design (subagent: solution-designer)

Stage: Design record: stack, commands, API, schema, ADRs, decisions, rollback plan
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

- `C:\Users\pavan\Documents\repos\urlShortner\urlShortner\runs\url-shortener-short\artifacts\requirements.json` - requirements: New project: Spring Boot (Maven) URL shortener with create, redirect, custom alias, click count and validation, tested with JUnit and documented run/test commands.
- `C:\Users\pavan\Documents\repos\urlShortner\urlShortner\runs\url-shortener-short\artifacts\risk.json` - risk: New project: in-memory Spring Boot URL shortener. Main risks are abuse of redirects (unsafe schemes, open redirect, SSRF-style targets), alias/code collisions and click-counter races under concurrency
- `C:\Users\pavan\Documents\repos\urlShortner\urlShortner\runs\url-shortener-short\artifacts\clarify.json` - clarify: 5 decisions, 0 assumed by default

## Orchestrator guidance for this stage

- Keep it minimal: in-memory ConcurrentHashMap store with atomic putIfAbsent for alias uniqueness and AtomicLong click counter; state the persistence ceiling explicitly. Document exact run and test commands (mvn spring-boot:run, mvn test). Use Java and Spring Boot versions that are available locally.

## Terminology

Say `new project` / `existing project` for the workspace state; do not use other labels for these scopes.

## Output

Write ONE JSON file to `C:\Users\pavan\Documents\repos\urlShortner\urlShortner\runs\url-shortener-short\results\design.json` that validates against the schema in `C:\Users\pavan\Documents\repos\urlShortner\urlShortner\schemas\design.json` (read it first). No other output file is read. Then reply with a single line: what you did.

