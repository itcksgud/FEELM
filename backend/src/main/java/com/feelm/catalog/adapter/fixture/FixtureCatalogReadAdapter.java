package com.feelm.catalog.adapter.fixture;

import com.feelm.catalog.domain.CatalogModels;
import com.feelm.catalog.domain.CatalogModels.AvailabilitySnapshot;
import com.feelm.catalog.domain.CatalogModels.CatalogSnapshot;
import com.feelm.catalog.domain.CatalogModels.Country;
import com.feelm.catalog.domain.CatalogModels.CreditRole;
import com.feelm.catalog.domain.CatalogModels.ExternalRating;
import com.feelm.catalog.domain.CatalogModels.Genre;
import com.feelm.catalog.domain.CatalogModels.LinkType;
import com.feelm.catalog.domain.CatalogModels.MonetizationType;
import com.feelm.catalog.domain.CatalogModels.Movie;
import com.feelm.catalog.domain.CatalogModels.Offer;
import com.feelm.catalog.domain.CatalogModels.PersonCredit;
import com.feelm.catalog.domain.CatalogModels.Provider;
import com.feelm.catalog.domain.CatalogModels.SimilarityItem;
import com.feelm.catalog.domain.CatalogModels.SimilarityReason;
import com.feelm.catalog.domain.CatalogModels.SnapshotFetchStatus;
import com.feelm.catalog.domain.CatalogReadPort;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Repository;

import java.math.BigDecimal;
import java.net.URI;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.UUID;

@Repository
@Profile("!postgres")
public class FixtureCatalogReadAdapter implements CatalogReadPort {
    public static final String CATALOG_VERSION = "catalog-fixture-20260829-01";
    public static final String SIMILARITY_VERSION = "sim-fixture-v1";

    public static final UUID MOV_KO_FULL = uuid("6b226903-0ca4-4f5a-9bf0-50d6cedd224c");
    public static final UUID MOV_EN_FALLBACK = uuid("19406c31-213f-4fe1-93f6-109f8570ec20");
    public static final UUID MOV_NO_POSTER = uuid("97204ea5-e6e5-4417-a13f-bc8197660705");
    public static final UUID MOV_NONE_LISTED = uuid("e8f7cf02-9bc4-4ff7-87b7-12fb02dd2490");
    public static final UUID MOV_OTT_UNKNOWN = uuid("1958ba3a-3d8c-4a4f-8845-124c0b12373e");
    public static final UUID MOV_OTT_STALE = uuid("0437c1c0-06d5-4cdf-a7d1-5d5f1dc42e89");
    public static final UUID MOV_STALE_RECOVERED = uuid("c886c3ca-52d6-45c6-bdbc-89fbfce62d3c");
    public static final UUID MOV_TV_MISMATCH = uuid("8524f2c2-aaeb-48ff-a21d-df544df23d46");
    public static final UUID MOV_SIMILAR_1 = uuid("e67778c9-7b2e-42d4-9d3e-a3026b2efea3");
    public static final UUID MOV_SIMILAR_2 = uuid("cc3ddb45-0511-46ea-bf28-95b67c9fd20f");

    public static final UUID NETFLIX = uuid("d392a4d5-0428-4e06-aa41-aef899c06842");
    public static final UUID WATCHA = uuid("4f57022d-6d8e-40b2-b7be-4ac313ef6bd0");
    public static final UUID WAVVE = uuid("1f0c5888-f6f4-42a9-b661-a90cff45e303");
    public static final UUID GOOGLE_PLAY = uuid("7012659c-f25e-429b-9fda-21528dc6cd1b");

    public static final UUID CRIME = uuid("2d07d5d3-486f-4638-9d58-49331e798c76");
    public static final UUID THRILLER = uuid("475dc158-d914-46ec-a59c-a48791e6ae8f");
    public static final UUID DRAMA = uuid("165a3c6f-9b81-4420-9713-c59303d5bb92");

    private final CatalogSnapshot snapshot;

    public FixtureCatalogReadAdapter() {
        Genre crime = new Genre(CRIME, "범죄", 10);
        Genre thriller = new Genre(THRILLER, "스릴러", 20);
        Genre drama = new Genre(DRAMA, "드라마", 30);
        Country us = new Country("US", "미국");
        Country fr = new Country("FR", "프랑스");
        Country kr = new Country("KR", "대한민국");

        Provider netflix = provider(NETFLIX, "Netflix", 10);
        Provider watcha = provider(WATCHA, "Watcha", 20);
        Provider wavve = provider(WAVVE, "wavve", 30);
        Provider google = provider(GOOGLE_PLAY, "Google Play Movies", 40);

        PersonCredit louis = director("88bc6285-b82b-491d-9cae-ab17c3d7a9cf", "Louis Leterrier", 0);
        PersonCredit jesse = cast("336ef1c3-2df8-4c24-9139-58beac956ad4", "Jesse Eisenberg", "J. Daniel Atlas", 0);
        PersonCredit mark = cast("aa60da55-46b0-4e51-a604-75e54b73d711", "Mark Ruffalo", "Dylan Rhodes", 1);
        PersonCredit fixtureDirector = director("1e6d9d1a-2c83-4498-a7bb-f1e31b93dbd2", "Fixture Director", 0);

        AvailabilitySnapshot freshListed = listed(
                "2026-08-29T06:00:00Z",
                offer("4c411f48-9990-4938-9f6c-cf17b42ce4cb", NETFLIX, MonetizationType.FLATRATE),
                offer("82d84bfc-a318-4dd6-9c22-fd84945ac88a", WAVVE, MonetizationType.FLATRATE),
                offer("41ad9c91-f5ea-498d-9e0b-1bd5c7013a5a", NETFLIX, MonetizationType.RENT),
                offer("9a56f31a-4c26-46d8-ae89-16c484134e18", NETFLIX, MonetizationType.BUY),
                offer("5e779354-bc51-43c4-abbe-e80063301098", GOOGLE_PLAY, MonetizationType.RENT)
        );
        AvailabilitySnapshot watchaListed = listed(
                "2026-08-29T06:00:00Z",
                offer("780702d1-a92d-4f78-9d0c-f327748b6281", WATCHA, MonetizationType.FLATRATE)
        );
        AvailabilitySnapshot noneListed = empty("2026-08-29T06:00:00Z");
        AvailabilitySnapshot staleListed = listedAt(
                "2026-08-26T12:00:00Z",
                "2026-08-27T12:00:00Z",
                "2026-09-02T12:00:00Z",
                offer("afaa874e-20d0-42de-a143-f89ee8f706d5", NETFLIX, MonetizationType.FLATRATE)
        );

        List<Movie> movies = List.of(
                movie(MOV_KO_FULL, "나우 유 씨 미", "ko-KR", "Now You See Me", "마술사들이 펼치는 완벽한 범죄.", "ko-KR",
                        LocalDate.of(2013, 5, 29), 115, image("now-you-see-me.jpg"), List.of(crime, thriller), List.of(us, fr),
                        List.of(louis), List.of(jesse, mark), "나우 유 씨 미 now you see me Louis Leterrier Jesse Eisenberg Mark Ruffalo", 100, true, true, freshListed),
                movie(MOV_EN_FALLBACK, "The English Fallback", "en-US", "The English Fallback", "English overview fallback.", "en-US",
                        LocalDate.of(2018, 3, 1), 102, image("en-fallback.jpg"), List.of(drama), List.of(us),
                        List.of(fixtureDirector), List.of(), "the english fallback fixture director", 80, true, true, watchaListed),
                movie(MOV_NO_POSTER, "포스터 없는 영화", "ko-KR", "No Poster Movie", "포스터가 없어도 상세를 볼 수 있다.", "ko-KR",
                        LocalDate.of(2012, 7, 10), 95, null, List.of(drama), List.of(kr),
                        List.of(fixtureDirector), List.of(), "포스터 없는 영화 no poster movie", 10, true, false, null),
                movie(MOV_NONE_LISTED, "현재 제공처 없음", "ko-KR", "Nothing Listed", "최근 성공 조회에 제공처가 없다.", "ko-KR",
                        LocalDate.of(2020, 1, 1), 99, image("none-listed.jpg"), List.of(drama), List.of(kr),
                        List.of(fixtureDirector), List.of(), "현재 제공처 없음 nothing listed", 50, true, true, noneListed),
                movie(MOV_OTT_UNKNOWN, "시청 옵션 미확인", "ko-KR", "OTT Unknown", "성공한 시청 옵션 스냅샷이 없다.", "ko-KR",
                        LocalDate.of(2021, 2, 2), 101, image("unknown.jpg"), List.of(thriller), List.of(us),
                        List.of(fixtureDirector), List.of(), "시청 옵션 미확인 ott unknown", 40, true, true, null),
                movie(MOV_OTT_STALE, "오래된 시청 옵션", "ko-KR", "Stale OTT", "마지막 정상 데이터를 제한적으로 제공한다.", "ko-KR",
                        LocalDate.of(2019, 4, 3), 105, image("stale.jpg"), List.of(thriller), List.of(us),
                        List.of(fixtureDirector), List.of(), "오래된 시청 옵션 stale ott", 60, true, true, staleListed),
                movie(MOV_STALE_RECOVERED, "복구된 영화", "ko-KR", "Recovered Movie", "외부 ID가 복구된 영화다.", "ko-KR",
                        LocalDate.of(2017, 6, 4), 98, image("recovered.jpg"), List.of(drama), List.of(us),
                        List.of(fixtureDirector), List.of(), "복구된 영화 recovered movie", 30, true, true, null),
                movie(MOV_TV_MISMATCH, "공개 금지 TV", "ko-KR", "TV mismatch", "TV 항목.", "ko-KR",
                        LocalDate.of(2022, 1, 1), 50, image("tv.jpg"), List.of(drama), List.of(us),
                        List.of(), List.of(), "공개 금지 tv mismatch", 200, false, false, null),
                movie(MOV_SIMILAR_1, "인사이드 맨", "ko-KR", "Inside Man", "범죄의 이면을 파고드는 영화.", "ko-KR",
                        LocalDate.of(2006, 3, 24), 129, image("similar-1.jpg"), List.of(crime, thriller), List.of(us),
                        List.of(louis), List.of(), "인사이드 맨 inside man Louis Leterrier", 75, true, true, null),
                movie(MOV_SIMILAR_2, "프레스티지", "ko-KR", "The Prestige", "두 마술사의 집요한 대결.", "ko-KR",
                        LocalDate.of(2006, 10, 20), 130, image("similar-2.jpg"), List.of(crime, drama), List.of(us),
                        List.of(fixtureDirector), List.of(), "프레스티지 the prestige 마술", 70, true, true, null)
        );

        this.snapshot = new CatalogSnapshot(
                CATALOG_VERSION,
                SIMILARITY_VERSION,
                movies,
                List.of(crime, thriller, drama),
                List.of(us, fr, kr),
                List.of(netflix, watcha, wavve, google),
                Map.of(MOV_KO_FULL, List.of(
                        new SimilarityItem(MOV_SIMILAR_1, List.of(
                                new SimilarityReason("SHARED_GENRE", "같은 범죄 장르"),
                                new SimilarityReason("SHARED_DIRECTOR", "같은 감독")
                        )),
                        new SimilarityItem(MOV_SIMILAR_2, List.of(
                                new SimilarityReason("SHARED_GENRE", "같은 범죄 장르"),
                                new SimilarityReason("SHARED_KEYWORD", "마술 소재")
                        )),
                        new SimilarityItem(MOV_NO_POSTER, List.of(new SimilarityReason("SHARED_GENRE", "같은 장르"))),
                        new SimilarityItem(MOV_TV_MISMATCH, List.of(new SimilarityReason("TEXT_SIMILARITY", "설명 유사")))
                ))
        );
    }

    @Override
    public CatalogSnapshot loadActiveSnapshot() {
        return snapshot;
    }

    private static Movie movie(
            UUID id, String title, String titleLocale, String originalTitle, String overview, String overviewLocale,
            LocalDate releaseDate, Integer runtime, URI poster, List<Genre> genres, List<Country> countries,
            List<PersonCredit> directors, List<PersonCredit> cast, String search, double popularity,
            boolean visible, boolean uiReady, AvailabilitySnapshot availability
    ) {
        return new Movie(
                id, title, titleLocale, originalTitle, overview, overviewLocale, releaseDate, runtime, poster,
                image("backdrop-" + id + ".jpg"), genres, countries, directors, cast,
                new ExternalRating("TMDB", new BigDecimal("7.30"), 10, Math.round(popularity * 128)),
                Instant.parse("2026-08-29T05:00:00Z"), search, popularity, Math.round(popularity * 128),
                visible, uiReady, availability
        );
    }

    private static Provider provider(UUID id, String name, int priority) {
        return new Provider(id, name, image("provider-" + id + ".jpg"), priority);
    }

    private static PersonCredit director(String id, String name, int order) {
        return new PersonCredit(uuid(id), name, CreditRole.DIRECTOR, null, order);
    }

    private static PersonCredit cast(String id, String name, String character, int order) {
        return new PersonCredit(uuid(id), name, CreditRole.CAST, character, order);
    }

    private static AvailabilitySnapshot listed(String fetchedAt, Offer... offers) {
        Instant fetched = Instant.parse(fetchedAt);
        return listedAt(fetchedAt, fetched.plusSeconds(24 * 3600).toString(), fetched.plusSeconds(7 * 24 * 3600).toString(), offers);
    }

    private static AvailabilitySnapshot listedAt(String fetchedAt, String freshUntil, String serveUntil, Offer... offers) {
        return new AvailabilitySnapshot(
                SnapshotFetchStatus.SUCCESS_LISTED,
                Instant.parse(fetchedAt),
                Instant.parse(freshUntil),
                Instant.parse(serveUntil),
                List.of(offers)
        );
    }

    private static AvailabilitySnapshot empty(String fetchedAt) {
        Instant fetched = Instant.parse(fetchedAt);
        return new AvailabilitySnapshot(
                SnapshotFetchStatus.SUCCESS_EMPTY,
                fetched,
                fetched.plusSeconds(24 * 3600),
                fetched.plusSeconds(7 * 24 * 3600),
                List.of()
        );
    }

    private static Offer offer(String id, UUID providerId, MonetizationType type) {
        return new Offer(
                uuid(id), providerId, type, LinkType.AGGREGATOR,
                URI.create("https://www.themoviedb.org/movie/fixture/watch")
        );
    }

    private static URI image(String filename) {
        return URI.create("https://image.tmdb.org/t/p/w500/" + filename);
    }

    private static UUID uuid(String value) {
        return UUID.fromString(value);
    }
}
