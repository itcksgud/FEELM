package com.feelm.catalog.c4.config;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class C4PropertiesTest {
    @Test
    void localProfileGeneratesOneProcessScopedDeliveryKeyWhenNoKeyWasProvided() {
        C4Properties properties = properties(true, true, "");

        properties.validate();

        assertThat(properties.deliveryKey()).hasSize(32);
        assertThat(properties.deliveryKey()).isEqualTo(properties.deliveryKey());
    }

    @Test
    void nonLocalActivationRemainsFailClosedEvenWhenAKeyIsProvided() {
        C4Properties properties = properties(true, false,
                "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=");

        assertThatThrownBy(properties::validate)
                .isInstanceOf(IllegalStateException.class)
                .hasMessage("C4 production activation is not authorized");
    }

    private static C4Properties properties(boolean enabled, boolean local, String key) {
        return new C4Properties(enabled, local, "http://127.0.0.1:5173", key,
                "local-v1", false, "", 1025, "no-reply@feelm.test");
    }
}
