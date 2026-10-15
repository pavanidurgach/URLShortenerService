package com.example.shortener;

import java.security.SecureRandom;
import org.springframework.stereotype.Component;

@Component
public class RandomCodeGenerator implements CodeGenerator {
    private static final String ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    private static final int LENGTH = 7;
    private final SecureRandom random = new SecureRandom();

    @Override
    public String next() {
        StringBuilder sb = new StringBuilder(LENGTH);
        for (int i = 0; i < LENGTH; i++) {
            sb.append(ALPHABET.charAt(random.nextInt(ALPHABET.length())));
        }
        return sb.toString();
    }
}
