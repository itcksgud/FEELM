package com.feelm.catalog.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;

@Configuration
public class CatalogConfiguration {
    @Bean
    Clock catalogClock(@Value("${catalog.fixed-clock:}") String fixedClock) {
        if (fixedClock == null || fixedClock.isBlank()) {
            return Clock.systemUTC();
        }
        return Clock.fixed(Instant.parse(fixedClock), ZoneOffset.UTC);
    }
}
