package com.feelm.catalog.c4.mail;

import com.feelm.catalog.c4.config.C4Properties;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.net.Socket;
import java.nio.charset.StandardCharsets;

@Component
@ConditionalOnProperty(name = "catalog.c4.mail.enabled", havingValue = "true")
public final class C4LocalSmtpMailGateway implements C4MailGateway {
    private final C4Properties properties;
    public C4LocalSmtpMailGateway(C4Properties properties) { this.properties = properties; }

    @Override
    public void sendVerification(String recipient, String verificationLink) {
        try (Socket socket = new Socket(properties.mailHost(), properties.mailPort());
             BufferedReader reader = new BufferedReader(new InputStreamReader(socket.getInputStream(), StandardCharsets.US_ASCII));
             BufferedWriter writer = new BufferedWriter(new OutputStreamWriter(socket.getOutputStream(), StandardCharsets.UTF_8))) {
            expect(reader, 220);
            command(writer, reader, "EHLO feelm-local", 250);
            command(writer, reader, "MAIL FROM:<" + properties.mailFrom() + ">", 250);
            command(writer, reader, "RCPT TO:<" + recipient + ">", 250);
            command(writer, reader, "DATA", 354);
            writer.write("From: " + properties.mailFrom() + "\r\nTo: " + recipient
                    + "\r\nSubject: FEELM local email verification\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\n"
                    + "Open this local verification link:\r\n" + verificationLink + "\r\n.\r\n");
            writer.flush();
            expect(reader, 250);
            command(writer, reader, "QUIT", 221);
        } catch (Exception exception) {
            throw new IllegalStateException("C4 local mail capture failed", exception);
        }
    }

    private static void command(BufferedWriter writer, BufferedReader reader, String command, int expected) throws Exception {
        writer.write(command + "\r\n"); writer.flush(); expect(reader, expected);
    }
    private static void expect(BufferedReader reader, int expected) throws Exception {
        String line;
        do { line = reader.readLine(); } while (line != null && line.length() > 3 && line.charAt(3) == '-');
        if (line == null || !line.startsWith(Integer.toString(expected))) throw new IllegalStateException("SMTP protocol failure");
    }
}
