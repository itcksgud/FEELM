package com.feelm.catalog.c4;

import com.feelm.catalog.c4.config.C4Properties;
import com.feelm.catalog.c4.mail.C4LocalSmtpMailGateway;
import org.junit.jupiter.api.Test;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.utility.DockerImageName;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;

import static org.assertj.core.api.Assertions.assertThat;

@Testcontainers
class C4MailpitCaptureIntegrationTest {
    private static final String IMAGE = "axllent/mailpit:v1.30.4@sha256:5a49a77c5bdbe7c5474450b4f46348d09949df3695257729c93a30369382d4f6";

    @Container
    static final GenericContainer<?> MAILPIT = new GenericContainer<>(DockerImageName.parse(IMAGE))
            .withExposedPorts(1025, 8025);

    @Test
    void credentiallessSmtpIsCapturedByThePinnedLocalMailpitImage() throws Exception {
        C4Properties properties = new C4Properties(true, true, "http://127.0.0.1:5173", "",
                "local-v1", true, MAILPIT.getHost(), MAILPIT.getMappedPort(1025), "no-reply@feelm.test");
        new C4LocalSmtpMailGateway(properties).sendVerification(
                "mailpit.capture@example.test",
                "http://127.0.0.1:5173/verify-email?signupId=00000000-0000-0000-0000-000000000001#verificationSecret=local-only"
        );

        HttpResponse<String> messages = HttpClient.newHttpClient().send(
                HttpRequest.newBuilder(URI.create("http://" + MAILPIT.getHost() + ":"
                        + MAILPIT.getMappedPort(8025) + "/api/v1/messages")).GET().build(),
                HttpResponse.BodyHandlers.ofString()
        );
        assertThat(messages.statusCode()).isEqualTo(200);
        assertThat(messages.body()).contains("mailpit.capture@example.test");
    }
}
