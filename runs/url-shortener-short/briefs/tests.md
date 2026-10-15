# Task brief: tests (subagent: test-engineer)

Stage: Run the full test-suite (re-verified independently by the kernel)
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

- `C:\Users\pavan\Documents\repos\urlShortner\urlShortner\runs\url-shortener-short\artifacts\implement.json` - implement: 18 added, 0 modified, 0 deleted (+717/-0 lines); agent: New project: Spring Boot 3.5.6 (Maven, Java 17) URL shortener with in-memory ConcurrentHashMap store, POST /api/links, GET /{code} 302 redirect 
- `C:\Users\pavan\Documents\repos\urlShortner\urlShortner\runs\url-shortener-short\artifacts\design.json` - design: New project: single-module Spring Boot 3.5.6 (Maven, Java) URL shortener with an in-memory ConcurrentHashMap store (putIfAbsent for atomic code/alias uniqueness, AtomicLong click counter), JSON API un

## Orchestrator guidance for this stage

- Run mvn test from a clean state; confirm Maven and a JDK are present and report clearly if the toolchain is missing.

## Project commands (from the design; use exactly these)

- setup: `mvn -q dependency:resolve`
- test: `mvn -B -q test`
- run: `mvn spring-boot:run`

## Terminology

Say `new project` / `existing project` for the workspace state; do not use other labels for these scopes.

## Output

Write ONE JSON file to `C:\Users\pavan\Documents\repos\urlShortner\urlShortner\runs\url-shortener-short\results\tests.json` that validates against the schema in `C:\Users\pavan\Documents\repos\urlShortner\urlShortner\schemas\tests.json` (read it first). No other output file is read. Then reply with a single line: what you did.

