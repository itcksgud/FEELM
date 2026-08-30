package com.feelm.catalog.c4.api;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

public final class C4ApiDtos {
    private C4ApiDtos() {}

    public record CreateEmailSignupRequest(
            @NotBlank @Email @Size(max = 320) String email,
            @NotBlank @Size(min = 15, max = 128) String password,
            @NotBlank @Size(min = 2, max = 20) String nickname
    ) {}
    public record PendingEmailSignup(UUID signupId, String membershipStatus, String emailMasked,
                                     String deliveryStatus, Instant verificationExpiresAt,
                                     Instant resendAvailableAt, long revision) {}
    public record VerifyEmailRequest(@NotNull UUID signupId, @NotBlank @Size(max = 256) String verificationSecret) {}
    public record ResendEmailVerificationRequest(@NotNull UUID signupId) {}
    public record VerificationDeliveryState(UUID signupId, String deliveryStatus, Instant verificationExpiresAt,
                                            Instant resendAvailableAt, long revision) {}
    public record EmailVerificationResult(String membershipStatus, String emailMasked, String nextAction, long revision) {}
    public record EmailLoginRequest(@NotBlank @Email @Size(max = 320) String email,
                                    @NotBlank @Size(min = 15, max = 128) String password) {}
    public record AuthenticationResult(String tokenType, String accessToken, int expiresInSeconds, MyMembership membership) {}
    public record MyMembership(String membershipStatus, String emailMasked, String nickname,
                               long profileRevision, OnboardingSummary onboarding) {}
    public record UpdateNicknameRequest(@NotBlank @Size(min = 2, max = 20) String nickname) {}
    public record OnboardingMoviePage(String catalogVersion, String selectionPolicyVersion,
                                      int targetCount, List<OnboardingMovie> items) {}
    public record OnboardingMovie(UUID movieId, String title, String posterUrl) {}
    public record ReplaceOnboardingPreferencesRequest(
            @NotBlank String catalogVersion,
            @NotBlank String selectionPolicyVersion,
            @NotNull @Size(max = 10) List<@Valid OnboardingPreferenceInput> preferences
    ) {}
    public record OnboardingPreferenceInput(@NotNull UUID movieId, @NotNull Preference preference) {}
    public enum Preference { LIKE, DISLIKE }
    public record CompleteOnboardingRequest(@NotNull CompletionMode completionMode,
                                            @NotNull Integer expectedPreferenceCount) {}
    public enum CompletionMode { SUBMITTED, SKIPPED }
    public record OnboardingState(String status, int preferenceCount, int likeCount, int dislikeCount,
                                  Integer requiredPreferenceCount, Integer maximumPreferenceCount,
                                  long revision, String recommendationProjection) {}
    public record OnboardingSummary(String status, int preferenceCount, long revision) {}
    public record ReplaceOttSubscriptionsRequest(@NotNull OttSelectionMode selectionMode,
                                                  @NotNull List<@NotNull UUID> providerIds) {}
    public enum OttSelectionMode { CONFIGURED, SKIPPED }
    public record MyOttSubscriptionSet(String region, String selectionStatus, List<UUID> providerIds, long revision) {}

    public record AuthenticationEnvelope(AuthenticationResult body, String refreshToken, String csrfToken) {}
}
