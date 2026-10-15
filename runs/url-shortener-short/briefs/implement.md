# Task brief: implement (subagent: implementer)

Stage: HIGH IMPACT: implement the design with tests, in a staging copy; the diff is applied after policy checks
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
- `C:\Users\pavan\Documents\repos\urlShortner\urlShortner\runs\url-shortener-short\artifacts\design.json` - design: New project: single-module Spring Boot 3.5.6 (Maven, Java) URL shortener with an in-memory ConcurrentHashMap store (putIfAbsent for atomic code/alias uniqueness, AtomicLong click counter), JSON API un
- `C:\Users\pavan\Documents\repos\urlShortner\urlShortner\runs\url-shortener-short\artifacts\clarify.json` - human clarifications (a `change_request` overrides earlier decisions)

## Orchestrator guidance for this stage

- Validate URLs with java.net.URI (scheme http/https, host present) and alias with regex ^[A-Za-z0-9_-]{3,32}$. Map errors: invalid URL/alias 422, taken alias 409, unknown code 404. Generated codes must not collide with existing codes or aliases. Include MockMvc tests for each requirement bullet and a concurrency check on alias creation.

## Where to work

Edit ONLY inside this isolated copy of the project: `C:\Users\pavan\Documents\repos\urlShortner\urlShortner\runs\url-shortener-short\staging\implement`. Your changes are diffed, policy-checked and applied by the orchestrator; anything outside it is rejected.

## Project commands (from the design; use exactly these)

- setup: `mvn -q dependency:resolve`
- test: `mvn -B -q test`
- run: `mvn spring-boot:run`

## Terminology

Say `new project` / `existing project` for the workspace state; do not use other labels for these scopes.

## Output

Write ONE JSON file to `C:\Users\pavan\Documents\repos\urlShortner\urlShortner\runs\url-shortener-short\results\implement.json` that validates against the schema in `C:\Users\pavan\Documents\repos\urlShortner\urlShortner\schemas\implement.json` (read it first). No other output file is read. Then reply with a single line: what you did.

