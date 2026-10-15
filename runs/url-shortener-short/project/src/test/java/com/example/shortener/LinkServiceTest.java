package com.example.shortener;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;

class LinkServiceTest {
    private static CodeGenerator seq(String... codes) {
        Deque<String> q = new ArrayDeque<>(List.of(codes));
        return q::pop;
    }

    @Test
    void generatedCollisionRetriesWithoutOverwriting() {
        LinkStore store = new LinkStore();
        store.add("dupcode", "https://original.example/");
        LinkService service = new LinkService(store, seq("dupcode", "fresh01"), "http://x");
        Link link = service.create("https://new.example/", null);
        assertEquals("fresh01", link.code());
        assertEquals("https://original.example/", store.find("dupcode").orElseThrow().url());
    }

    @Test
    void generatedCodeCannotOverwriteAlias() {
        LinkStore store = new LinkStore();
        LinkService service = new LinkService(store, seq("taken-alias", "other77"), "http://x");
        service.create("https://a.example/", "taken-alias");
        assertEquals("other77", service.create("https://b.example/", null).code());
        assertEquals("https://a.example/", store.find("taken-alias").orElseThrow().url());
    }

    @Test
    void exhaustedRetriesFailWithoutOverwrite() {
        LinkStore store = new LinkStore();
        store.add("always1", "https://original.example/");
        LinkService service = new LinkService(store, () -> "always1", "http://x");
        ApiException e = assertThrows(ApiException.class, () -> service.create("https://new.example/", null));
        assertEquals(HttpStatus.SERVICE_UNAVAILABLE, e.status());
        assertEquals("https://original.example/", store.find("always1").orElseThrow().url());
    }

    @Test
    void shortUrlStripsTrailingSlashOfBase() {
        assertEquals("http://x/abc", new LinkService(new LinkStore(), () -> "abc", "http://x/").shortUrl("abc"));
    }

    @Test
    void generatorProducesSevenAlphanumerics() {
        assertTrue(new RandomCodeGenerator().next().matches("[A-Za-z0-9]{7}"));
    }
}
