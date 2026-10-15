# Task brief: security (subagent: security-reviewer)

Stage: Security review merged with a policy pattern scan
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
- `C:\Users\pavan\Documents\repos\urlShortner\urlShortner\runs\url-shortener-short\artifacts\risk.json` - risk: New project: in-memory Spring Boot URL shortener. Main risks are abuse of redirects (unsafe schemes, open redirect, SSRF-style targets), alias/code collisions and click-counter races under concurrency

## Orchestrator guidance for this stage

- Check open redirect and scheme abuse (javascript:, data:, file:), unbounded URL length, alias path-reserved collisions, and unbounded memory growth.

## Terminology

Say `new project` / `existing project` for the workspace state; do not use other labels for these scopes.

## Output

Write ONE JSON file to `C:\Users\pavan\Documents\repos\urlShortner\urlShortner\runs\url-shortener-short\results\security.json` that validates against the schema in `C:\Users\pavan\Documents\repos\urlShortner\urlShortner\schemas\security.json` (read it first). No other output file is read. Then reply with a single line: what you did.

