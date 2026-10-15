package com.example.shortener;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

class LinkStoreTest {
    @Test
    void addIsFirstWriterWins() {
        LinkStore store = new LinkStore();
        assertTrue(store.add("abc", "https://one.example/"));
        assertFalse(store.add("abc", "https://two.example/"));
        assertEquals("https://one.example/", store.find("abc").orElseThrow().url());
        assertEquals(0, store.find("abc").orElseThrow().clicks().get());
    }

    @Test
    void findUnknownIsEmpty() {
        assertTrue(new LinkStore().find("missing").isEmpty());
    }
}
