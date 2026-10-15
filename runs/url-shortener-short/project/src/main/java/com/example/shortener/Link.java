package com.example.shortener;

import java.util.concurrent.atomic.AtomicLong;

public record Link(String code, String url, AtomicLong clicks) {
}
