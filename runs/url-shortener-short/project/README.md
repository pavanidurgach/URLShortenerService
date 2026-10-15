# URL shortener

Spring Boot 3.5.6 (Maven, Java 17+) URL shortener with in-memory storage. This is a new project; there is no database and no authentication.

- Create a short link for an http(s) URL, optionally with a custom alias.
- Redirect `GET /{code}` to the original URL (302) and count the click.
- Read a link's click count at `GET /api/links/{code}`.

## Prerequisites

- JDK 17 or newer (the build targets Java 17; developed on JDK 25) and Maven (`mvn`) on `PATH`.
- Spring Boot 3.5.6 artifacts in `~/.m2`, or Maven Central reachable during setup.

## Commands

    mvn -q dependency:resolve   # setup
    mvn -B -q test              # run tests (JUnit 5 + MockMvc)
    mvn spring-boot:run         # run on http://localhost:8080

Tests run offline once dependencies are resolved. Tests cover the API (`LinkApiTest`), concurrency (`ConcurrencyTest`), code-collision handling (`LinkServiceTest`) and the store (`LinkStoreTest`).

## Quick start

    curl -X POST localhost:8080/api/links -H 'Content-Type: application/json' \
      -d '{"url":"https://example.com/long","alias":"my-link"}'
    # 201 {"code":"my-link","shortUrl":"http://localhost:8080/my-link","url":"https://example.com/long"}
    curl -i localhost:8080/my-link            # 302, Location: https://example.com/long
    curl localhost:8080/api/links/my-link     # 200 {"code":"my-link","url":"https://example.com/long","clicks":1}

Full reference: [docs/API.md](docs/API.md).

## API summary

| Method and path | Success | Errors |
|---|---|---|
| `POST /api/links` | 201 `{code, shortUrl, url}` | 400, 409, 413, 422, 503 |
| `GET /{code}` | 302 with `Location`, `Cache-Control: no-store`; +1 click | 404 |
| `GET /api/links/{code}` | 200 `{code, url, clicks}`; no click counted | 404 |

All errors return `{"error": "<machine_code>", "message": "<text>"}`.

## Configuration

Set in `src/main/resources/application.properties`, or override on the command line, e.g. `mvn spring-boot:run -Dspring-boot.run.arguments=--app.base-url=https://sho.rt`.

| Property | Default | Meaning |
|---|---|---|
| `app.base-url` | `http://localhost:8080` | Prefix of returned `shortUrl` (a trailing `/` is trimmed). Never derived from the `Host` header. |
| `app.max-body-bytes` | `8192` | Requests declaring a larger `Content-Length` get 413. |
| `server.port` | Spring default 8080 | Standard Spring Boot property. |

## Operational notes and limitations

- Storage is in memory (`ConcurrentHashMap`): all links and click counts are lost on restart; the map is unbounded and there is no link expiry or deletion.
- Single instance only; state is not shared across instances.
- No authentication or rate limiting. Like any shortener this is an open redirect; only scheme and host are validated (no blocklist, no private-host filtering). Add rate limiting, auth and a blocklist before exposing it publicly.
- Stored URLs are readable by anyone who knows the code via `GET /api/links/{code}`; do not shorten secret URLs.
- The body-size check relies on the declared `Content-Length`; chunked bodies are not covered.
- Responses carry `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`.
