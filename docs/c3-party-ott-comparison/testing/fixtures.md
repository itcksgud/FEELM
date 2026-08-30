# C3 Local MVP Deterministic Fixtures

> 상태: `APPROVED` — fake data only

## Fake actor allowlist

| label | actor UUID | nickname |
| --- | --- | --- |
| OWNER | `018f6826-4da1-7c38-a846-8f794cd8b0cf` | local_owner |
| MEMBER_A | `4d85e2ae-87ce-4f48-8ac1-fabf89bb1371` | film_a |
| MEMBER_B | `bb5799ab-7654-4e01-8e0f-c1fe583d340d` | film_b |
| MEMBER_C | `85b0fa76-5b3e-4fcb-8846-807b466e757d` | film_c |
| OTHER | `83b8c4bd-7027-4b5a-86cc-82ccb574da64` | local_other |

Unknown UUID와 missing header는 401 `LOCAL_ACTOR_UNAUTHORIZED`다.

## Providers·actual movies

| label | UUID |
| --- | --- |
| Netflix | `d392a4d5-0428-4e06-aa41-aef899c06842` |
| Watcha | `4f57022d-6d8e-40b2-b7be-4ac313ef6bd0` |
| Wavve | `1f0c5888-f6f4-42a9-b661-a90cff45e303` |

`CATALOG-FIXTURE-V100`, `OTT-MAT-COMPLETE-1`은 KR FLATRATE COMPLETE다.

| movie | 실제 title | popularity rank | membership |
| --- | --- | ---: | --- |
| `6b226903-0ca4-4f5a-9bf0-50d6cedd224c` | 나우 유 씨 미 | 2 | Netflix |
| `19406c31-213f-4fe1-93f6-109f8570ec20` | The Fall | 4 | Watcha |
| `e67778c9-7b2e-42d4-9d3e-a3026b2efea3` | 인사이드 맨 | 3 | Netflix |
| `cc3ddb45-0511-46ea-bf28-95b67c9fd20f` | 공통 영화 | 1 | Netflix, Watcha |

Netflix+Watcha Party baseline expected order: 공통 영화(coverage2, rank1) → 나우 유 씨 미(rank2) →
인사이드 맨(rank3) → The Fall(rank4). Rating/behavior fixture는 없다.

## Party·comparison

- `PARTY-C3-LOCAL-DRAFT`: OWNER, Netflix+Watcha, DRAFT 1/4, revision 1.
- MEMBER_A accept 뒤 ACTIVE 2/4, revision 3(invite create + accept).
- OWNER+A+B 3/4에서 B/C concurrent accept fixture는 한 요청만 성공해 4/4다.
- `OTT-COMP-LOCAL-READY`: Netflix movieCount=3, Watcha movieCount=2.
- provider page limit=2 traversal은 Netflix 2+1, Watcha 2이며 중복/누락 0이다.
- tampered cursor/OTHER actor comparison은 각각 400/404다.

Raw token, email, MovieLens ID, Rating, vector, satisfaction field는 fixture에 없다.

