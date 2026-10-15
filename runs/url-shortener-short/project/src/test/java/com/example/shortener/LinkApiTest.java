package com.example.shortener;

import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.matchesPattern;
import static org.hamcrest.Matchers.not;
import static org.hamcrest.Matchers.startsWith;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.HashMap;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.NullSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.system.CapturedOutput;
import org.springframework.boot.test.system.OutputCaptureExtension;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;

@SpringBootTest
@AutoConfigureMockMvc
@ExtendWith(OutputCaptureExtension.class)
class LinkApiTest {
    @Autowired MockMvc mvc;
    @Autowired ObjectMapper json;

    private ResultActions create(String url, String alias) throws Exception {
        Map<String, String> body = new HashMap<>();
        body.put("url", url);
        if (alias != null) {
            body.put("alias", alias);
        }
        return mvc.perform(post("/api/links").contentType(MediaType.APPLICATION_JSON).content(json.writeValueAsString(body)));
    }

    private String createCode(String url, String alias) throws Exception {
        String res = create(url, alias).andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
        return json.readTree(res).get("code").asText();
    }

    @Test
    void createReturnsCodeAndShortUrl() throws Exception {
        create("https://example.com/a?b=c", null)
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.code").value(matchesPattern("[A-Za-z0-9]{7}")))
                .andExpect(jsonPath("$.shortUrl").value(startsWith("http://localhost:8080/")))
                .andExpect(jsonPath("$.url").value("https://example.com/a?b=c"));
    }

    @Test
    void redirectsWith302NoStoreAndSecurityHeaders() throws Exception {
        String code = createCode("https://example.com/target", null);
        mvc.perform(get("/" + code))
                .andExpect(status().isFound())
                .andExpect(header().stringValues("Location", "https://example.com/target"))
                .andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(header().string("X-Content-Type-Options", "nosniff"))
                .andExpect(header().string("Referrer-Policy", "no-referrer"));
    }

    @Test
    void unknownCodeIs404OnRedirectAndStats() throws Exception {
        mvc.perform(get("/nope123")).andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("not_found"));
        mvc.perform(get("/api/links/nope123")).andExpect(status().isNotFound());
    }

    @Test
    void customAliasWorksAndTakenAliasIs409() throws Exception {
        create("https://example.com/1", "my-alias_1").andExpect(status().isCreated())
                .andExpect(jsonPath("$.code").value("my-alias_1"));
        create("https://example.com/2", "my-alias_1").andExpect(status().isConflict())
                .andExpect(jsonPath("$.error").value("alias_taken"));
        mvc.perform(get("/my-alias_1")).andExpect(header().string("Location", "https://example.com/1"));
    }

    @Test
    void clicksCountRedirectsOnly() throws Exception {
        String code = createCode("https://example.com/clicks", null);
        mvc.perform(get("/api/links/" + code)).andExpect(jsonPath("$.clicks").value(0));
        mvc.perform(get("/" + code));
        mvc.perform(get("/" + code));
        mvc.perform(get("/api/links/" + code));
        mvc.perform(get("/api/links/" + code)).andExpect(status().isOk())
                .andExpect(jsonPath("$.clicks").value(2))
                .andExpect(jsonPath("$.url").value("https://example.com/clicks"))
                .andExpect(jsonPath("$.code").value(code));
    }

    @ParameterizedTest
    @NullSource
    @ValueSource(strings = {"", "  ", "javascript:alert(1)", "data:text/html,x", "file:///etc/passwd", "ftp://example.com/x",
            "http://", "https:///path", "http://a b", "http://user:pw@example.com/", "example.com", "http://exa\tmple.com",
            "http://example.com/%0d%0aSet-Cookie:x", "http://example.com/%0A", "http://example.com/é"})
    void invalidUrlsAre422(String url) throws Exception {
        create(url, null).andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.error").exists());
    }

    @Test
    void rawCrLfAndOverlongUrlsAre422() throws Exception {
        create("http://example.com/\r\nX: y", null).andExpect(status().isUnprocessableEntity());
        create("http://example.com/" + "a".repeat(2030), null).andExpect(status().isUnprocessableEntity());
        create("http://example.com/" + "a".repeat(2000), null).andExpect(status().isCreated());
    }

    @ParameterizedTest
    @ValueSource(strings = {"HTTP://ok.com", "http://ok.com", "https://sub.ok.com:8443/p?q=1#f", "http://127.0.0.1/x"})
    void validUrlsAre201(String url) throws Exception {
        create(url, null).andExpect(status().isCreated());
    }

    @ParameterizedTest
    @ValueSource(strings = {"ab", "a b", "a/b", "a.b", "ünï", "abc\n", "api", "links", "error", "actuator",
            "h2-console", "012345678901234567890123456789012"})
    void invalidAliasesAre422(String alias) throws Exception {
        create("https://example.com/", alias).andExpect(status().isUnprocessableEntity());
    }

    @ParameterizedTest
    @ValueSource(strings = {"abc-valid", "A_b-9x", "01234567890123456789012345678901"})
    void validAliasesAre201(String alias) throws Exception {
        create("https://example.com/", alias).andExpect(status().isCreated());
    }

    @Test
    void aliasesAreCaseSensitive() throws Exception {
        create("https://example.com/", "CaseAlias").andExpect(status().isCreated());
        create("https://example.com/", "casealias").andExpect(status().isCreated());
    }

    @Test
    void missingUrlIs422AndMalformedJsonIs400() throws Exception {
        mvc.perform(post("/api/links").contentType(MediaType.APPLICATION_JSON).content("{}"))
                .andExpect(status().isUnprocessableEntity());
        String res = mvc.perform(post("/api/links").contentType(MediaType.APPLICATION_JSON).content("{not json"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("malformed_json"))
                .andReturn().getResponse().getContentAsString();
        assertFalse(res.contains("Exception") || res.contains("at org."));
    }

    @Test
    void hugeBodyIsRejectedAndNothingStored() throws Exception {
        String body = "{\"url\":\"http://example.com/" + "a".repeat(1_000_000) + "\",\"alias\":\"huge-body\"}";
        mvc.perform(post("/api/links").contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isPayloadTooLarge());
        mvc.perform(get("/api/links/huge-body")).andExpect(status().isNotFound());
    }

    @Test
    void shortUrlIgnoresSpoofedHost() throws Exception {
        mvc.perform(post("/api/links").header("Host", "evil.example").header("X-Forwarded-Host", "evil.example")
                        .contentType(MediaType.APPLICATION_JSON).content("{\"url\":\"https://example.com/\"}"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.shortUrl").value(startsWith("http://localhost:8080/")))
                .andExpect(jsonPath("$.shortUrl").value(not(containsString("evil"))));
    }

    @Test
    void urlsAreNotLogged(CapturedOutput output) throws Exception {
        String code = createCode("https://example.com/?token=SECRET-TOKEN-XYZ", null);
        mvc.perform(get("/" + code)).andExpect(status().isFound());
        assertFalse(output.getAll().contains("SECRET-TOKEN-XYZ"));
    }
}
