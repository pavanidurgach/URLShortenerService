package com.example.shortener;

import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;
import org.springframework.stereotype.Component;

/** In-memory and ephemeral: everything is lost on restart and the map is unbounded. */
@Component
public class LinkStore {
    private final ConcurrentHashMap<String, Link> links = new ConcurrentHashMap<>();

    /** Atomically stores the link; returns false (and changes nothing) if the code is taken. */
    public boolean add(String code, String url) {
        return links.putIfAbsent(code, new Link(code, url, new AtomicLong())) == null;
    }

    public Optional<Link> find(String code) {
        return Optional.ofNullable(links.get(code));
    }
}
