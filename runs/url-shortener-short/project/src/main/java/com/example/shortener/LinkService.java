package com.example.shortener;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Pattern;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

@Service
public class LinkService {
    static final int MAX_URL_LENGTH = 2048;
    static final int MAX_ATTEMPTS = 10;
    private static final Pattern ALIAS = Pattern.compile("^[A-Za-z0-9_-]{3,32}$");
    private static final Set<String> RESERVED = Set.of("api", "links", "actuator", "error", "h2-console");

    private final LinkStore store;
    private final CodeGenerator generator;
    private final String baseUrl;

    public LinkService(LinkStore store, CodeGenerator generator, @Value("${app.base-url}") String baseUrl) {
        this.store = store;
        this.generator = generator;
        this.baseUrl = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
    }

    public Link create(String url, String alias) {
        validateUrl(url);
        if (alias != null) {
            validateAlias(alias);
            if (!store.add(alias, url)) {
                throw new ApiException(HttpStatus.CONFLICT, "alias_taken", "Alias is already in use");
            }
            return store.find(alias).orElseThrow();
        }
        for (int i = 0; i < MAX_ATTEMPTS; i++) {
            String code = generator.next();
            if (store.add(code, url)) {
                return store.find(code).orElseThrow();
            }
        }
        throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "code_generation_failed", "Could not generate a unique code");
    }

    public String shortUrl(String code) {
        return baseUrl + "/" + code;
    }

    /** Counts one click and returns the target URL. */
    public String redirect(String code) {
        Link link = lookup(code);
        link.clicks().incrementAndGet();
        return link.url();
    }

    /** Does not change the click count. */
    public Link stats(String code) {
        return lookup(code);
    }

    private Link lookup(String code) {
        return store.find(code).orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "not_found", "Unknown code"));
    }

    private static void validateAlias(String alias) {
        if (!ALIAS.matcher(alias).matches() || RESERVED.contains(alias)) {
            throw invalid("Alias must be 3-32 letters, digits, '_' or '-' and not a reserved name");
        }
    }

    private static void validateUrl(String url) {
        if (url == null || url.isBlank() || url.length() > MAX_URL_LENGTH) {
            throw invalid("url must be 1-" + MAX_URL_LENGTH + " characters");
        }
        for (int i = 0; i < url.length(); i++) {
            char c = url.charAt(i);
            if (c <= 0x20 || c >= 0x7f) {
                throw invalid("url must not contain whitespace, control or non-ASCII characters");
            }
        }
        String lower = url.toLowerCase(Locale.ROOT);
        if (lower.contains("%0d") || lower.contains("%0a")) {
            throw invalid("url must not contain encoded line breaks");
        }
        URI uri;
        try {
            uri = new URI(url);
        } catch (URISyntaxException e) {
            throw invalid("url is not a valid URI");
        }
        String scheme = uri.getScheme();
        boolean httpScheme = scheme != null && (scheme.equalsIgnoreCase("http") || scheme.equalsIgnoreCase("https"));
        if (!httpScheme || uri.getHost() == null || uri.getHost().isEmpty() || uri.getRawUserInfo() != null) {
            throw invalid("url must be http(s) with a host and no credentials");
        }
    }

    private static ApiException invalid(String message) {
        return new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "invalid_request", message);
    }
}
