# REC038 — 8개 안의 실제 군집과 설명

상태: DRAFT — 봉인 결과의 읽기용 색인. 개인 연구 자료.

각 방법의 01~08은 그 방법 안에서만 쓰는 번호다. 서로 같은 맛이나 색을 뜻하지 않는다.
설명은 군집마다 평가 영화와 겹치지 않는 hash 4편으로 만들었다. 상위 장르·태그는 전체 native 군집의
c-TF-IDF 설명 자료이며 블라인드 설명자와 평가자는 보지 않았다. 대표어가 있다고 원문 공통 내용이 입증되지는 않는다.
태그 전용 안의 대표어 비교 모집단은 태그 지원 61,942편이고 다른 안과 분모가 다르다.
E는 방법마다 같은 128편, D는 방법별 군집에서 뽑은 설명용 4편이다. D의 총256슬롯은 고유85편이다.
이 문서의 영화 제목과 배정은 판정 봉인 이후 연결했다. 원문·정확한 구절은 ignored 산출물에 보존한다.

## 기존 단순 장르 규칙 (RULE_GENRE)

| 군집 | 입력 근거 배정 수 | 장르 상위 3개 | 태그 상위 5개 | 고정 D 설명 | D 설명 상태 | E 영화 수 |
| --- | ---: | --- | --- | --- | --- | ---: |
| 01 | 8,665 | 액션, 모험, 스릴러 | martial arts, sequel, revenge, based on novel or book, kung fu | 폭력적인 세력이나 생존의 위협에 맞서 싸우는 이야기. | 넓은 공통 범주 | 12 |
| 02 | 25,483 | 드라마, 로맨스, TV 영화 | woman director, based on novel or book, lgbt, gay theme, based on true story | 가족 안에서 겪는 위기와 소외를 통해 관계의 의미를 드러내는 이야기. | 넓은 공통 범주 | 45 |
| 03 | 18,342 | 코미디, 가족, 로맨스 | woman director, musical, christmas, dark comedy, romcom | 친밀한 관계가 얽힌 만남과 함께 지내는 상황에서 갈등이나 소동을 겪는 이야기. | 넓은 공통 범주 | 22 |
| 04 | 2,542 | 서부, 전쟁, 역사 | world war ii, spaghetti western, 19th century, based on true story, native american | 역사적 시대를 배경으로 극한 위험을 견디며 적대 세력과 싸우는 이야기. | 넓은 공통 범주 | 5 |
| 05 | 9,085 | 다큐멘터리, 음악, 역사 | woman director, biography, sports, interview, sports documentary | 현실의 장소와 인물의 경험을 관찰하거나 기록해 보여주는 작품들. | 넓은 공통 범주 | 12 |
| 06 | 1,130 | 음악, 다큐멘터리, 로맨스 | musical, concert, concert film, live performance, rock 'n' roll | 범죄와 욕망에 휘말려 돈이나 권력을 둘러싼 충돌을 겪는 인물들. | 일부 영화만 설명 | 3 |
| 07 | 13,836 | 공포, 스릴러, 범죄 | murder, serial killer, slasher, based on novel or book, found footage | 범죄에 얽힌 인물들이 수사와 대립 속에서 위험에 처하는 이야기. | 일부 영화만 설명 | 18 |
| 08 | 6,422 | 애니메이션, SF, 판타지 | short film, anime, 3d animation, cartoon, adult animation | 위험에 놓인 사람이나 삶의 터전을 지키기 위해 나서는 이야기. | 넓은 공통 범주 | 11 |

설명에 사용한 영화와 개별 지지 판정:

- **01** — 레이징 피닉스 2: 여자 옹박의 귀환 [MovieLens 199830; FIT]; 레고 DC 코믹스 슈퍼 히어로: 저스티스 리그 vs 비자로 리그 [MovieLens 129826; FIT]; 배틀독 [MovieLens 220852; FIT]; Turkey Shoot [MovieLens 136646; FIT]. 조직 소탕, 영웅들의 연합, 치료제 확보, 살인 경쟁의 생존은 다른 이야기이며 위협과 대결이라는 넓은 공통점만 있다.
- **02** — Непрощенный [MovieLens 195553; FIT]; David [MovieLens 160295; FIT]; Voltati Eugenio [MovieLens 146780; FIT]; 원 트루 씽 [MovieLens 2272; FIT]. 사고로 인한 상실, 아버지의 기대, 버려짐, 간병으로 갈등의 원인이 달라 가족이라는 넓은 범주에서만 묶인다.
- **03** — 캠프파이어 키스 [MovieLens 246358; FIT]; Table for Three [MovieLens 144418; FIT]; Конец прекрасной эпохи [MovieLens 149568; INSUFFICIENT]; Dove vai in vacanza? [MovieLens 136283; PARTIAL]. C097은 관계가 얽힌 여행자들을 소개하지만 소동의 구체적 원인은 없다. C187의 작가 소개만으로는 관계 갈등을 확인할 수 없다.
- **04** — 맨 인 더 와일더니스 [MovieLens 111119; FIT]; 파일럿: 배틀 포 서바이벌 [MovieLens 271262; FIT]; 댄저 클로즈: 롱탄 대전투 [MovieLens 204834; FIT]; 무법자 제시 제임스 [MovieLens 47956; FIT]. 모두 과거 시점의 위험과 대립을 다루지만, 원정대의 배신·전쟁 생존·부대 전투·철도 권력에 대한 저항이라는 맥락은 다름.
- **05** — 원더스 오브 더 시 3D [MovieLens 197891; FIT]; 대전차 장애물의 기도 [MovieLens 278914; PARTIAL]; Le Regard de Charles [MovieLens 228063; FIT]; Private Violence [MovieLens 201741; FIT]. 공통점은 현실 관찰·기록이라는 넓은 형식뿐이며 구체 소재는 다름. C005는 실제 전시 활동을 서술하지만 기록 방식은 명시하지 않음.
- **06** — 서푼짜리 오페라 [MovieLens 32280; FIT]; Dixie [MovieLens 163362; CONFLICT]; 도쿄 트라이브 [MovieLens 138632; FIT]; 할렐루야 [MovieLens 42886; FIT]. C132의 작곡·출판 경력은 다른 세 편의 범죄와 폭력 갈등에 맞지 않는다.
- **07** — Men in Exile [MovieLens 166832; FIT]; 디스트로이어 [MovieLens 194959; FIT]; Mysterious Intruder [MovieLens 156637; FIT]; The Teacher [MovieLens 138162; CONFLICT]. C106은 젊은 남성과 기혼 교사 사이의 관계가 중심이며, 죽음에 대한 언급만으로 범죄 수사물로 볼 수 없음.
- **08** — 헨젤과 그레텔: 마녀 사냥꾼 [MovieLens 100163; FIT]; Fantasmi a Roma [MovieLens 178883; FIT]; 미키의 여행 [MovieLens 260061; INSUFFICIENT]; Black Road [MovieLens 163492; FIT]. 아이들·궁전·위협받는 여성을 지킨다는 세 편의 연결은 확인된다. C121은 등장인물 소개뿐이라 보호나 구조의 내용은 확인되지 않는다.

## 기존 전체 텍스트 + K-means (KM_TEXT)

| 군집 | 입력 근거 배정 수 | 장르 상위 3개 | 태그 상위 5개 | 고정 D 설명 | D 설명 상태 | E 영화 수 |
| --- | ---: | --- | --- | --- | --- | ---: |
| 01 | 13,521 | 코미디, 로맨스, TV 영화 | christmas, woman director, musical, pre-code, holiday | 가족이나 동거인과 원활하지 않은 관계를 겪는 인물들의 이야기. | 일부 영화만 설명 | 17 |
| 02 | 11,336 | 다큐멘터리, 애니메이션, 음악 | short film, woman director, biography, sports, cartoon | 촬영 기록과 증언을 통해 실제 세계의 모습이나 지나간 삶과 사건을 보여주는 작품들. | 넓은 공통 범주 | 11 |
| 03 | 8,506 | 액션, 스릴러, 드라마 | based on novel or book, sequel, murder, martial arts, new york city | 인명을 해치는 적대 세력에 맞서, 복수하거나 사람들을 구하려는 인물들의 대결. | 넓은 공통 범주 | 15 |
| 04 | 13,626 | 공포, 스릴러, 범죄 | murder, film noir, revenge, serial killer, found footage | 타인의 배신과 의심으로 궁지에 몰리는 인물들의 이야기. | 넓은 공통 범주 | 19 |
| 05 | 12,035 | 드라마, 스릴러, 액션 | based on novel or book, woman director, based on true story, murder, sequel | 타인의 고통과 부재를 마주하며 그 사람의 삶을 이해하거나 기억하려는 이야기. | 일부 영화만 설명 | 21 |
| 06 | 8,695 | 드라마, 공포, 코미디 | woman director, lgbt, gay theme, based on novel or book, based on true story | 동료들과 힘을 합쳐 위협적인 세력에 맞서는 이야기. | 일부 영화만 설명 | 11 |
| 07 | 7,865 | 코미디, 드라마, 로맨스 | woman director, spaghetti western, paris, france, silent film, based on novel or book | 집을 떠나 이동하거나 낯선 곳에 들어선 뒤 예상하지 못한 곤경을 겪는 이야기. | 일부 영화만 설명 | 12 |
| 08 | 9,933 | 드라마, 코미디, 로맨스 | woman director, based on novel or book, fairy tale, short film, finland | 실존 인물의 삶과 경험을 바탕으로 개인과 가족이 처한 현실을 돌아보는 작품들. | 일부 영화만 설명 | 22 |

설명에 사용한 영화와 개별 지지 판정:

- **01** — 캠프파이어 키스 [MovieLens 246358; FIT]; Table for Three [MovieLens 144418; FIT]; 마리솔 [MovieLens 290269; CONFLICT]; Gideon [MovieLens 163090; CONFLICT]. C094의 가족 관계와 C070의 동거 갈등만 지지함. C112의 도피와 C122의 요양원에서 삶의 의욕을 되찾는 과정은 다른 중심 내용임.
- **02** — 원더스 오브 더 시 3D [MovieLens 197891; FIT]; Le Regard de Charles [MovieLens 228063; FIT]; Stonewall Uprising [MovieLens 86985; FIT]; British Sounds [MovieLens 116082; FIT]. 바다, 개인의 영상 일기, 역사적 시위, 정치적 몽타주로 내용은 달라 기록 형식 수준의 공통점이다.
- **03** — 헨젤과 그레텔: 마녀 사냥꾼 [MovieLens 100163; FIT]; 배틀독 [MovieLens 220852; FIT]; 디스트로이어 [MovieLens 194959; FIT]; 대특명 3 [MovieLens 3768; FIT]. 구출과 복수의 대결이라는 넓은 범주이며, 마녀·바이러스·범죄조직·전쟁의 구체적 위협은 다름.
- **04** — 맨 인 더 와일더니스 [MovieLens 111119; FIT]; Men in Exile [MovieLens 166832; FIT]; The Rebel Set [MovieLens 173037; FIT]; Mysterious Intruder [MovieLens 156637; FIT]. 버림받음·배신·범죄 혐의라는 넓은 위기 범주를 공유하나, 복수·도주·강도·탐정 활동은 서로 다름.
- **05** — 원 트루 씽 [MovieLens 2272; FIT]; 대전차 장애물의 기도 [MovieLens 278914; CONFLICT]; 내가 죽던 날 [MovieLens 252134; FIT]; 파랑새 [MovieLens 169130; FIT]. C005는 전쟁에 적응한 조각가들의 방어물 제작이며 특정인의 삶을 이해하거나 기억하는 과정은 제시되지 않는다.
- **06** — 레이징 피닉스 2: 여자 옹박의 귀환 [MovieLens 199830; FIT]; 레고 DC 코믹스 슈퍼 히어로: 저스티스 리그 vs 비자로 리그 [MovieLens 129826; FIT]; 미키의 여행 [MovieLens 260061; INSUFFICIENT]; 퍼니 페이스 [MovieLens 247716; CONFLICT]. C183과 C174만 집단 협력으로 적대 세력에 맞섬. C072의 주거 위기와 동행에는 그런 공동 대응이 명시되지 않음.
- **07** — Fantasmi a Roma [MovieLens 178883; CONFLICT]; Voltati Eugenio [MovieLens 146780; FIT]; Dove vai in vacanza? [MovieLens 136283; FIT]; J'embrasse pas [MovieLens 127695; FIT]. C192는 기존 궁전을 파괴로부터 지키는 이야기로, 떠남이나 낯선 환경의 곤경과 다르다.
- **08** — Непрощенный [MovieLens 195553; FIT]; David [MovieLens 160295; CONFLICT]; Конец прекрасной эпохи [MovieLens 149568; FIT]; Yrittäjä [MovieLens 196671; FIT]. C022에는 실존 인물을 기록한다는 근거가 없고 소년의 우정과 정체성 갈등이 중심임. 나머지도 구체적인 삶의 문제는 다름.

## 장르 + K-means (KM_GENRE)

| 군집 | 입력 근거 배정 수 | 장르 상위 3개 | 태그 상위 5개 | 고정 D 설명 | D 설명 상태 | E 영화 수 |
| --- | ---: | --- | --- | --- | --- | ---: |
| 01 | 7,391 | 가족, 애니메이션, 판타지 | short film, 3d animation, cartoon, anime, christmas | 여러 인물이 팀을 이루어 각자의 힘을 합치는 이야기. | 일부 영화만 설명 | 14 |
| 02 | 11,582 | 로맨스, 코미디, 드라마 | love, woman director, based on novel or book, lgbt, musical | 가족이나 동거인의 관계가 일상을 흔들고 관계를 다시 바라보게 하는 이야기. | 일부 영화만 설명 | 18 |
| 03 | 12,853 | 범죄, 스릴러, 미스터리 | murder, film noir, based on novel or book, revenge, police | 범죄에 얽힌 인물들이 수사와 대립 속에서 위험에 처하는 이야기. | 일부 영화만 설명 | 15 |
| 04 | 11,135 | 액션, 모험, SF | martial arts, dystopia, based on novel or book, sequel, superhero | 생명이 위협받는 극한 상황에서 살아남으려는 인물들의 이야기. | 넓은 공통 범주 | 14 |
| 05 | 8,826 | 공포, 스릴러, 미스터리 | murder, slasher, found footage, zombie, vampire | 기괴한 존재가 얽힌 죽음과 실종의 배후를 파헤치는 이야기. | 넓은 공통 범주 | 16 |
| 06 | 12,461 | 드라마, 역사, 전쟁 | woman director, biography, based on true story, based on novel or book, world war ii | 가족관계에서 겪는 상실과 소외를 다루는 이야기. | 일부 영화만 설명 | 21 |
| 07 | 11,330 | 코미디, 드라마, 음악 | woman director, stand-up comedy, silent film, musical, satire | 공통된 내용 설명을 도출하지 못함. | 일부 영화만 설명 | 15 |
| 08 | 9,939 | 다큐멘터리, 음악, 역사 | woman director, biography, sports, interview, sports documentary | 현실의 사람과 환경을 관찰하고 기록해 그 모습을 드러내는 작품들. | 넓은 공통 범주 | 15 |

설명에 사용한 영화와 개별 지지 판정:

- **01** — 레고 DC 코믹스 슈퍼 히어로: 저스티스 리그 vs 비자로 리그 [MovieLens 129826; FIT]; 파랑새 [MovieLens 169130; CONFLICT]; 미키의 여행 [MovieLens 260061; INSUFFICIENT]; 쿵푸팬더: 두루마리의 비밀 [MovieLens 202517; FIT]. C174와 C027의 협력만 지지함. C011은 교실의 자살미수 사건과 교사의 대응이며, C121은 인물 소개 외 사건 정보가 없음.
- **02** — Men in Exile [MovieLens 166832; CONFLICT]; 캠프파이어 키스 [MovieLens 246358; FIT]; Table for Three [MovieLens 144418; FIT]; 원 트루 씽 [MovieLens 2272; FIT]. C110은 강도·살인 누명을 쓴 전과자의 도주로, 가까운 관계의 재검토가 제시되지 않는다.
- **03** — 레이징 피닉스 2: 여자 옹박의 귀환 [MovieLens 199830; FIT]; 디스트로이어 [MovieLens 194959; FIT]; Mysterious Intruder [MovieLens 156637; FIT]; The Teacher [MovieLens 138162; CONFLICT]. C106은 젊은 남성과 기혼 교사 사이의 관계가 중심이며, 죽음에 대한 언급만으로 범죄 수사물로 볼 수 없음.
- **04** — 맨 인 더 와일더니스 [MovieLens 111119; FIT]; 배틀독 [MovieLens 220852; FIT]; Turkey Shoot [MovieLens 136646; FIT]; 대특명 3 [MovieLens 3768; FIT]. 생존이라는 넓은 범주를 공유하나, 버림받은 안내자·감염자·생존쇼 참가자·구출 작전의 맥락은 다름.
- **05** — 헨젤과 그레텔: 마녀 사냥꾼 [MovieLens 100163; FIT]; 스마일리 [MovieLens 107684; FIT]; 에코에코 아자락 [MovieLens 272817; FIT]; 세넨툰치 [MovieLens 165545; FIT]. 납치, 살인 영상, 학교의 저주, 마을의 실종 수사가 각기 달라 구체적인 사건 구조까지 같지는 않다.
- **06** — Непрощенный [MovieLens 195553; FIT]; David [MovieLens 160295; FIT]; Voltati Eugenio [MovieLens 146780; FIT]; The Rebel Set [MovieLens 173037; CONFLICT]. C002는 현금수송차 강도 일당 내부의 배신이며 가족관계가 중심이라는 근거가 없다.
- **07** — Fantasmi a Roma [MovieLens 178883; CONFLICT]; Конец прекрасной эпохи [MovieLens 149568; INSUFFICIENT]; Dove vai in vacanza? [MovieLens 136283; CONFLICT]; Arnold's Wrecking Co. [MovieLens 160521; CONFLICT]. 궁전 보존, 휴가 소동, 마약 유통의 중심 사건이 연결되지 않는다. 작가 소개에는 구체적 줄거리가 없다.
- **08** — 원더스 오브 더 시 3D [MovieLens 197891; FIT]; 대전차 장애물의 기도 [MovieLens 278914; PARTIAL]; Le Regard de Charles [MovieLens 228063; FIT]; Private Violence [MovieLens 201741; FIT]. 해양 관찰, 전시 작업, 사적 영상 일기, 가정폭력 증언으로 주제가 갈린다. C005는 현실의 작업을 소개하지만 기록 방식까지 명시하지 않는다.

## 태그 + K-means (KM_TAG)

| 군집 | 입력 근거 배정 수 | 장르 상위 3개 | 태그 상위 5개 | 고정 D 설명 | D 설명 상태 | E 영화 수 |
| --- | ---: | --- | --- | --- | --- | ---: |
| 01 | 55,030 | 드라마, 코미디, 스릴러 | based on novel or book, murder, woman director, biography, based on true story | 위험을 일으킨 상대에게 맞서 타인을 구하거나 정의를 구하는 이야기. | 일부 영화만 설명 | 71 |
| 02 | 1,432 | 모험, 공포, 액션 | sequel, duringcreditsstinger, aftercreditsstinger, based on novel or book, slasher | 가족의 사정이나 누군가를 돌보는 책임 때문에 생활이 흔들리는 이야기. | 일부 영화만 설명 | 2 |
| 03 | 1,886 | 다큐멘터리, 드라마, 코미디 | woman director, anthology, biography, lgbt, coming of age | 부재하거나 연락이 끊긴 배우자를 둘러싼 불안과 관계의 단절을 다루는 이야기. | 일부 영화만 설명 | 5 |
| 04 | 754 | TV 영화, 가족, 로맨스 | christmas, holiday, santa claus, woman director, based on novel or book | 크리스마스를 둘러싼 상실과 관계의 단절을 다루는 이야기. | 구체적 공통 설명 | 2 |
| 05 | 609 | 액션, 모험, 범죄 | martial arts, kung fu, action hero, revenge, wuxia | 폭력적인 조직이나 권력에 맞서 직접 행동하고 싸우는 이야기. | 넓은 공통 범주 | 1 |
| 06 | 757 | 음악, 로맨스, 코미디 | musical, pre-code, based on play or musical, singer, biography | 노래와 음악 활동을 중심으로 성공과 삶의 선택을 다룬 이야기. | 일부 영화만 설명 | 1 |
| 07 | 1,299 | 애니메이션, 코미디, SF | short film, silent film, cartoon, black and white, 3d animation | 공통된 내용 설명을 도출하지 못함. | 일부 영화만 설명 | 3 |
| 08 | 175 | 서부, 액션, 코미디 | spaghetti western, django, maverick, eurowestern, gunfight | 억압적인 권력이나 범죄자에게 맞서는 이야기. | 일부 영화만 설명 | 1 |

설명에 사용한 영화와 개별 지지 판정:

- **01** — 헨젤과 그레텔: 마녀 사냥꾼 [MovieLens 100163; FIT]; Непрощенный [MovieLens 195553; FIT]; 원더스 오브 더 시 3D [MovieLens 197891; CONFLICT]; 레고 DC 코믹스 슈퍼 히어로: 저스티스 리그 vs 비자로 리그 [MovieLens 129826; FIT]. C197은 바다를 관찰하는 영상이며 구조나 정의를 위한 대립이 없다.
- **02** — Grand-Daddy Day Care [MovieLens 210971; FIT]; 메리 포핀스 리턴즈 [MovieLens 195161; PARTIAL]; 애널라이즈 댓 [MovieLens 5900; FIT]; 라이즈 오브 더 풋솔져 3 [MovieLens 228945; CONFLICT]. C013은 가족의 상실과 주거 위기에 부분적으로 맞는다. C141의 마약 지배권 전쟁은 이 설명에서 벗어난다.
- **03** — 자마 [MovieLens 178063; CONFLICT]; Queen of Diamonds [MovieLens 211934; FIT]; 애재별향적계절 [MovieLens 185079; FIT]; Bir Avuç Deniz [MovieLens 145124; CONFLICT]. C206과 C038만 배우자의 부재를 지지함. C042는 발령을 기다리는 장교, C199는 휴가 중 삶의 방향을 돌아보는 인물이 중심임.
- **04** — 어 디퍼런트 카인드 오브 크리스마스 [MovieLens 182709; FIT]; Return to Christmas Creek [MovieLens 268458; FIT]; Hometown Christmas [MovieLens 210943; FIT]; By God's Grace [MovieLens 198547; FIT]. 네 편 모두 크리스마스의 상징이나 시기가 상실·단절과 직접 연결된다. 구체적인 대응 방식은 서로 다르다.
- **05** — 레이징 피닉스 2: 여자 옹박의 귀환 [MovieLens 199830; FIT]; 대특명 3 [MovieLens 3768; FIT]; 옹박: 더 레전드 [MovieLens 67252; FIT]; 정봉적수 [MovieLens 210415; INSUFFICIENT]. 세 편은 조직 소탕·구출·복수라는 서로 다른 싸움이다. C073은 배우와 배역 소개에 그쳐 중심 사건을 판단하기 어렵다.
- **06** — 엄마는 인터넷 스타 [MovieLens 136940; FIT]; Dixie [MovieLens 163362; FIT]; 스카이스 더 리미트 [MovieLens 86653; CONFLICT]; 아이 쏘우 더 라이트 [MovieLens 155810; PARTIAL]. C163은 신분을 숨긴 군인의 구애다. C207은 음악가 전기라는 소개만 있어 구체적인 선택과 갈등은 확인되지 않는다.
- **07** — Hopptornet [MovieLens 168666; CONFLICT]; The Lady in Red [MovieLens 198375; CONFLICT]; 이단아 [MovieLens 198267; CONFLICT]; السلام عليك يا مريم [MovieLens 156575; CONFLICT]. 다이빙의 공포, 앵무새의 추격, 인종적 정체성, 안식일의 만남을 잇는 구체적 중심 내용이 없다.
- **08** — 표범 황혼에 떠나가다 [MovieLens 54878; FIT]; Joko invoca Dio... e muori [MovieLens 142352; FIT]; 내 이름은 상하이 조 [MovieLens 268064; FIT]; Al di là della legge [MovieLens 108635; CONFLICT]. C015의 보안관 취임은 약자를 지키려는 행동이 아니라 은 수송물을 훔치기 위한 수단이다.

## 장르·태그 + K-means (KM_BOTH)

| 군집 | 입력 근거 배정 수 | 장르 상위 3개 | 태그 상위 5개 | 고정 D 설명 | D 설명 상태 | E 영화 수 |
| --- | ---: | --- | --- | --- | --- | ---: |
| 01 | 16,595 | 가족, 애니메이션, 판타지 | short film, 3d animation, based on novel or book, musical, world war ii | 상실이나 배신을 겪은 인물이 자신을 해친 상대를 찾아 책임을 묻는 이야기. | 일부 영화만 설명 | 22 |
| 02 | 11,354 | 로맨스, 코미디, 드라마 | love, woman director, based on novel or book, lgbt, gay theme | 멀어진 부모와 자녀의 관계를 회복하고 서로를 이해하려는 이야기. | 일부 영화만 설명 | 18 |
| 03 | 8,904 | 액션, 모험, 스릴러 | martial arts, revenge, sequel, superhero, police | 범죄나 무력의 위협 속에서 자신과 가까운 사람을 위해 맞서는 이야기. | 넓은 공통 범주 | 10 |
| 04 | 9,141 | 공포, 스릴러, 미스터리 | murder, slasher, found footage, zombie, vampire | 죽음을 부르는 기괴한 존재나 힘의 정체와 음모에 접근하는 이야기. | 넓은 공통 범주 | 16 |
| 05 | 10,101 | 범죄, 스릴러, 미스터리 | murder, film noir, based on novel or book, detective, gangster | 여성을 찾고 그 사연을 추적하는 과정에서 수사자가 사건에 깊이 얽히는 이야기. | 일부 영화만 설명 | 16 |
| 06 | 10,359 | 코미디, 드라마, 음악 | woman director, stand-up comedy, silent film, sex comedy, dark comedy | 젊은 인물이 자신의 진로를 선택하고 그 선택의 어려움과 마주하는 이야기. | 일부 영화만 설명 | 15 |
| 07 | 9,328 | 드라마 | woman director, based on novel or book, lgbt, gay theme, coming of age | 어린아이와 젊은이가 가족·사회에서 소외와 정체성의 어려움을 겪는 이야기. | 일부 영화만 설명 | 16 |
| 08 | 9,735 | 다큐멘터리, 음악, 역사 | woman director, biography, sports, interview, sports documentary | 현실의 장소와 인물의 경험을 관찰하거나 기록해 보여주는 작품들. | 넓은 공통 범주 | 15 |

설명에 사용한 영화와 개별 지지 판정:

- **01** — Непрощенный [MovieLens 195553; FIT]; 레고 DC 코믹스 슈퍼 히어로: 저스티스 리그 vs 비자로 리그 [MovieLens 129826; CONFLICT]; 맨 인 더 와일더니스 [MovieLens 111119; FIT]; Fantasmi a Roma [MovieLens 178883; CONFLICT]. C117의 가족 상실에 대한 정의 추구와 C095의 버림받은 뒤 복수만 지지함. C174의 공동 전투와 C192의 궁전 보존은 다른 동기임.
- **02** — Men in Exile [MovieLens 166832; CONFLICT]; 캠프파이어 키스 [MovieLens 246358; FIT]; Table for Three [MovieLens 144418; CONFLICT]; 원 트루 씽 [MovieLens 2272; FIT]. C094의 부모·자녀 관계 회복과 C111의 부모 이해만 지지함. 누명 도주와 부부 동거인의 개입은 다른 내용임.
- **03** — 레이징 피닉스 2: 여자 옹박의 귀환 [MovieLens 199830; FIT]; 디스트로이어 [MovieLens 194959; FIT]; Turkey Shoot [MovieLens 136646; FIT]; 대특명 3 [MovieLens 3768; FIT]. 조직 소탕, 연인의 복수, 생존 경쟁, 가족 구출로 목표가 달라 폭력적 위협에 대응한다는 넓은 범주만 공유한다.
- **04** — 헨젤과 그레텔: 마녀 사냥꾼 [MovieLens 100163; FIT]; 배틀독 [MovieLens 220852; FIT]; 스마일리 [MovieLens 107684; FIT]; 에코에코 아자락 [MovieLens 272817; FIT]. 마녀, 감염과 군의 음모, 살인 영상, 저주로 위협의 성격이 달라 기괴한 위협을 파헤친다는 넓은 공통점에 머문다.
- **05** — Mysterious Intruder [MovieLens 156637; FIT]; The Teacher [MovieLens 138162; CONFLICT]; 내가 죽던 날 [MovieLens 252134; FIT]; 롱 로스트 [MovieLens 201767; CONFLICT]. C200과 C203만 찾기·수사를 지지함. C106의 연애와 C093의 저택 초대에는 이런 수사가 명시되지 않음.
- **06** — Конец прекрасной эпохи [MovieLens 149568; INSUFFICIENT]; Dove vai in vacanza? [MovieLens 136283; CONFLICT]; Arnold's Wrecking Co. [MovieLens 160521; PARTIAL]; Być jak Kazimierz Deyna [MovieLens 181309; FIT]. C101은 마약 유통을 사업으로 택한다는 점에서 부분적으로 맞는다. 휴가 소동은 진로 탐색과 다르고 작가 소개의 구체적 사건은 부족하다.
- **07** — David [MovieLens 160295; FIT]; Voltati Eugenio [MovieLens 146780; FIT]; The Rebel Set [MovieLens 173037; CONFLICT]; 마리솔 [MovieLens 290269; FIT]. C002는 강도단 내부 배신이 중심이며, 성장과 소속의 문제를 뒷받침하지 않음.
- **08** — 원더스 오브 더 시 3D [MovieLens 197891; FIT]; 대전차 장애물의 기도 [MovieLens 278914; PARTIAL]; Le Regard de Charles [MovieLens 228063; FIT]; Private Violence [MovieLens 201741; FIT]. 공통점은 현실 관찰·기록이라는 넓은 형식뿐이며 구체 소재는 다름. C005는 실제 전시 활동을 서술하지만 기록 방식은 명시하지 않음.

## 장르 + NMF (NMF_GENRE)

| 군집 | 입력 근거 배정 수 | 장르 상위 3개 | 태그 상위 5개 | 고정 D 설명 | D 설명 상태 | E 영화 수 |
| --- | ---: | --- | --- | --- | --- | ---: |
| 01 | 7,331 | 가족, 애니메이션, 판타지 | 3d animation, short film, anime, christmas, magic | 여러 인물이 팀을 이루어 각자의 힘을 합치는 이야기. | 일부 영화만 설명 | 13 |
| 02 | 11,597 | 로맨스, 코미디, 드라마 | love, woman director, based on novel or book, lgbt, musical | 멀어진 부모와 자녀의 관계를 회복하고 서로를 이해하려는 이야기. | 일부 영화만 설명 | 18 |
| 03 | 9,411 | 액션, SF, 모험 | martial arts, revenge, superhero, sequel, based on comic | 폭력적인 조직이나 통제 상황에 휘말린 인물이 살아남거나 다른 사람을 구하려고 맞서는 이야기. | 넓은 공통 범주 | 11 |
| 04 | 10,330 | 스릴러, 범죄, 미스터리 | murder, film noir, based on novel or book, detective, police | 사람의 행적을 추적하는 수사자가 범죄와 개인적 사연에 깊이 얽히는 이야기. | 일부 영화만 설명 | 15 |
| 05 | 8,917 | 공포, 스릴러, 미스터리 | murder, slasher, found footage, zombie, vampire | 기괴한 존재가 얽힌 죽음과 실종의 배후를 파헤치는 이야기. | 넓은 공통 범주 | 16 |
| 06 | 13,989 | 드라마, 역사, 전쟁 | woman director, biography, based on true story, based on novel or book, world war ii | 가족관계에서 겪는 상실과 소외를 다루는 이야기. | 일부 영화만 설명 | 22 |
| 07 | 13,726 | 코미디, 드라마, 애니메이션 | short film, woman director, silent film, dark comedy, stand-up comedy | 공통된 내용 설명을 도출하지 못함. | 일부 영화만 설명 | 18 |
| 08 | 10,216 | 다큐멘터리, 음악, 역사 | woman director, biography, sports, interview, sports documentary | 현실의 장소와 인물의 경험을 관찰하거나 기록해 보여주는 작품들. | 넓은 공통 범주 | 15 |

설명에 사용한 영화와 개별 지지 판정:

- **01** — 레고 DC 코믹스 슈퍼 히어로: 저스티스 리그 vs 비자로 리그 [MovieLens 129826; FIT]; 파랑새 [MovieLens 169130; CONFLICT]; 쿵푸팬더: 두루마리의 비밀 [MovieLens 202517; FIT]; 아이 빌리브 [MovieLens 215041; CONFLICT]. C174와 C027만 지지함. C011의 교실 사건과 C107의 신앙·치유는 팀의 전투가 중심이 아님.
- **02** — Men in Exile [MovieLens 166832; CONFLICT]; 캠프파이어 키스 [MovieLens 246358; FIT]; Table for Three [MovieLens 144418; CONFLICT]; 원 트루 씽 [MovieLens 2272; FIT]. C094의 부모·자녀 관계 회복과 C111의 부모 이해만 지지함. 누명 도주와 부부 동거인의 개입은 다른 내용임.
- **03** — 레이징 피닉스 2: 여자 옹박의 귀환 [MovieLens 199830; FIT]; 배틀독 [MovieLens 220852; FIT]; Turkey Shoot [MovieLens 136646; FIT]; 대특명 3 [MovieLens 3768; FIT]. 범죄조직, 감염자 수용소, 살인자들의 방송, 전쟁 지역으로 상황과 대결 목적은 다르다.
- **04** — 디스트로이어 [MovieLens 194959; FIT]; Mysterious Intruder [MovieLens 156637; FIT]; The Teacher [MovieLens 138162; CONFLICT]; 내가 죽던 날 [MovieLens 252134; FIT]. C106은 연애 관계와 죽은 인물의 연결이 제시될 뿐, 수사나 사연 추적이 중심이라는 근거가 없음.
- **05** — 헨젤과 그레텔: 마녀 사냥꾼 [MovieLens 100163; FIT]; 스마일리 [MovieLens 107684; FIT]; 에코에코 아자락 [MovieLens 272817; FIT]; 세넨툰치 [MovieLens 165545; FIT]. 납치, 살인 영상, 학교의 저주, 마을의 실종 수사가 각기 달라 구체적인 사건 구조까지 같지는 않다.
- **06** — Непрощенный [MovieLens 195553; FIT]; 맨 인 더 와일더니스 [MovieLens 111119; CONFLICT]; David [MovieLens 160295; FIT]; Voltati Eugenio [MovieLens 146780; FIT]. C095는 가족관계가 아니라 자신을 버린 탐험 동료에 대한 복수가 중심이다.
- **07** — Fantasmi a Roma [MovieLens 178883; CONFLICT]; Конец прекрасной эпохи [MovieLens 149568; CONFLICT]; Dove vai in vacanza? [MovieLens 136283; CONFLICT]; 미키의 여행 [MovieLens 260061; INSUFFICIENT]. 궁전을 지키는 유령, 작가의 삶, 휴가 중 불운 사이에 공통 내용 근거를 찾지 못함. C121은 사건 정보가 없음.
- **08** — 원더스 오브 더 시 3D [MovieLens 197891; FIT]; 대전차 장애물의 기도 [MovieLens 278914; PARTIAL]; Le Regard de Charles [MovieLens 228063; FIT]; Private Violence [MovieLens 201741; FIT]. 공통점은 현실 관찰·기록이라는 넓은 형식뿐이며 구체 소재는 다름. C005는 실제 전시 활동을 서술하지만 기록 방식은 명시하지 않음.

## 태그 + NMF (NMF_TAG)

| 군집 | 입력 근거 배정 수 | 장르 상위 3개 | 태그 상위 5개 | 고정 D 설명 | D 설명 상태 | E 영화 수 |
| --- | ---: | --- | --- | --- | --- | ---: |
| 01 | 29,279 | 스릴러, 공포, 액션 | based on novel or book, murder, revenge, sequel, police | 초자연적 존재가 인간 세계의 갈등에 관여하는 이야기. | 일부 영화만 설명 | 36 |
| 02 | 9,408 | 다큐멘터리, 역사, 드라마 | biography, based on true story, sports, world war ii, politics | 피해나 범죄에 맞서 책임을 묻고 응징하려는 움직임. | 일부 영화만 설명 | 9 |
| 03 | 2,652 | TV 영화, 가족, 코미디 | christmas, holiday, family, santa claus, dog | 생명을 위협하는 사건을 조사하고 그 원인을 추적하는 인물들의 이야기. | 일부 영화만 설명 | 4 |
| 04 | 2,867 | 애니메이션, 가족, 코미디 | short film, cartoon, 3d animation, stop motion, based on comic | 공통된 내용 설명을 도출하지 못함. | 일부 영화만 설명 | 6 |
| 05 | 4,779 | 다큐멘터리, 드라마, 코미디 | woman director, stand-up comedy, coming of age, lgbt, mother daughter relationship | 부재와 단절을 겪는 인물이 오지 않는 소식이나 사라진 사람 때문에 고통을 겪는 이야기. | 일부 영화만 설명 | 10 |
| 06 | 6,121 | 드라마, 로맨스, 코미디 | gay theme, lgbt, coming of age, friendship, male homosexuality | 가족이나 동거인의 관계가 일상을 흔들고 관계를 다시 바라보게 하는 이야기. | 일부 영화만 설명 | 10 |
| 07 | 4,319 | 음악, 코미디, 로맨스 | musical, pre-code, based on play or musical, fairy tale, dance | 가족 관계의 변화가 개인의 삶을 흔들며 갈등과 선택을 낳는 이야기. | 일부 영화만 설명 | 7 |
| 08 | 2,517 | 코미디, 드라마, 로맨스 | silent film, black and white, slapstick comedy, short film, film noir | 사랑할 상대를 찾거나 가로막힌 연인 관계를 다시 이어가려는 이야기. | 일부 영화만 설명 | 4 |

설명에 사용한 영화와 개별 지지 판정:

- **01** — 헨젤과 그레텔: 마녀 사냥꾼 [MovieLens 100163; FIT]; 배틀독 [MovieLens 220852; FIT]; Men in Exile [MovieLens 166832; CONFLICT]; Fantasmi a Roma [MovieLens 178883; FIT]. C061·C186·C192는 초자연적 존재가 대립에 관여함. C110의 누명 도주에는 초자연적 내용이 없음.
- **02** — Непрощенный [MovieLens 195553; FIT]; 원더스 오브 더 시 3D [MovieLens 197891; CONFLICT]; 레이징 피닉스 2: 여자 옹박의 귀환 [MovieLens 199830; FIT]; 맨 인 더 와일더니스 [MovieLens 111119; FIT]. C197의 해양 관찰에는 책임 추궁이나 응징의 갈등이 제시되지 않는다.
- **03** — 쿵푸팬더: 두루마리의 비밀 [MovieLens 202517; CONFLICT]; 롱 키스 굿나잇 [MovieLens 1047; FIT]; A Place in Hell [MovieLens 195839; FIT]; Storm Chasers: Revenge of the Twister [MovieLens 275677; FIT]. C027은 쿵푸 능력을 합치는 팀 구성이 중심이며, 위협 사건의 조사나 원인 추적이 제시되지 않음.
- **04** — 레고 DC 코믹스 슈퍼 히어로: 저스티스 리그 vs 비자로 리그 [MovieLens 129826; CONFLICT]; 미키의 여행 [MovieLens 260061; INSUFFICIENT]; Come Clean [MovieLens 177547; CONFLICT]; Hopptornet [MovieLens 168666; CONFLICT]. 팀의 전투, 아내들에게 여성을 숨기는 소동, 다이빙 결정을 관찰하는 기록 사이에 공통 내용 근거를 찾지 못함. C121은 사건 정보가 없음.
- **05** — 자마 [MovieLens 178063; FIT]; Queen of Diamonds [MovieLens 211934; FIT]; 에코에코 아자락 [MovieLens 272817; CONFLICT]; 애재별향적계절 [MovieLens 185079; FIT]. 발령 전갈의 부재와 배우자의 부재는 느슨하게 연결되지만, C078의 학교 저주와 마법 대결은 이 중심 내용에 맞지 않는다.
- **06** — 캠프파이어 키스 [MovieLens 246358; FIT]; Table for Three [MovieLens 144418; FIT]; 원 트루 씽 [MovieLens 2272; FIT]; 마리솔 [MovieLens 290269; CONFLICT]. C112의 중심은 범죄 누명과 미등록 이민 신분으로 인한 도주·제도적 억압이다.
- **07** — The Rebel Set [MovieLens 173037; CONFLICT]; Child of Divorce [MovieLens 202809; FIT]; 서푼짜리 오페라 [MovieLens 32280; CONFLICT]; 엄마는 인터넷 스타 [MovieLens 136940; FIT]. C018의 부모 이혼과 C021의 가족·인기 사이 선택만 지지함. C002와 C185는 범죄와 배신이 중심임.
- **08** — 인 서치 오브 어 미드나잇 키스 [MovieLens 53769; FIT]; 메리 위도우 [MovieLens 85205; FIT]; Egged On [MovieLens 267982; CONFLICT]; Newark Athlete [MovieLens 148064; INSUFFICIENT]. C075는 발명품을 노리는 회사들의 경쟁이다. C099는 촬영 장치 설명만 있어 연애 내용의 유무를 판단할 수 없다.

## 장르·태그 + NMF (NMF_BOTH)

| 군집 | 입력 근거 배정 수 | 장르 상위 3개 | 태그 상위 5개 | 고정 D 설명 | D 설명 상태 | E 영화 수 |
| --- | ---: | --- | --- | --- | --- | ---: |
| 01 | 7,894 | 가족, 애니메이션, 판타지 | short film, 3d animation, anime, christmas, cartoon | 여러 인물이 팀을 이루어 각자의 힘을 합치는 이야기. | 일부 영화만 설명 | 15 |
| 02 | 11,546 | 로맨스, 코미디, 드라마 | love, woman director, based on novel or book, lgbt, musical | 가족이나 동거인의 관계가 일상을 흔들고 관계를 다시 바라보게 하는 이야기. | 일부 영화만 설명 | 18 |
| 03 | 9,285 | 액션, 모험, SF | martial arts, revenge, superhero, sequel, based on comic | 폭력적인 적들에게 생존을 위협받고, 대항하거나 탈출하려는 인물들의 이야기. | 넓은 공통 범주 | 11 |
| 04 | 9,123 | 스릴러, 범죄, 미스터리 | murder, film noir, based on novel or book, detective, police | 사람의 행적을 추적하는 수사자가 범죄와 개인적 사연에 깊이 얽히는 이야기. | 일부 영화만 설명 | 11 |
| 05 | 8,946 | 공포, 스릴러, 미스터리 | murder, slasher, found footage, zombie, vampire | 죽음이나 실종을 둘러싼 섬뜩한 위협에 접근하고 그 배후를 파헤치는 이야기. | 넓은 공통 범주 | 16 |
| 06 | 15,210 | 드라마, 역사, 전쟁 | woman director, biography, based on true story, based on novel or book, world war ii | 가족 관계에서 겪는 상실과 소외가 인물의 삶을 흔드는 이야기. | 일부 영화만 설명 | 26 |
| 07 | 13,355 | 코미디, 드라마, 음악 | woman director, stand-up comedy, silent film, dark comedy, musical | 공통된 내용 설명을 도출하지 못함. | 일부 영화만 설명 | 16 |
| 08 | 10,158 | 다큐멘터리, 음악, 역사 | woman director, biography, sports, interview, sports documentary | 현실의 사람과 환경을 관찰하고 기록해 그 모습을 드러내는 작품들. | 넓은 공통 범주 | 15 |

설명에 사용한 영화와 개별 지지 판정:

- **01** — 레고 DC 코믹스 슈퍼 히어로: 저스티스 리그 vs 비자로 리그 [MovieLens 129826; FIT]; 파랑새 [MovieLens 169130; CONFLICT]; 미키의 여행 [MovieLens 260061; INSUFFICIENT]; 쿵푸팬더: 두루마리의 비밀 [MovieLens 202517; FIT]. C174와 C027의 협력만 지지함. C011은 교실의 자살미수 사건과 교사의 대응이며, C121은 인물 소개 외 사건 정보가 없음.
- **02** — Men in Exile [MovieLens 166832; CONFLICT]; 캠프파이어 키스 [MovieLens 246358; FIT]; Table for Three [MovieLens 144418; FIT]; 원 트루 씽 [MovieLens 2272; FIT]. C110은 강도·살인 누명을 쓴 전과자의 도주로, 가까운 관계의 재검토가 제시되지 않는다.
- **03** — 레이징 피닉스 2: 여자 옹박의 귀환 [MovieLens 199830; PARTIAL]; 배틀독 [MovieLens 220852; FIT]; Turkey Shoot [MovieLens 136646; FIT]; 대특명 3 [MovieLens 3768; FIT]. C183은 범죄조직 간 싸움과 공동 대응을 지지하지만 생존·탈출 자체를 명시하지 않음. 나머지도 위협의 원인과 대응 방식은 다름.
- **04** — 디스트로이어 [MovieLens 194959; FIT]; Mysterious Intruder [MovieLens 156637; FIT]; The Teacher [MovieLens 138162; CONFLICT]; 내가 죽던 날 [MovieLens 252134; FIT]. C106은 연애 관계와 죽은 인물의 연결이 제시될 뿐, 수사나 사연 추적이 중심이라는 근거가 없음.
- **05** — 헨젤과 그레텔: 마녀 사냥꾼 [MovieLens 100163; FIT]; 스마일리 [MovieLens 107684; FIT]; 에코에코 아자락 [MovieLens 272817; FIT]; 세넨툰치 [MovieLens 165545; FIT]. 위협 조사와 공포라는 넓은 범주만 공통됨. 모든 위협이 같은 초자연적 원인이라고 단정하지 않음.
- **06** — Непрощенный [MovieLens 195553; FIT]; 맨 인 더 와일더니스 [MovieLens 111119; CONFLICT]; David [MovieLens 160295; FIT]; Voltati Eugenio [MovieLens 146780; FIT]. C095의 버림받음은 가족이 아닌 원정대 동료들에 의한 것이므로 가족 관계 설명에 맞지 않음.
- **07** — Fantasmi a Roma [MovieLens 178883; CONFLICT]; Конец прекрасной эпохи [MovieLens 149568; CONFLICT]; Dove vai in vacanza? [MovieLens 136283; CONFLICT]; Arnold's Wrecking Co. [MovieLens 160521; CONFLICT]. 궁전을 지키는 유령, 작가의 삶, 휴가 중 불운, 대마 판매자의 추적 위기를 연결할 공통 내용 근거를 찾지 못함.
- **08** — 원더스 오브 더 시 3D [MovieLens 197891; FIT]; 대전차 장애물의 기도 [MovieLens 278914; PARTIAL]; Le Regard de Charles [MovieLens 228063; FIT]; Private Violence [MovieLens 201741; FIT]. 해양 관찰, 전시 작업, 사적 영상 일기, 가정폭력 증언으로 주제가 갈린다. C005는 현실의 작업을 소개하지만 기록 방식까지 명시하지 않는다.

