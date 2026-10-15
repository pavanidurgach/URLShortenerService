package com.example.shortener;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest
@AutoConfigureMockMvc
class ConcurrencyTest {
    @Autowired MockMvc mvc;

    private <T> List<T> runAll(int threads, Callable<T> task) throws Exception {
        ExecutorService pool = Executors.newFixedThreadPool(threads);
        try {
            CountDownLatch ready = new CountDownLatch(threads);
            CountDownLatch go = new CountDownLatch(1);
            List<Future<T>> futures = new ArrayList<>();
            for (int i = 0; i < threads; i++) {
                futures.add(pool.submit(() -> {
                    ready.countDown();
                    go.await();
                    return task.call();
                }));
            }
            ready.await();
            go.countDown();
            List<T> out = new ArrayList<>();
            for (Future<T> f : futures) {
                out.add(f.get());
            }
            return out;
        } finally {
            pool.shutdown();
        }
    }

    @Test
    void sameAliasRaceHasExactlyOneWinner() throws Exception {
        int n = 50;
        AtomicInteger seq = new AtomicInteger();
        List<Integer> statuses = runAll(n, () -> {
            int i = seq.incrementAndGet();
            return mvc.perform(post("/api/links").contentType(MediaType.APPLICATION_JSON)
                            .content("{\"url\":\"https://example.com/w" + i + "\",\"alias\":\"race-alias\"}"))
                    .andReturn().getResponse().getStatus();
        });
        assertEquals(1, statuses.stream().filter(s -> s == 201).count());
        assertEquals(n - 1, statuses.stream().filter(s -> s == 409).count());
        String stored = mvc.perform(get("/api/links/race-alias")).andReturn().getResponse().getContentAsString();
        assertTrue(stored.matches(".*\"url\":\"https://example.com/w\\d+\".*"), stored);
    }

    @Test
    void concurrentRedirectsAreAllCounted() throws Exception {
        mvc.perform(post("/api/links").contentType(MediaType.APPLICATION_JSON)
                .content("{\"url\":\"https://example.com/c\",\"alias\":\"click-race\"}"));
        runAll(100, () -> {
            for (int i = 0; i < 10; i++) {
                mvc.perform(get("/click-race"));
            }
            return null;
        });
        mvc.perform(get("/api/links/click-race")).andExpect(jsonPath("$.clicks").value(1000));
        mvc.perform(get("/api/links/click-race")).andExpect(jsonPath("$.clicks").value(1000));
    }
}
