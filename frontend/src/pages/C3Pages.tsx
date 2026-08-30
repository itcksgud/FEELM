import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { C3_LOCAL_ACTORS, c3ErrorMessage, createC3Id, type C3Party } from "../api/c3";
import { useC3 } from "../api/C3ApiContext";
import { useCatalogApi } from "../api/CatalogApiContext";
import { AppHeader, ErrorPanel, InlineLoader, Poster } from "../components/CatalogUi";
import styles from "../styles/c3.module.css";

function C3Shell({ children }: { children: React.ReactNode }) {
  const { actorId, setActorId } = useC3();
  const queryClient = useQueryClient();
  return <main className={styles.page}><div className={styles.inner}>
    <AppHeader />
    <aside className={styles.localBar} aria-label="로컬 테스트 사용자">
      <strong>로컬 테스트 사용자</strong>
      <select aria-label="로컬 테스트 사용자 선택" value={actorId} onChange={(event) => { queryClient.removeQueries({ queryKey: ["c3"] }); setActorId(event.target.value); }}>
        {C3_LOCAL_ACTORS.map((actor) => <option key={actor.actorId} value={actor.actorId}>{actor.nickname}</option>)}
      </select>
      <nav aria-label="로컬 Party 메뉴"><Link to="/me/parties">Party</Link><Link to="/me/party-invitations">받은 초대</Link><Link to="/me/ott-comparisons/new">OTT 비교</Link></nav>
    </aside>
    {children}
  </div></main>;
}

function Hero({ eyebrow, title, description }: { eyebrow: string; title: string; description: string }) {
  return <header className={styles.hero}><p>{eyebrow}</p><h1>{title}</h1><span>{description}</span></header>;
}

function ProviderFields({ selected, onChange }: { selected: string[]; onChange: (ids: string[]) => void }) {
  const catalog = useCatalogApi();
  const query = useQuery({ queryKey: ["c3", "providers"], queryFn: ({ signal }) => catalog.listOttProviders(signal) });
  if (query.isPending) return <InlineLoader label="OTT 목록 불러오는 중" />;
  if (query.isError) return <p role="alert">OTT 목록을 불러오지 못했어요.</p>;
  return <fieldset className={styles.providerFields}><legend>비교할 OTT (2~4개)</legend>
    {query.data.items.map((provider) => <label key={provider.providerId}>
      <input type="checkbox" checked={selected.includes(provider.providerId)} onChange={() => onChange(selected.includes(provider.providerId) ? selected.filter((id) => id !== provider.providerId) : selected.length < 4 ? [...selected, provider.providerId] : selected)} />
      <span>{provider.name}</span>
    </label>)}
    <p role="status">{selected.length}개 선택</p>
  </fieldset>;
}

export function OttComparisonCreatePage() {
  const { api } = useC3();
  const navigate = useNavigate();
  const [providers, setProviders] = useState<string[]>([]);
  const mutation = useMutation({ mutationFn: () => api.createOttComparison(providers, createC3Id()), onSuccess: (data) => navigate(`/me/ott-comparisons/${data.comparisonId}`) });
  return <C3Shell><Hero eyebrow="LOCAL KR FLATRATE" title="OTT 영화 목록 비교" description="선택한 OTT의 실제 영화 전체를 비교해요." />
    <section className={styles.panel}><ProviderFields selected={providers} onChange={setProviders} />
      {mutation.isError && <p role="alert">{c3ErrorMessage(mutation.error)}</p>}
      <button className={styles.primary} disabled={providers.length < 2 || mutation.isPending} onClick={() => mutation.mutate()}>{mutation.isPending ? "비교 만드는 중" : "비교하기"}</button>
    </section></C3Shell>;
}

export function OttComparisonPage() {
  const { api } = useC3(); const { comparisonId = "" } = useParams();
  const query = useQuery({ queryKey: ["c3", "comparison", comparisonId], queryFn: ({ signal }) => api.getOttComparison(comparisonId, signal) });
  return <C3Shell><Hero eyebrow="ACTUAL MOVIE CATALOG" title="OTT 비교 결과" description="취향 점수가 아닌 KR 정액제 영화 목록 기준이에요." />
    {query.isPending && <InlineLoader />}{query.isError && <ErrorPanel message={c3ErrorMessage(query.error)} onRetry={() => void query.refetch()} />}
    {query.data && <section className={styles.cardGrid}>{query.data.providers.map((entry) => <article className={styles.summaryCard} key={entry.provider.providerId}><h2>{entry.provider.name}</h2><strong>{entry.movieCount}편</strong><Link to={`/me/ott-comparisons/${comparisonId}/providers/${entry.provider.providerId}/movies`}>전체 영화 보기 ({entry.movieCount})</Link></article>)}</section>}
  </C3Shell>;
}

function MovieRows({ items, baseline = false }: { items: Array<{ movie: { movieId: string; displayTitle: string; posterUrl: string | null; releaseYear: number | null }; availableProviderIds: string[]; explanation?: { availableProviderCount: number; selectedProviderCount: number; catalogPopularityRank: number } }>; baseline?: boolean }) {
  return <div className={styles.movieGrid}>{items.map((item) => <article key={item.movie.movieId} className={styles.movieCard}><Link to={`/movies/${item.movie.movieId}`}><Poster src={item.movie.posterUrl} title={item.movie.displayTitle} /><h2>{item.movie.displayTitle}</h2></Link><p>{item.movie.releaseYear ?? "연도 정보 없음"} · 제공 OTT {item.availableProviderIds.length}개</p>{baseline && item.explanation && <p className={styles.explanation}>선택한 {item.explanation.selectedProviderCount}개 OTT 중 {item.explanation.availableProviderCount}개에서 볼 수 있어요 · 인기 기준 {item.explanation.catalogPopularityRank}위</p>}</article>)}</div>;
}

export function OttComparisonMoviesPage() {
  const { api } = useC3(); const { comparisonId = "", providerId = "" } = useParams();
  const query = useInfiniteQuery({ queryKey: ["c3", "comparison-movies", comparisonId, providerId], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam, signal }) => api.listOttMovies(comparisonId, providerId, pageParam, signal), getNextPageParam: (page) => page.hasNext ? page.nextCursor ?? undefined : undefined });
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  return <C3Shell><Hero eyebrow="FULL MOVIE LIST" title="OTT 전체 영화" description="대표작이 아니라 포함된 실제 영화를 끝까지 보여줘요." />{query.isPending && <InlineLoader />}{query.isError && <ErrorPanel message={c3ErrorMessage(query.error)} onRetry={() => void query.refetch()} />}{query.data && <><p className={styles.count}>{query.data.pages[0].totalCount}편</p><MovieRows items={items} />{query.hasNextPage && <button className={styles.secondary} disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>{query.isFetchingNextPage ? "불러오는 중" : "더 보기"}</button>}</>}</C3Shell>;
}

export function PartiesPage() {
  const { api } = useC3();
  const query = useQuery({ queryKey: ["c3", "parties"], queryFn: ({ signal }) => api.listParties(signal) });
  return <C3Shell><Hero eyebrow="LOCAL PARTY" title="내 Party" description="최대 4명이 함께 실제 영화를 살펴봐요." /><div className={styles.actions}><Link className={styles.primary} to="/me/parties/new">Party 만들기</Link><Link className={styles.secondary} to="/me/party-invitations">받은 초대</Link></div>{query.isPending && <InlineLoader />}{query.isError && <ErrorPanel message={c3ErrorMessage(query.error)} onRetry={() => void query.refetch()} />}{query.data?.items.length === 0 && <p className={styles.empty}>아직 Party가 없어요.</p>}<section className={styles.list}>{query.data?.items.map((party) => <Link key={party.partyId} to={`/parties/${party.partyId}`} className={styles.listCard}><strong>{party.name}</strong><span>{party.status === "DRAFT" ? "초대 대기" : "활성"} · {party.memberCount}/4</span></Link>)}</section></C3Shell>;
}

export function PartyCreatePage() {
  const { api } = useC3(); const navigate = useNavigate(); const [name, setName] = useState(""); const [providers, setProviders] = useState<string[]>([]);
  const mutation = useMutation({ mutationFn: () => api.createParty(name.trim(), providers, createC3Id()), onSuccess: (party) => navigate(`/parties/${party.partyId}`) });
  function submit(event: FormEvent) { event.preventDefault(); if (name.trim() && providers.length >= 2) mutation.mutate(); }
  return <C3Shell><Hero eyebrow="CREATE PARTY" title="Party 만들기" description="이름과 함께 볼 OTT를 골라요." /><form className={styles.panel} onSubmit={submit}><label className={styles.textField}>Party 이름<input value={name} maxLength={60} onChange={(event) => setName(event.target.value)} /></label><ProviderFields selected={providers} onChange={setProviders} />{mutation.isError && <p role="alert">{c3ErrorMessage(mutation.error)}</p>}<button className={styles.primary} disabled={!name.trim() || providers.length < 2 || mutation.isPending}>만들기</button></form></C3Shell>;
}

export function PartyDetailPage() {
  const { api } = useC3(); const { partyId = "" } = useParams(); const queryClient = useQueryClient(); const [recipient, setRecipient] = useState<string>(C3_LOCAL_ACTORS[1].actorId);
  const partyQuery = useQuery({ queryKey: ["c3", "party", partyId], queryFn: ({ signal }) => api.getParty(partyId, signal) });
  const invitations = useQuery({ queryKey: ["c3", "party-invitations", partyId], queryFn: ({ signal }) => api.listPartyInvitations(partyId, signal), enabled: partyQuery.data?.myRole === "OWNER" });
  const invite = useMutation({ mutationFn: ({ party }: { party: C3Party }) => api.createInvitation(partyId, recipient, party.revision, createC3Id()), onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["c3", "party", partyId] }); await invitations.refetch(); } });
  const party = partyQuery.data;
  return <C3Shell><Hero eyebrow="PARTY DETAIL" title={party?.name ?? "Party"} description="로컬 테스트 Party의 구성원과 OTT 기준을 확인해요." />{partyQuery.isPending && <InlineLoader />}{partyQuery.isError && <ErrorPanel message={c3ErrorMessage(partyQuery.error)} onRetry={() => void partyQuery.refetch()} />}{party && <><section className={styles.panel}><h2>구성원 {party.memberCount}/4</h2><ul>{party.members.map((member) => <li key={member.memberId}>{member.actor.nickname} · {member.role}</li>)}</ul><Link className={styles.primary} to={`/parties/${partyId}/baseline-recommendations`}>인기·OTT 기준 영화 보기</Link></section>{party.myRole === "OWNER" && party.memberCount < 4 && <section className={styles.panel}><h2>fake actor 초대</h2><select aria-label="초대할 로컬 사용자" value={recipient} onChange={(event) => setRecipient(event.target.value)}>{C3_LOCAL_ACTORS.filter((actor) => !party.members.some((member) => member.actor.actorId === actor.actorId)).map((actor) => <option value={actor.actorId} key={actor.actorId}>{actor.nickname}</option>)}</select><button className={styles.secondary} disabled={invite.isPending} onClick={() => invite.mutate({ party })}>초대하기</button>{invite.isError && <p role="alert">{c3ErrorMessage(invite.error)}</p>}<ul>{invitations.data?.items.map((entry) => <li key={entry.invitationId}>{entry.recipient.nickname} · {entry.status}</li>)}</ul></section>}</>}</C3Shell>;
}

export function PartyInvitationsPage() {
  const { api } = useC3(); const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["c3", "my-invitations"], queryFn: ({ signal }) => api.listMyInvitations(signal) });
  const accept = useMutation({ mutationFn: (invitation: NonNullable<typeof query.data>["items"][number]) => api.acceptInvitation(invitation.invitationId, invitation.partyRevision, invitation.revision, createC3Id()), onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["c3"] }); } });
  return <C3Shell><Hero eyebrow="INVITATIONS" title="받은 초대" description="현재 선택한 local fake actor의 초대예요." />{query.isPending && <InlineLoader />}{query.isError && <ErrorPanel message={c3ErrorMessage(query.error)} onRetry={() => void query.refetch()} />}{query.data?.items.length === 0 && <p className={styles.empty}>받은 초대가 없어요.</p>}<section className={styles.list}>{query.data?.items.map((invitation) => <article className={styles.listCard} key={invitation.invitationId}><strong>{invitation.partyName}</strong><span>{invitation.inviter.nickname}의 초대 · {invitation.status}</span>{invitation.status === "PENDING" && <button className={styles.primary} disabled={accept.isPending} onClick={() => accept.mutate(invitation)}>수락</button>}</article>)}</section>{accept.isError && <p role="alert">{c3ErrorMessage(accept.error)}</p>}</C3Shell>;
}

export function PartyBaselinePage() {
  const { api } = useC3(); const { partyId = "" } = useParams();
  const query = useInfiniteQuery({ queryKey: ["c3", "baseline", partyId], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam, signal }) => api.listBaseline(partyId, pageParam, signal), getNextPageParam: (page) => page.hasNext ? page.nextCursor ?? undefined : undefined });
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  return <C3Shell><Hero eyebrow="POPULARITY · OTT COVERAGE" title="Party 영화 기준선" description="제공 OTT 수와 정해진 인기 순으로 보여줘요." />{query.isPending && <InlineLoader />}{query.isError && <ErrorPanel message={c3ErrorMessage(query.error)} onRetry={() => void query.refetch()} />}{query.data && <><p className={styles.policy}>정책: 인기·OTT 제공 범위 기준</p><MovieRows items={items} baseline />{query.hasNextPage && <button className={styles.secondary} onClick={() => void query.fetchNextPage()}>더 보기</button>}</>}</C3Shell>;
}
