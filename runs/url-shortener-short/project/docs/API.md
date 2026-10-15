# API reference

Base URL: `http://localhost:8080` (default). JSON in and out for `/api/*`. No authentication.

## Error format

Every error from the application:

    {"error": "<machine_code>", "message": "<human text>"}

No exception class names or stack traces are returned.

| HTTP | `error` | When |
|---|---|---|
| 400 | `malformed_json` | Body is not parseable JSON |
| 404 | `not_found` | Unknown code (redirect and stats) |
| 409 | `alias_taken` | Alias already in use |
| 413 | `payload_too_large` | Declared `Content-Length` over `app.max-body-bytes` (8192) |
| 422 | `invalid_request` | Missing/blank/invalid `url`, invalid or reserved `alias` |
| 503 | `code_generation_failed` | 10 random codes in a row collided (practically unreachable) |

## POST /api/links

Create a short link.

Request body (`Content-Type: application/json`):

| Field | Type | Required | Rules |
|---|---|---|---|
| `url` | string | yes | http or https (scheme case-insensitive), non-empty host, max 2048 chars, no whitespace/control/non-ASCII characters, no `%0d`/`%0a`, no userinfo (`user:pass@`) |
| `alias` | string | no | Full match `^[A-Za-z0-9_-]{3,32}$`, case sensitive (`Abc` and `abc` differ); reserved: `api`, `links`, `actuator`, `error`, `h2-console`. If omitted, a 7-character random `[A-Za-z0-9]` code is generated. |

Aliases and generated codes share one namespace.

Example:

    curl -i -X POST localhost:8080/api/links -H 'Content-Type: application/json' \
      -d '{"url":"https://example.com/a?b=c","alias":"docs_1"}'

    HTTP/1.1 201
    {"code":"docs_1","shortUrl":"http://localhost:8080/docs_1","url":"https://example.com/a?b=c"}

Without alias: `{"url":"https://example.com"}` returns 201 with e.g. `{"code":"aB3dE9z","shortUrl":"http://localhost:8080/aB3dE9z","url":"https://example.com"}`.

Error cases:

| Request | Status |
|---|---|
| `{"url":"javascript:alert(1)"}`, `ftp://...`, `http://`, URL with spaces, userinfo, or over 2048 chars | 422 `invalid_request` |
| `{}` or `{"url":""}` | 422 `invalid_request` ("url is required") |
| `{"url":"https://a.com","alias":"ab"}` (too short), 33+ chars, `a/b`, `a.b`, `api` | 422 `invalid_request` |
| `{"url":"https://a.com","alias":"docs_1"}` when taken | 409 `alias_taken` |
| `{not json` | 400 `malformed_json` |
| Body declared larger than 8192 bytes | 413 `payload_too_large` |

## GET /{code}

Redirect to the stored URL.

    curl -i localhost:8080/docs_1

    HTTP/1.1 302
    Location: https://example.com/a?b=c
    Cache-Control: no-store

Each successful redirect increments the click count by one (atomic, safe under concurrency). 302 with `no-store` is used so every click reaches the service. Unknown code: 404 `not_found`; nothing is counted.

## GET /api/links/{code}

Return link details and click count. Does not change the count.

    curl localhost:8080/api/links/docs_1

    200 {"code":"docs_1","url":"https://example.com/a?b=c","clicks":3}

Unknown code: 404 `{"error":"not_found","message":"Unknown code"}`.

## Data notes

There is no schema or database. A link is `(code, url, clicks)` held in memory in `LinkStore`; uniqueness is enforced atomically with `putIfAbsent`. Data is lost on restart.
