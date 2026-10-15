# URL shortener

- Create a short link for a long http(s) URL; return the short code and short URL.
- Redirect a short link to its original URL; unknown codes return 404.
- Optional custom alias (3-32 letters, digits, `_` or `-`); a taken alias returns 409.
- Show a link's click count.
- Reject invalid URLs with 422.
- Tech stack: Java with Spring Boot (Maven), JUnit for tests.
- Provide documented commands to run the service and its tests.
