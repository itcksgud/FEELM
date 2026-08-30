import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { C3_LOCAL_ACTORS } from "../api/c3";
import { renderCatalog } from "./renderCatalog";
import { server } from "./server";

const netflix = "d392a4d5-0428-4e06-aa41-aef899c06842";
const watcha = "4f57022d-6d8e-40b2-b7be-4ac313ef6bd0";
const comparisonId = "a7c344b7-379b-48b0-831d-d146978da3aa";
const partyId = "bda55cbb-922e-4ff1-98e6-23d5883e443d";
const invitationId = "21565851-c802-4aa6-9ae0-6737677c5a7a";

const actors = {
  owner: { actorId: C3_LOCAL_ACTORS[0].actorId, nickname: "local_owner" },
  member: { actorId: C3_LOCAL_ACTORS[1].actorId, nickname: "film_a" },
};

const providers = {
  totalCount: 2,
  items: [
    { providerId: netflix, name: "Netflix", logoUrl: null, displayPriority: 1, isSubscribed: null },
    { providerId: watcha, name: "왓챠", logoUrl: null, displayPriority: 2, isSubscribed: null },
  ],
};

const comparison = {
  comparisonId,
  status: "READY",
  region: "KR",
  monetizationType: "FLATRATE",
  catalogVersion: "CATALOG-FIXTURE-V100",
  providers: [
    { provider: { providerId: netflix, name: "Netflix" }, movieCount: 3, moviesHref: `/api/v1/me/ott-catalog-comparisons/${comparisonId}/movies?providerId=${netflix}` },
    { provider: { providerId: watcha, name: "왓챠" }, movieCount: 2, moviesHref: `/api/v1/me/ott-catalog-comparisons/${comparisonId}/movies?providerId=${watcha}` },
  ],
};

function movie(index: number, title: string) {
  return { movie: { movieId: `20000000-0000-4000-8000-${String(index).padStart(12, "0")}`, displayTitle: title, posterUrl: null, releaseYear: 2000 + index }, availableProviderIds: index === 1 ? [netflix, watcha] : [netflix] };
}

function party(member = false) {
  return {
    partyId, name: "금요일 영화", status: member ? "ACTIVE" : "DRAFT", myRole: "OWNER",
    memberCount: member ? 2 : 1, maximumMemberCount: 4, revision: member ? 3 : 1,
    providerIds: [netflix, watcha],
    members: [
      { memberId: "30000000-0000-4000-8000-000000000001", actor: actors.owner, role: "OWNER", joinedAt: "2026-08-30T00:00:00Z" },
      ...(member ? [{ memberId: "30000000-0000-4000-8000-000000000002", actor: actors.member, role: "MEMBER", joinedAt: "2026-08-30T01:00:00Z" }] : []),
    ],
    baselineHref: `/api/v1/parties/${partyId}/baseline-recommendations`,
  };
}

const invitation = {
  invitationId, partyId, partyName: "금요일 영화", inviter: actors.owner, recipient: actors.member,
  status: "PENDING", revision: 1, partyRevision: 2,
};

describe("C3 local Party and OTT vertical", () => {
  it("공통 header에서 local Party 진입 링크를 발견할 수 있다", () => {
    renderCatalog(<App />, ["/search"]);
    expect(within(screen.getByRole("navigation", { name: "주요 메뉴" })).getByRole("link", { name: "Party" })).toHaveAttribute("href", "/me/parties");
  });

  it("provider 2개로 비교를 만들고 실제 전체 영화 링크가 있는 summary로 이동한다", async () => {
    let requestBody: unknown; let actor = "";
    server.use(
      http.get("http://localhost/api/v1/ott-providers", () => HttpResponse.json(providers)),
      http.post("http://localhost/api/v1/me/ott-catalog-comparisons", async ({ request }) => { requestBody = await request.json(); actor = request.headers.get("X-Local-Actor-Id") ?? ""; return HttpResponse.json(comparison, { status: 201 }); }),
      http.get(`http://localhost/api/v1/me/ott-catalog-comparisons/${comparisonId}`, () => HttpResponse.json(comparison)),
    );
    const user = userEvent.setup(); renderCatalog(<App />, ["/me/ott-comparisons/new"]);
    await user.click(await screen.findByRole("checkbox", { name: "Netflix" }));
    await user.click(screen.getByRole("checkbox", { name: "왓챠" }));
    await user.click(screen.getByRole("button", { name: "비교하기" }));
    expect(await screen.findByRole("heading", { name: "OTT 비교 결과" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "전체 영화 보기 (3)" })).toBeInTheDocument();
    expect(requestBody).toEqual({ providerIds: [netflix, watcha] });
    expect(actor).toBe(actors.owner.actorId);
  });

  it("OTT 실제 영화는 cursor 더 보기로 중복 없이 끝까지 표시한다", async () => {
    server.use(http.get(`http://localhost/api/v1/me/ott-catalog-comparisons/${comparisonId}/movies`, ({ request }) => {
      const cursor = new URL(request.url).searchParams.get("cursor");
      return HttpResponse.json(cursor ? { comparisonId, providerId: netflix, totalCount: 3, hasNext: false, nextCursor: null, items: [movie(3, "세 번째 실제 영화")] } : { comparisonId, providerId: netflix, totalCount: 3, hasNext: true, nextCursor: "page-2", items: [movie(1, "공통 영화"), movie(2, "두 번째 실제 영화")] });
    }));
    const user = userEvent.setup(); renderCatalog(<App />, [`/me/ott-comparisons/${comparisonId}/providers/${netflix}/movies`]);
    expect(await screen.findByRole("heading", { name: "공통 영화" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "더 보기" }));
    expect(await screen.findByRole("heading", { name: "세 번째 실제 영화" })).toBeInTheDocument();
    expect(screen.getAllByRole("article")).toHaveLength(3);
  });

  it("Party owner가 allowlist fake actor를 초대하며 revision과 local actor header를 보낸다", async () => {
    let body: unknown; let actor = "";
    server.use(
      http.get(`http://localhost/api/v1/parties/${partyId}`, () => HttpResponse.json(party())),
      http.get(`http://localhost/api/v1/parties/${partyId}/invitations`, () => HttpResponse.json({ totalCount: 0, hasNext: false, nextCursor: null, items: [] })),
      http.post(`http://localhost/api/v1/parties/${partyId}/invitations`, async ({ request }) => { body = await request.json(); actor = request.headers.get("X-Local-Actor-Id") ?? ""; return HttpResponse.json(invitation, { status: 201 }); }),
    );
    const user = userEvent.setup(); renderCatalog(<App />, [`/parties/${partyId}`]);
    expect(await screen.findByText("구성원 1/4")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "초대하기" }));
    await waitFor(() => expect(body).toEqual({ recipientActorId: actors.member.actorId, expectedPartyRevision: 1 }));
    expect(actor).toBe(actors.owner.actorId);
  });

  it("local actor를 recipient로 바꿔 invitation을 수락한다", async () => {
    let body: unknown; let actor = ""; let accepted = false;
    server.use(
      http.get("http://localhost/api/v1/me/party-invitations", () => HttpResponse.json({ totalCount: 1, hasNext: false, nextCursor: null, items: [{ ...invitation, status: accepted ? "ACCEPTED" : "PENDING" }] })),
      http.post(`http://localhost/api/v1/me/party-invitations/${invitationId}/accept`, async ({ request }) => { body = await request.json(); actor = request.headers.get("X-Local-Actor-Id") ?? ""; accepted = true; return HttpResponse.json({ invitation: { ...invitation, status: "ACCEPTED", revision: 2, partyRevision: 3 }, party: { ...party(true), myRole: "MEMBER" } }); }),
    );
    const user = userEvent.setup(); renderCatalog(<App />, ["/me/party-invitations"]);
    await user.selectOptions(screen.getByRole("combobox", { name: "로컬 테스트 사용자 선택" }), actors.member.actorId);
    await user.click(await screen.findByRole("button", { name: "수락" }));
    await waitFor(() => expect(body).toEqual({ expectedPartyRevision: 2, expectedInvitationRevision: 1 }));
    expect(actor).toBe(actors.member.actorId);
  });

  it("Party baseline은 제공 OTT 수와 인기 순위만 설명한다", async () => {
    server.use(http.get(`http://localhost/api/v1/parties/${partyId}/baseline-recommendations`, () => HttpResponse.json({
      partyId, policyVersion: "CATALOG_POPULARITY_KR_FLATRATE_V1", catalogVersion: "CATALOG-FIXTURE-V100", totalCount: 1, hasNext: false, nextCursor: null,
      items: [{ ...movie(1, "공통 영화"), explanation: { availableProviderCount: 2, selectedProviderCount: 2, catalogPopularityRank: 1, policyVersion: "CATALOG_POPULARITY_KR_FLATRATE_V1" } }],
    })));
    renderCatalog(<App />, [`/parties/${partyId}/baseline-recommendations`]);
    expect(await screen.findByRole("heading", { name: "공통 영화" })).toBeInTheDocument();
    expect(screen.getByText(/선택한 2개 OTT 중 2개에서 볼 수 있어요 · 인기 기준 1위/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/예상 별점|만족도|효용|공정성/);
  });
});
