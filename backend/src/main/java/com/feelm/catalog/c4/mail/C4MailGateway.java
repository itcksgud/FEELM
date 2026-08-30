package com.feelm.catalog.c4.mail;

public interface C4MailGateway {
    void sendVerification(String recipient, String verificationLink);
}
