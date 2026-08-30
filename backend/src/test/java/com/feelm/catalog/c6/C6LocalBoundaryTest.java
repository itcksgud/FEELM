package com.feelm.catalog.c6;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.feelm.catalog.api.ApiException;
import com.feelm.catalog.c6.api.C6Controller;
import com.feelm.catalog.c6.service.C6LoopbackGuard;
import com.feelm.catalog.security.C1RequiredAuthFilter;
import com.feelm.catalog.security.CatalogUserContext;
import com.feelm.catalog.security.CatalogUserContextResolver;
import jakarta.servlet.http.HttpServletRequest;
import org.junit.jupiter.api.Test;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.core.env.MapPropertySource;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

class C6LocalBoundaryTest {
    private static final String PATH = "/api/v1/me/recommendation-interpretation-experiment";

    @Test
    void c1FilterRequiresAuthenticationForExperimentPath() throws Exception {
        CatalogUserContextResolver resolver = new CatalogUserContextResolver("fake", List.of());
        MockMvc mvc = MockMvcBuilders.standaloneSetup(new ActorEchoController())
                .addFilters(new C1RequiredAuthFilter(resolver, new ObjectMapper()))
                .build();

        mvc.perform(get(PATH))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("UNAUTHORIZED"));
        mvc.perform(get(PATH).header("Authorization", "Bearer test-c1-owner-token"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.authenticated").value(true));
    }

    @Test
    void disabledPropertyDoesNotRegisterExternalController() {
        try (AnnotationConfigApplicationContext context = new AnnotationConfigApplicationContext()) {
            context.getEnvironment().setActiveProfiles("local");
            context.getEnvironment().getPropertySources().addFirst(new MapPropertySource(
                    "test", java.util.Map.of("catalog.c6.local.enabled", "false")
            ));
            context.register(C6Controller.class);
            context.refresh();
            assertThat(context.getBeanNamesForType(C6Controller.class)).isEmpty();
        }
    }

    @Test
    void loopbackGuardRejectsUnsafeBindRemoteAndOrigin() {
        C6LoopbackGuard safe = new C6LoopbackGuard("127.0.0.1");
        safe.validateBind();
        safe.requireLocal("127.0.0.1", "http://localhost:5173");

        C6LoopbackGuard unsafe = new C6LoopbackGuard("0.0.0.0");
        assertThatThrownBy(unsafe::validateBind).isInstanceOf(IllegalStateException.class);
        assertThatThrownBy(() -> safe.requireLocal("10.0.0.4", null))
                .isInstanceOfSatisfying(ApiException.class,
                        error -> assertThat(error.status().value()).isEqualTo(404));
        assertThatThrownBy(() -> safe.requireLocal("127.0.0.1", "https://example.com"))
                .isInstanceOfSatisfying(ApiException.class,
                        error -> assertThat(error.status().value()).isEqualTo(404));
    }

    @RestController
    static final class ActorEchoController {
        @GetMapping(PATH)
        CatalogUserContext get(HttpServletRequest request) {
            return (CatalogUserContext) request.getAttribute(C1RequiredAuthFilter.ACTOR_ATTRIBUTE);
        }
    }
}
