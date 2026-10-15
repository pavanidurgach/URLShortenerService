package com.example.shortener;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import java.util.Map;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class LinkController {
    public record CreateRequest(@NotBlank String url, String alias) {
    }

    private final LinkService service;

    public LinkController(LinkService service) {
        this.service = service;
    }

    @PostMapping("/api/links")
    public ResponseEntity<Map<String, String>> create(@Valid @RequestBody CreateRequest request) {
        Link link = service.create(request.url(), request.alias());
        return ResponseEntity.status(HttpStatus.CREATED).body(Map.of(
                "code", link.code(), "shortUrl", service.shortUrl(link.code()), "url", link.url()));
    }

    @GetMapping("/api/links/{code}")
    public Map<String, Object> stats(@PathVariable String code) {
        Link link = service.stats(code);
        return Map.of("code", link.code(), "url", link.url(), "clicks", link.clicks().get());
    }

    @GetMapping("/{code}")
    public ResponseEntity<Void> redirect(@PathVariable String code) {
        return ResponseEntity.status(HttpStatus.FOUND)
                .header(HttpHeaders.LOCATION, service.redirect(code))
                .header(HttpHeaders.CACHE_CONTROL, "no-store")
                .build();
    }
}
