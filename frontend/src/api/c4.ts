import type { components } from "./schema";

export type CreateEmailSignupRequest = components["schemas"]["CreateEmailSignupRequest"];
export type PendingEmailSignup = components["schemas"]["PendingEmailSignup"];
export type VerifyEmailRequest = components["schemas"]["VerifyEmailRequest"];
export type EmailVerificationResult = components["schemas"]["EmailVerificationResult"];
export type VerificationDeliveryState = components["schemas"]["VerificationDeliveryState"];
export type EmailLoginRequest = components["schemas"]["EmailLoginRequest"];
export type AuthenticationResult = components["schemas"]["AuthenticationResult"];
export type MyMembership = components["schemas"]["MyMembership"];
export type OnboardingMoviePage = components["schemas"]["OnboardingMoviePage"];
export type OnboardingPreferenceInput = components["schemas"]["OnboardingPreferenceInput"];
export type OnboardingState = components["schemas"]["OnboardingState"];
export type MyOttSubscriptionSet = components["schemas"]["MyOttSubscriptionSet"];

type ApiErrorPayload = { code?: string; message?: string; currentRevision?: number | null };

export class C4ApiError extends Error {
  constructor(readonly status: number, readonly payload?: ApiErrorPayload) {
    super(payload?.message ?? "요청을 처리하지 못했어요.");
    this.name = "C4ApiError";
  }
}

async function result<T>(pending: Promise<Response>): Promise<T> {
  const response = await pending;
  if (response.ok) return (response.status === 204 ? undefined : await response.json()) as T;
  let payload: ApiErrorPayload | undefined;
  try { payload = await response.json() as ApiErrorPayload; } catch { payload = undefined; }
  throw new C4ApiError(response.status, payload);
}

function idempotencyKey() {
  return globalThis.crypto?.randomUUID?.() ?? `request-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function origin() {
  return globalThis.location?.origin ?? "http://localhost";
}

function cookie(name: string) {
  return document.cookie.split(";").map((part) => part.trim()).find((part) => part.startsWith(`${name}=`))?.slice(name.length + 1);
}

export interface C4Api {
  signUp(input: CreateEmailSignupRequest): Promise<PendingEmailSignup>;
  verify(input: VerifyEmailRequest): Promise<EmailVerificationResult>;
  resend(signupId: string): Promise<VerificationDeliveryState>;
  login(input: EmailLoginRequest): Promise<AuthenticationResult>;
  refresh(): Promise<AuthenticationResult>;
  logout(): Promise<void>;
  getMembership(): Promise<MyMembership>;
  updateNickname(nickname: string, expectedRevision: number): Promise<MyMembership>;
  listOnboardingMovies(): Promise<OnboardingMoviePage>;
  replaceOnboardingPreferences(input: { catalogVersion: string; selectionPolicyVersion: string; preferences: OnboardingPreferenceInput[] }, expectedRevision: number): Promise<OnboardingState>;
  completeOnboarding(completionMode: "SUBMITTED" | "SKIPPED", expectedPreferenceCount: number, expectedRevision: number): Promise<OnboardingState>;
  getOttSubscriptions(): Promise<MyOttSubscriptionSet>;
  replaceOttSubscriptions(selectionMode: "CONFIGURED" | "SKIPPED", providerIds: string[], expectedRevision: number): Promise<MyOttSubscriptionSet>;
}

export class HttpC4Api implements C4Api {
  constructor(private readonly baseUrl = import.meta.env.VITE_API_BASE_URL ?? "", private readonly accessToken: () => string | null) {}

  private publicHeaders(idempotent = false): HeadersInit {
    return { "Content-Type": "application/json", ...(idempotent ? { "Idempotency-Key": idempotencyKey() } : {}) };
  }

  private authHeaders(extra: Record<string, string> = {}): HeadersInit {
    const accessToken = this.accessToken();
    if (!accessToken) throw new C4ApiError(401, { code: "UNAUTHORIZED", message: "로그인이 필요해요." });
    return { "Content-Type": "application/json", Authorization: `Bearer ${accessToken}`, ...extra };
  }

  signUp(input: CreateEmailSignupRequest) {
    return result<PendingEmailSignup>(fetch(`${this.baseUrl}/api/v1/auth/sign-up`, { method: "POST", headers: this.publicHeaders(true), body: JSON.stringify(input) }));
  }
  verify(input: VerifyEmailRequest) {
    return result<EmailVerificationResult>(fetch(`${this.baseUrl}/api/v1/auth/email-verifications`, { method: "POST", headers: this.publicHeaders(true), body: JSON.stringify(input) }));
  }
  resend(signupId: string) {
    return result<VerificationDeliveryState>(fetch(`${this.baseUrl}/api/v1/auth/email-verification-resends`, { method: "POST", headers: this.publicHeaders(true), body: JSON.stringify({ signupId }) }));
  }
  login(input: EmailLoginRequest) {
    return result<AuthenticationResult>(fetch(`${this.baseUrl}/api/v1/auth/login`, { method: "POST", credentials: "include", headers: { ...this.publicHeaders(), Origin: origin() }, body: JSON.stringify(input) }));
  }
  refresh() {
    const csrf = cookie("feelm_local_csrf") ?? cookie("__Host-feelm_csrf");
    return result<AuthenticationResult>(fetch(`${this.baseUrl}/api/v1/auth/refresh`, { method: "POST", credentials: "include", headers: { Origin: origin(), "X-CSRF-Token": csrf ?? "" } }));
  }
  logout() {
    const csrf = cookie("feelm_local_csrf") ?? cookie("__Host-feelm_csrf");
    return result<void>(fetch(`${this.baseUrl}/api/v1/auth/logout`, { method: "POST", credentials: "include", headers: { Origin: origin(), ...(csrf ? { "X-CSRF-Token": csrf, "Idempotency-Key": idempotencyKey() } : {}) } }));
  }
  getMembership() { return result<MyMembership>(fetch(`${this.baseUrl}/api/v1/me`, { headers: this.authHeaders() })); }
  updateNickname(nickname: string, revision: number) {
    return result<MyMembership>(fetch(`${this.baseUrl}/api/v1/me`, { method: "PATCH", headers: this.authHeaders({ "Idempotency-Key": idempotencyKey(), "X-Expected-Revision": String(revision) }), body: JSON.stringify({ nickname }) }));
  }
  listOnboardingMovies() { return result<OnboardingMoviePage>(fetch(`${this.baseUrl}/api/v1/onboarding/movies`, { headers: this.authHeaders() })); }
  replaceOnboardingPreferences(input: { catalogVersion: string; selectionPolicyVersion: string; preferences: OnboardingPreferenceInput[] }, revision: number) {
    return result<OnboardingState>(fetch(`${this.baseUrl}/api/v1/onboarding/preferences`, { method: "PUT", headers: this.authHeaders({ "Idempotency-Key": idempotencyKey(), "X-Expected-Revision": String(revision) }), body: JSON.stringify(input) }));
  }
  completeOnboarding(completionMode: "SUBMITTED" | "SKIPPED", expectedPreferenceCount: number, revision: number) {
    return result<OnboardingState>(fetch(`${this.baseUrl}/api/v1/onboarding/complete`, { method: "POST", headers: this.authHeaders({ "Idempotency-Key": idempotencyKey(), "X-Expected-Revision": String(revision) }), body: JSON.stringify({ completionMode, expectedPreferenceCount }) }));
  }
  getOttSubscriptions() { return result<MyOttSubscriptionSet>(fetch(`${this.baseUrl}/api/v1/me/ott-subscriptions`, { headers: this.authHeaders() })); }
  replaceOttSubscriptions(selectionMode: "CONFIGURED" | "SKIPPED", providerIds: string[], revision: number) {
    return result<MyOttSubscriptionSet>(fetch(`${this.baseUrl}/api/v1/me/ott-subscriptions`, { method: "PUT", headers: this.authHeaders({ "Idempotency-Key": idempotencyKey(), "X-Expected-Revision": String(revision) }), body: JSON.stringify({ selectionMode, providerIds }) }));
  }
}
