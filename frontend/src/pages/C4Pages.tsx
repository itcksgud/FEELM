import { useEffect, useMemo, useRef, useState, type FormEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { AppHeader, Poster } from "../components/CatalogUi";
import { useCatalogApi } from "../api/CatalogApiContext";
import { useC4 } from "../api/C4ApiContext";
import type { MyMembership, OnboardingMoviePage, OnboardingPreferenceInput, OnboardingState, PendingEmailSignup } from "../api/c4";
import styles from "../styles/c4.module.css";
import { localFeaturesEnabled } from "../config/localFeatures";

function Shell({ title, lead, children, auth = false }: { title: string; lead?: string; children: ReactNode; auth?: boolean }) {
  return <main className={`${styles.page} ${auth ? styles.authPage : ""}`}><div className={styles.inner}>{!auth && <AppHeader compact />}<section className={`${styles.panel} ${auth ? styles.authPanel : ""}`}>{auth && <Link className={styles.authBrand} to="/search" aria-label="FEELM 검색 홈">feelm<span>.</span></Link>}<p className={styles.eyebrow}>FEELM MEMBERSHIP</p><h1>{title}</h1>{lead && <p className={styles.lead}>{lead}</p>}{children}</section></div></main>;
}

function ErrorNotice({ error }: { error: unknown }) {
  if (!error) return null;
  return <p className={styles.error} role="alert">{error instanceof Error ? error.message : "요청을 처리하지 못했어요."}</p>;
}

export function ProtectedC4Route({ children }: { children: ReactNode }) {
  const { accessToken, restoreSession } = useC4();
  const location = useLocation();
  const [checking, setChecking] = useState(!accessToken);
  useEffect(() => {
    if (accessToken) { setChecking(false); return; }
    if (!checking) return;
    let active = true;
    void restoreSession().finally(() => { if (active) setChecking(false); });
    return () => { active = false; };
  }, [accessToken, checking, restoreSession]);
  if (checking) return <Shell title="세션 확인 중"><p>로그인 상태를 확인하고 있어요.</p></Shell>;
  return accessToken ? children : <Navigate to="/login" replace state={{ from: `${location.pathname}${location.search}` }} />;
}

export function SignUpPage() {
  const { api } = useC4();
  const navigate = useNavigate();
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(undefined); setBusy(true);
    const data = new FormData(event.currentTarget);
    try {
      const pending = await api.signUp({ email: String(data.get("email")), password: String(data.get("password")), nickname: String(data.get("nickname")) });
      navigate(`/verify-email?signupId=${encodeURIComponent(pending.signupId)}`, { state: { pending } });
    } catch (reason) { setError(reason); } finally { setBusy(false); }
  }
  return <Shell auth title="이메일로 시작하기" lead={localFeaturesEnabled ? "로컬 프로필에서는 Mailpit으로 인증 메일을 확인합니다." : "인증 메일로 이메일 소유를 확인합니다."}><form className={styles.form} onSubmit={submit}>
    <label>이메일<input name="email" type="email" required autoComplete="email" /></label>
    <label>닉네임<input name="nickname" minLength={2} maxLength={20} required autoComplete="nickname" /></label>
    <label>비밀번호<input name="password" type="password" minLength={15} maxLength={128} required autoComplete="new-password" /></label>
    <ErrorNotice error={error} /><button className={styles.primary} disabled={busy}>{busy ? "가입 요청 중…" : "인증 메일 받기"}</button>
  </form><p className={styles.foot}>이미 가입했나요? <Link to="/login">로그인</Link></p></Shell>;
}

type VerifyState = { pending?: PendingEmailSignup };

export function VerifyEmailPage() {
  const { api } = useC4();
  const navigate = useNavigate();
  const location = useLocation();
  const [search] = useSearchParams();
  const pending = (location.state as VerifyState | null)?.pending;
  const signupId = search.get("signupId") ?? pending?.signupId ?? "";
  const [secret] = useState(() => {
    const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const value = hash.get("verificationSecret") ?? hash.get("secret") ?? "";
    if (window.location.hash) window.history.replaceState(window.history.state, "", `${window.location.pathname}${window.location.search}`);
    return value;
  });
  const [error, setError] = useState<unknown>();
  const [message, setMessage] = useState(secret ? "인증 링크를 확인했습니다." : localFeaturesEnabled ? "Mailpit에서 최신 인증 링크를 열어 주세요." : "최신 인증 링크를 열어 주세요.");
  const [busy, setBusy] = useState(false);
  async function verify() {
    if (!signupId || !secret) { setError(new Error("유효한 인증 링크가 필요해요.")); return; }
    setBusy(true); setError(undefined);
    try { await api.verify({ signupId, verificationSecret: secret }); navigate("/login", { replace: true, state: { verified: true } }); }
    catch (reason) { setError(reason); } finally { setBusy(false); }
  }
  async function resend() {
    if (!signupId) { setError(new Error("가입 요청 식별자가 없어요.")); return; }
    setBusy(true); setError(undefined);
    try { await api.resend(signupId); setMessage(localFeaturesEnabled ? "새 인증 메일을 요청했습니다. Mailpit에서 확인해 주세요." : "새 인증 메일을 요청했습니다."); }
    catch (reason) { setError(reason); } finally { setBusy(false); }
  }
  return <Shell auth title="이메일 인증" lead={pending ? `${pending.emailMasked} 주소로 인증 메일을 보냈어요.` : message}>
    {localFeaturesEnabled && <a className={styles.mailpit} href="http://localhost:8025" target="_blank" rel="noreferrer">Mailpit 받은편지함 열기</a>}
    <p className={styles.note}>{message} 인증 비밀값은 주소에서 즉시 제거되며 서버 POST body로만 전송됩니다.</p>
    <ErrorNotice error={error} /><div className={styles.actions}><button className={styles.primary} disabled={busy || !secret} onClick={verify}>인증 완료</button><button className={styles.secondary} disabled={busy} onClick={resend}>메일 다시 받기</button></div>
  </Shell>;
}

export function LoginPage() {
  const { api, acceptAuthentication } = useC4();
  const navigate = useNavigate();
  const location = useLocation();
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(undefined);
    const data = new FormData(event.currentTarget);
    try {
      const authentication = await api.login({ email: String(data.get("email")), password: String(data.get("password")) });
      acceptAuthentication(authentication);
      const from = (location.state as { from?: string } | null)?.from;
      navigate(from ?? "/me/profile", { replace: true });
    } catch (reason) { setError(reason); } finally { setBusy(false); }
  }
  return <Shell auth title="로그인" lead={(location.state as { verified?: boolean } | null)?.verified ? "이메일 인증이 완료됐어요. 로그인해 주세요." : "FEELM 기록을 이어가세요."}><form className={styles.form} onSubmit={submit}>
    <label>이메일<input name="email" type="email" required autoComplete="email" /></label><label>비밀번호<input name="password" type="password" required autoComplete="current-password" /></label>
    <ErrorNotice error={error} /><button className={styles.primary} disabled={busy}>{busy ? "로그인 중…" : "로그인"}</button>
  </form><p className={styles.foot}>처음인가요? <Link to="/sign-up">회원가입</Link></p></Shell>;
}

export function MembershipPage() {
  const { api, membership: sessionMembership, clearSession } = useC4();
  const navigate = useNavigate();
  const [membership, setMembership] = useState<MyMembership | null>(sessionMembership);
  const [error, setError] = useState<unknown>();
  useEffect(() => { let active = true; void api.getMembership().then((value) => { if (active) setMembership(value); }).catch(setError); return () => { active = false; }; }, [api]);
  async function rename(event: FormEvent<HTMLFormElement>) { event.preventDefault(); if (!membership) return; const nickname = String(new FormData(event.currentTarget).get("nickname")); try { setMembership(await api.updateNickname(nickname, membership.profileRevision)); } catch (reason) { setError(reason); } }
  async function logout() { try { await api.logout(); } finally { clearSession(); navigate("/login", { replace: true }); } }
  return <Shell title="내 멤버십" lead={membership ? `${membership.emailMasked} · ${membership.membershipStatus}` : "멤버십을 불러오는 중입니다."}><ErrorNotice error={error} />{membership && <>
    <form className={styles.inlineForm} onSubmit={rename}><label>닉네임<input name="nickname" defaultValue={membership.nickname} minLength={2} maxLength={20} required /></label><button className={styles.secondary}>변경</button></form>
    <dl className={styles.summary}><div><dt>온보딩</dt><dd>{membership.onboarding.status}</dd></div><div><dt>선호 기록</dt><dd>{membership.onboarding.preferenceCount}개</dd></div></dl>
    <div className={styles.actions}><Link className={styles.primaryLink} to="/onboarding/movies">취향 설정</Link><button className={styles.textButton} onClick={logout}>로그아웃</button></div>
  </>}</Shell>;
}

type OnboardingRouteState = { onboarding: OnboardingState; completionMode: "SUBMITTED" | "SKIPPED" };

type OnboardingPreference = "LIKE" | "DISLIKE";
type BoardPoint = { x: number; y: number };
type DragState = {
  movieId: string;
  pointerId: number;
  startX: number;
  startY: number;
  offsetX: number;
  offsetY: number;
  moved: boolean;
};

const likeButtonPoints: BoardPoint[] = [
  { x: 34, y: 23 }, { x: 66, y: 23 }, { x: 30, y: 40 }, { x: 70, y: 40 }, { x: 40, y: 54 },
  { x: 60, y: 54 }, { x: 50, y: 17 }, { x: 23, y: 31 }, { x: 77, y: 31 }, { x: 50, y: 48 },
];
const dislikeButtonPoints: BoardPoint[] = [
  { x: 8, y: 12 }, { x: 92, y: 12 }, { x: 8, y: 35 }, { x: 92, y: 35 }, { x: 10, y: 58 },
  { x: 90, y: 58 }, { x: 20, y: 67 }, { x: 80, y: 67 }, { x: 50, y: 66 }, { x: 50, y: 7 },
];

export function classifyPreferenceDistance(distance: number, radius: number): OnboardingPreference {
  return distance <= radius ? "LIKE" : "DISLIKE";
}

function waitingPoint(index: number, count: number): BoardPoint {
  const columns = Math.min(5, Math.max(1, count));
  const row = Math.floor(index / columns);
  const rowStart = row * columns;
  const itemsInRow = Math.min(columns, count - rowStart);
  const column = index - rowStart;
  const rows = Math.ceil(count / columns);
  return {
    x: ((column + 0.5) / itemsInRow) * 100,
    y: rows === 1 ? 85 : 75 + row * 15,
  };
}

function PreferenceDistanceBoard({
  items,
  choices,
  onChoice,
}: {
  items: OnboardingMoviePage["items"];
  choices: Record<string, OnboardingPreference>;
  onChoice: (movieId: string, preference: OnboardingPreference | null) => void;
}) {
  const boardRef = useRef<HTMLDivElement>(null);
  const circleRef = useRef<HTMLDivElement>(null);
  const trayRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<DragState | null>(null);
  const [placements, setPlacements] = useState<Record<string, BoardPoint>>({});
  const [activeMovieId, setActiveMovieId] = useState(items[0]?.movieId ?? "");
  const activeMovie = items.find((movie) => movie.movieId === activeMovieId) ?? items[0];
  const selectedCount = Object.keys(choices).length;

  function movePlacement(movieId: string, point: BoardPoint) {
    setPlacements((current) => ({ ...current, [movieId]: point }));
  }

  function clearPlacement(movieId: string) {
    setPlacements((current) => {
      const next = { ...current };
      delete next[movieId];
      return next;
    });
    onChoice(movieId, null);
  }

  function chooseWithButton(movieId: string, preference: OnboardingPreference) {
    const index = Math.max(0, items.findIndex((movie) => movie.movieId === movieId));
    movePlacement(movieId, (preference === "LIKE" ? likeButtonPoints : dislikeButtonPoints)[index % 10]);
    onChoice(movieId, preference);
  }

  function pointerPoint(event: ReactPointerEvent<HTMLButtonElement>, drag: DragState): BoardPoint | null {
    const board = boardRef.current;
    if (!board) return null;
    const rect = board.getBoundingClientRect();
    const centerX = event.clientX - drag.offsetX;
    const centerY = event.clientY - drag.offsetY;
    return {
      x: Math.min(94, Math.max(6, ((centerX - rect.left) / rect.width) * 100)),
      y: Math.min(94, Math.max(6, ((centerY - rect.top) / rect.height) * 100)),
    };
  }

  function beginDrag(movieId: string, event: ReactPointerEvent<HTMLButtonElement>) {
    if (event.button !== 0) return;
    const rect = event.currentTarget.getBoundingClientRect();
    setActiveMovieId(movieId);
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      movieId,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      offsetX: event.clientX - (rect.left + rect.width / 2),
      offsetY: event.clientY - (rect.top + rect.height / 2),
      moved: false,
    };
  }

  function dragMovie(event: ReactPointerEvent<HTMLButtonElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) >= 6) drag.moved = true;
    if (!drag.moved) return;
    const point = pointerPoint(event, drag);
    if (point) movePlacement(drag.movieId, point);
  }

  function finishDrag(event: ReactPointerEvent<HTMLButtonElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    dragRef.current = null;
    if (!drag.moved) return;

    const cardCenterX = event.clientX - drag.offsetX;
    const cardCenterY = event.clientY - drag.offsetY;
    const trayRect = trayRef.current?.getBoundingClientRect();
    if (trayRect && cardCenterY >= trayRect.top) {
      clearPlacement(drag.movieId);
      return;
    }

    const circleRect = circleRef.current?.getBoundingClientRect();
    if (!circleRect) return;
    const distance = Math.hypot(cardCenterX - (circleRect.left + circleRect.width / 2), cardCenterY - (circleRect.top + circleRect.height / 2));
    onChoice(drag.movieId, classifyPreferenceDistance(distance, circleRect.width / 2));
  }

  return <section className={styles.preferenceSection} aria-labelledby="distance-board-title">
    <div className={styles.preferenceHeading}>
      <div><p className={styles.preferenceKicker}>DISTANCE MAP</p><h2 id="distance-board-title">포스터를 취향 거리로 놓아 보세요</h2></div>
      <p className={styles.preferenceProgress} role="status"><strong>{selectedCount}</strong> / 10 선택</p>
    </div>
    <p className={styles.distanceHelp} id="distance-help">중앙의 나와 가까운 원 안은 좋아요, 원 밖은 싫어요예요. 아래 대기 공간으로 돌려놓으면 미선택입니다.</p>
    <div className={styles.preferenceBoard} ref={boardRef}>
      <div className={styles.preferenceCircle} ref={circleRef} aria-hidden="true">
        <span className={styles.insideLabel}>가까운 취향 · 좋아요</span>
        <div className={styles.preferenceUser}><span>나</span></div>
      </div>
      <span className={styles.outsideLabel} aria-hidden="true">먼 취향 · 싫어요</span>
      <div className={styles.waitingTray} ref={trayRef} aria-hidden="true"><span>아직 고르지 않은 영화</span></div>
      {items.map((movie, index) => {
        const point = placements[movie.movieId] ?? waitingPoint(index, items.length);
        const preference = choices[movie.movieId];
        const stateLabel = preference === "LIKE" ? "좋아요" : preference === "DISLIKE" ? "싫어요" : "미선택";
        return <div
          className={styles.preferenceCardSlot}
          data-preference={preference ?? "UNSELECTED"}
          data-active={movie.movieId === activeMovie?.movieId}
          key={movie.movieId}
          style={{ left: `${point.x}%`, top: `${point.y}%` }}
        >
          <button
            className={styles.preferenceCard}
            type="button"
            aria-label={`${movie.title}, 현재 ${stateLabel}. 취향 공간에서 드래그하기`}
            aria-describedby="distance-help"
            onFocus={() => setActiveMovieId(movie.movieId)}
            onPointerDown={(event) => beginDrag(movie.movieId, event)}
            onPointerMove={dragMovie}
            onPointerUp={finishDrag}
            onPointerCancel={() => { dragRef.current = null; }}
            onDragStart={(event) => event.preventDefault()}
          >
            <Poster src={movie.posterUrl} title={movie.title} />
            <span className={styles.preferenceTitle}>{movie.title}</span>
            <span className={styles.preferenceState}>{stateLabel}</span>
          </button>
          {preference && <button className={styles.removePreference} type="button" aria-label={`${movie.title} 미선택으로 되돌리기`} onClick={() => clearPlacement(movie.movieId)}>⊖</button>}
        </div>;
      })}
    </div>
    {activeMovie && <div className={styles.preferenceControls} aria-label={`${activeMovie.title} 선택`}>
      <div><span>선택한 영화</span><strong>{activeMovie.title}</strong></div>
      <div className={styles.preferenceButtons}>
        <button type="button" aria-pressed={choices[activeMovie.movieId] === "DISLIKE"} onClick={() => chooseWithButton(activeMovie.movieId, "DISLIKE")}>싫어요</button>
        <button type="button" aria-pressed={!choices[activeMovie.movieId]} onClick={() => clearPlacement(activeMovie.movieId)}>미선택</button>
        <button type="button" aria-pressed={choices[activeMovie.movieId] === "LIKE"} onClick={() => chooseWithButton(activeMovie.movieId, "LIKE")}>좋아요</button>
      </div>
    </div>}
  </section>;
}

export function OnboardingMoviesPage() {
  const { api } = useC4(); const navigate = useNavigate();
  const [page, setPage] = useState<Awaited<ReturnType<typeof api.listOnboardingMovies>> | null>(null);
  const [membership, setMembership] = useState<MyMembership | null>(null);
  const [choices, setChoices] = useState<Record<string, OnboardingPreference>>({});
  const [error, setError] = useState<unknown>();
  useEffect(() => { let active = true; void Promise.all([api.listOnboardingMovies(), api.getMembership()]).then(([movies, member]) => { if (active) { setPage(movies); setMembership(member); } }).catch(setError); return () => { active = false; }; }, [api]);
  function choose(movieId: string, preference: OnboardingPreference | null) {
    setChoices((current) => {
      const next = { ...current };
      if (preference) next[movieId] = preference;
      else delete next[movieId];
      return next;
    });
  }
  async function next(mode: "SUBMITTED" | "SKIPPED") {
    if (!membership) return;
    try {
      let onboarding: OnboardingState = { status: membership.onboarding.status, preferenceCount: membership.onboarding.preferenceCount, likeCount: 0, dislikeCount: 0, revision: membership.onboarding.revision, recommendationProjection: "NOT_REQUESTED" };
      if (mode === "SUBMITTED") {
        const preferences = Object.entries(choices).map(([movieId, preference]) => ({ movieId, preference } as OnboardingPreferenceInput));
        if (!page || preferences.length < 1 || preferences.length > 10) throw new Error("영화를 1개 이상 10개 이하로 선택해 주세요.");
        onboarding = await api.replaceOnboardingPreferences({ catalogVersion: page.catalogVersion, selectionPolicyVersion: page.selectionPolicyVersion, preferences }, membership.onboarding.revision);
      }
      navigate("/onboarding/ott", { state: { onboarding, completionMode: mode } satisfies OnboardingRouteState });
    } catch (reason) { setError(reason); }
  }
  return <Shell title="취향 초기 설정" lead="영화를 나와의 거리로 배치해 첫 취향을 알려 주세요. 이 입력은 일반 별점과 분리해 저장합니다."><ErrorNotice error={error} />{!page ? <p>영화 목록을 불러오는 중…</p> : page.items.length === 0 ? <p className={styles.emptyPreference}>배치할 수 있는 영화를 찾지 못했어요. 잠시 후 다시 시도하거나 건너뛸 수 있어요.</p> : <PreferenceDistanceBoard items={page.items} choices={choices} onChoice={choose} />}
    <div className={styles.actions}><button className={styles.primary} onClick={() => next("SUBMITTED")}>선택 저장 ({Object.keys(choices).length})</button><button className={styles.secondary} onClick={() => next("SKIPPED")}>건너뛰기</button></div></Shell>;
}

export function OnboardingOttPage() {
  const { api } = useC4(); const catalog = useCatalogApi(); const navigate = useNavigate(); const location = useLocation();
  const routeState = location.state as OnboardingRouteState | null;
  const [providers, setProviders] = useState<Awaited<ReturnType<typeof catalog.listOttProviders>>["items"]>([]);
  const [subscription, setSubscription] = useState<Awaited<ReturnType<typeof api.getOttSubscriptions>> | null>(null);
  const [selected, setSelected] = useState<string[]>([]); const [error, setError] = useState<unknown>();
  useEffect(() => { let active = true; void Promise.all([catalog.listOttProviders(), api.getOttSubscriptions()]).then(([list, current]) => { if (active) { setProviders(list.items); setSubscription(current); setSelected(current.providerIds); } }).catch(setError); return () => { active = false; }; }, [api, catalog]);
  const selectedSet = useMemo(() => new Set(selected), [selected]);
  function toggle(id: string) { setSelected((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id]); }
  async function finish(selectionMode: "CONFIGURED" | "SKIPPED") {
    if (!subscription || !routeState) { setError(new Error("온보딩 상태를 다시 확인해 주세요.")); return; }
    try {
      await api.replaceOttSubscriptions(selectionMode, selectionMode === "SKIPPED" ? [] : selected, subscription.revision);
      await api.completeOnboarding(routeState.completionMode, routeState.onboarding.preferenceCount, routeState.onboarding.revision);
      navigate("/onboarding/complete", { replace: true });
    } catch (reason) { setError(reason); }
  }
  return <Shell title="이용 중인 OTT" lead="KR 구독 목록 전체를 한 번에 교체합니다. 구매·대여는 포함하지 않습니다."><ErrorNotice error={error} /><div className={styles.providerList}>{providers.map((provider) => <label key={provider.providerId}><input type="checkbox" checked={selectedSet.has(provider.providerId)} onChange={() => toggle(provider.providerId)} />{provider.name}</label>)}</div><div className={styles.actions}><button className={styles.primary} onClick={() => finish("CONFIGURED")}>구독 목록 저장</button><button className={styles.secondary} onClick={() => finish("SKIPPED")}>OTT 선택 건너뛰기</button></div></Shell>;
}

export function OnboardingCompletePage() { return <Shell title="준비가 끝났어요" lead="온보딩 상태가 저장됐습니다. 추천 품질이나 예상 별점을 과장하지 않고, 기록을 시작할 수 있어요."><div className={styles.actions}><Link className={styles.primaryLink} to="/me/profile">내 멤버십</Link><Link className={styles.secondaryLink} to="/search">영화 둘러보기</Link></div></Shell>; }
