# Independent review record

Status: DRAFT local research. 2026-09-12. No external posting or team contract change.

## Source audit — /root/asset_audit

Read-only independent actual source/time checks PASS. No future label values decoded by reviewer.

- text339 prepared-seal SHA256 d27709a42bd0d5511a36f3740365637325d59b521c7f62eae04850523782fdd8.
- Train ratings SHA256 28b46687abec2e0edb3a892ec4f4dbd9d5cca701bf220b2812f9e7cf9b905a63. 4,997,069 unique user/movie rows, 39,859 train users, 45,074 movies; latest timestamp1672530707 < origin1672531200. Evaluation overlap0.
- Context SHA256 951fc2464bd3aea25ea486c79c33084f6241f7846a3ed626e9d5fc3907a7aab7. 1,350 contexts,270 users×5 caps;18,646 unique original targets; all timestamps legal. Labels are keyed joins, not context offsets.
- Catalog SHA256 0bde668e0e26f5f82b5c41d90d62c7569fd350bf2a2fd4b59bb4402f438c5947. Every recomputed train count matches catalog.
- Labels file hash only: e3bf301a6e2ea7885b59bcab7fe83c2d3ad84f93f2f1bbb2d54d943b9a658db8.
- ALS fit-seal SHA256 702fb7fd8b82dabd607811064ed02fdd6e46cd319e718072a01a00328c1e7282. All18 inventory files / 8 Parquet parts match;5,720,692 bytes.45,074×32 finite factors; IDs exactly train MovieLens movie IDs.
- Canonical factor inventory SHA2564631e4a9ba5ecff72a0e66063bc819b3fe41a0acd364165c71af75e09f9d4f4e: JSON relative paths→pin, sort_keys, compact separators, UTF8.
- CPU runtime Python3.12.5, NumPy1.26.4, SciPy1.15.2, pandas2.2.3, PyArrow23.0.1, sklearn1.9.0. Existing270 users are repeatedly used development subjects.

## First design review — /root/design_review

REQUEST_CHANGES resolved before any model execution: exact graded gain vs positive cohort definition; same selected hierarchy for both policies' discovery metric; zero-padding; cap0 distinguished from actual zero-history; movie-unit joint bootstrap across all feature masks; common GKT candidate support; child distortion explicitly conditional/transductive; fallback and ties fixed. Reviewer additionally found old viewed histories cover only85,517-item axis. New code scans all128 raw rating parts for full service-mapped pre-origin viewed sets.

## First code review — /root/design_review

REQUEST_CHANGES: homogeneous parents could crash when K exceeded distinct vectors; predecessor artifacts were read without validating seals. Corrections add distinct/zero-loss stop, explicit fallback log, published source digest assertions, complete predecessor inventory/hash checks. Synthetic singleton/batch/OOV/tie checks passed. OOV flags, null-list input contract and finite-bootstrap checks added.

## Recommendation code review — /root/asset_audit

REQUEST_CHANGES: anchor independently audited source seals/labels, check complete ALS inventory; baseline latency incorrectly included unused group/profile calculations, and popularity calculated ignored predictions. Corrections pin sources in config, verify complete inventory, restrict timed work to each policy's necessary computation, compute diagnostic exposure after timing and add discarded warmup plus policy-order rotation. Reviewer independently reproduced ALS algebra, empty/cold prediction, candidate budget/dedup/seen rules, graded IDCG and short-list metrics. No experiment results were inspected.

## Local synthetic tests

`python scripts/k8_tests.py`:6 tests PASS. Covers exact ties, append/delete/batch/order; signed negative and empty profiles; underseen counts; all-seen/empty fallback and candidate caps; known/UNKNOWN metrics and zero-padding; cold predictor and rating bounds.

## User steering while awaiting execution

User asked “8개?? 좀 더 들려볼 필요가 있을 것 같은데”. Explained that top8 is an inherited requested constraint, not an experimental finding, and child{1,2,4,8} is a proposed search range. Clarification requested: retain top8 and expand child search, compare topK too, or explain design first. No real source preparation or model training has run at this point. Continue independent correctness work; do not choose the dependent count scope before clarification.

User then confirmed top8 unchanged and asked child128/256. The final pre-execution protocol includes all feasible child K through256, cap8/32/128/256 comparisons and explicit alias handling. Two reviewers rechecked exact final code and7 synthetic tests; execution-review.json pins that version. The final fixed2 correction requires at least2 distinct TRAIN vectors and200 informative train rows. The reviewed train hash is29b7b1a5a605fdae858b3d798afee9e793ea224e466e64d914abef718ca36121.

## Prepare result — /root/design_review — PASS

Reconciled all237817 service/mapping rows and128 mapping parts; unique positive service/TMDb IDs and sort; raw availability,14 missing keyword files vs129131 empty arrays; mapping statuses and24adult/511video/0overlap. All3 preparation artifacts and reviewed fingerprints match. Full JSON streaming assertions ran in preparation; reviewer independently checked tables, not a second full JSON parse. Catalog hash f16abeb51ccff940a73480aa07347a44502524c82f29d9b7f1fde379b844006f; prepare-seal963a5bd3a4598c3ccf8fa0dd059e89761951d36755c0c0efb5b1f1105b9d6844.

## Train result — /root/design_review — PASS

Independently regenerated exact features, train vocab/IDF and128 direct projections. Original9 selection fits were not separately persisted: reviewer reran all9 identical fits in memory without new candidates or recommendation labels. All validation D8/block losses/inertia/iteration counts, verification reconstruction losses,2000 bootstrap replicates and6 seed-stability pairs match. All final8-parent and9 hierarchy assignments were recomputed from saved centers. K12823fit/1skip andK25621fit/3skip; none met minimum100-per-child. Audit59.93seconds, sampled peak RSS1066307584bytes. Train-seal a9e88305c703e0b9924a7cb7f122e701e5644958403d935b62e7bb62e1eaa09d.

## Recommendation result — /root/asset_audit — PASS

Recomputed all59130 validation,2160 verification and360 current-supply metric rows, including candidate budgets/duplicates/seen/eligibility, NDCG2/6/10, Recall10/candidate recall, UNKNOWN/known stars/slots, exposure, novelty and latency aggregates. Rebuilt all2520 verification/supply requests' exact candidates/ranks:0 mismatches. All32694 observed prediction rows match with maxdiff0; independent primal/dual ridge differs at most4.996e-16. Re-read all128 raw rating parts (IDs/time only) and rebuilt1350 contexts and all joins. Exact selection, BAND_EMPTY, paired bootstrap and original overall gate reproduce. Recommendation-seal2f83b17ef6a1959814354105f1d7a10ab85c9d6499e37b17d77bb13e62681ccd.

## Final artifact — /root/design_review — PASS

Verified237817 CSV/Parquet IDs and nesting,8taste/16group names, weak/OOV counts; independently reassigned1280 original records, reversed/reloaded/17batched/added samples.33 Korean-example ID pairs match. Entire root invariant suite passed all237817 rows. Manifest9721ac7fa3ee1887b45505965b23944718587fecc4022bb851a51a408d4c6ae9; final-seal6ae45a2920214fae5a0d58c6688ba3108149b776918232e211fe6c7a3e457287; assignments095c157daa77ffdd6d09fd4f33341b51bed81890f57095492112b5ef75a54708.

Reviewer verified corrected claims: keep original same-budget overall gate, but personalized application HOLD; BAND_EMPTY, popularity higher, candidate recall lower, warm/cold differences and subgroup15 evidence deficit disclosed. No score/tuning/selection changes followed this review. README records nonsemantic sklearn process cache and all-or-none CLI ID-column contract.

## Local serving adapter — /root/asset_audit — PASS

Separate post-freeze integration code does not alter measured experiment or frozen classifier. First review fixed cap-before-map order and strict ID/half-star validation, including atomic invalid removal rejection.34 independent validation checks passed. serve_fixed_k8.py hash fa54cd709fd67e9d261242a41acb54f09d77dd53e9254832cdb125166fdd3f69; checker ec63e9f2f16e1cde93d54bcac5b04ae6105f838c27a82c047a530f842a5add0e. Full237817-film acceptance rerun after review passed append/upsert/delete/prior preservation, empty/OOV/negative/unknown inputs, all caps, all seen and no active movies. Earlier result was preserved separately. Pandas future dtype warnings are recorded; current pinned runtime checks pass.

## Report and visual QA

Static report review corrected source verification, complete-row curve selection, capped-history/known-slot/support tables and vote-example wording. Report/plot inputs and script hashes are saved. Root rendered and viewed bothPNG figures, fixed logarithmic tick glyphs, and checked no clipping/overlap. Final worktree diff check and source-worktree status checks are recorded separately. No commit/push/remote writes.
