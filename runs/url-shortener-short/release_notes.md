# Release notes - URL shortener v0.1.0

Decision: **GO**

Stack: Java (compiled with java.release=17, runs on the locally installed JDK 25) / Spring Boot 3.5.6 (spring-boot-starter-web, spring-boot-starter-validation, spring-boot-starter-test) (Maven)

## Changes
- `.gitignore` (added, +5/-0)
- `README.md` (added, +28/-0)
- `pom.xml` (added, +41/-0)
- `src/main/java/com/example/shortener/ApiException.java` (added, +22/-0)
- `src/main/java/com/example/shortener/ApiExceptionHandler.java` (added, +31/-0)
- `src/main/java/com/example/shortener/CodeGenerator.java` (added, +6/-0)
- `src/main/java/com/example/shortener/Link.java` (added, +6/-0)
- `src/main/java/com/example/shortener/LinkController.java` (added, +46/-0)
- `src/main/java/com/example/shortener/LinkService.java` (added, +103/-0)
- `src/main/java/com/example/shortener/LinkStore.java` (added, +21/-0)
- `src/main/java/com/example/shortener/RandomCodeGenerator.java` (added, +20/-0)
- `src/main/java/com/example/shortener/SecurityHeadersFilter.java` (added, +35/-0)
- `src/main/java/com/example/shortener/UrlShortenerApplication.java` (added, +11/-0)
- `src/main/resources/application.properties` (added, +2/-0)
- `src/test/java/com/example/shortener/ConcurrencyTest.java` (added, +83/-0)
- `src/test/java/com/example/shortener/LinkApiTest.java` (added, +177/-0)
- `src/test/java/com/example/shortener/LinkServiceTest.java` (added, +57/-0)
- `src/test/java/com/example/shortener/LinkStoreTest.java` (added, +23/-0)

## Checklist
- [x] tests green (independently re-run): 55 passed, exit 0
- [x] no high-severity security findings: 0 high, 2 medium
- [x] documentation exists in the workspace: README.md, docs/API.md
- [x] every risk has a validation plan: 14 risks
- [x] audit chain intact: ok
- [x] human approvals recorded for high-impact stages: all present
- [x] change footprint within policy: 18 files <= 120
- [ ] assumptions acknowledged: 4 recorded in the summary

## Rollback plan
New project with no prior release: rollback is to stop the service and revert/discard the release commit or tag (git revert of the release commit, or redeploy the previous artifact if any). There is no database, schema or migration, and in-memory data is ephemeral, so no data restore is needed; deleting the target/ directory removes build output. If persistence is added later, that release must carry its own forward/backward migration plan.
