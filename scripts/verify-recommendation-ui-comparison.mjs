import { createHash } from "node:crypto";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";

const root = process.cwd();
const manifestPath = "docs/recommendation/evidence/manifests/rec-ev-008.json";
const manifest = JSON.parse(await readText(manifestPath));
const errors = [];

assert(manifest.evidence_id === "REC-EV-008", "evidence_id must be REC-EV-008");
assert(
  manifest.status === "COMPLETED_UI_COMPARISON_EVIDENCE",
  "manifest status must be completed UI comparison evidence",
);
assert(manifest.authority?.product_ui_approved === false, "product UI must remain unapproved");
assert(manifest.authority?.public_navigation_enabled === false, "public navigation must remain disabled");
assert(manifest.authority?.public_api_added === false, "public API must remain unchanged");
assert(manifest.protocol?.actual_user_study === false, "manifest must not claim a user study");
assert(manifest.validation?.frontend_full_suite === "PASS_26_OF_26", "frontend suite result is not final");
assert(manifest.validation?.frontend_production_build === "PASS", "frontend build result is not final");
assert(
  manifest.validation?.production_evidence_route_scan === "PASS_NOT_REGISTERED_OR_BUNDLED",
  "production evidence route scan result is not final",
);

const expectedComparisons = new Set([
  "EXPECTED_STAR_VISIBLE_VS_NOT_COMPUTED",
  "ONBOARDING_K5_VS_K10_VS_SKIP",
  "PARTY_AVERAGE_VS_BALANCED_CANDIDATE",
  "REASON_ONE_VS_UP_TO_THREE",
]);
assertSet(
  new Set(manifest.comparisons?.map((comparison) => comparison.id)),
  expectedComparisons,
  "comparison set",
);

const party = manifest.comparisons.find(
  (comparison) => comparison.id === "PARTY_AVERAGE_VS_BALANCED_CANDIDATE",
);
assert(party?.improvement_supported === false, "Balanced improvement must remain unsupported");
assert(party?.all_three_paired_ci_cross_zero === true, "three Balanced paired CIs must cross zero");

const reason = manifest.comparisons.find(
  (comparison) => comparison.id === "REASON_ONE_VS_UP_TO_THREE",
);
assert(reason?.reason_evidence_status === "COMPLETED_OFFLINE_EVIDENCE", "REC-EV-006 must be connected");
assert(
  reason?.same_recommendation_three_reason_cooccurrence_claimed === false,
  "three-reason co-occurrence must not be claimed",
);

for (const artifact of [...manifest.sources, ...manifest.implementation, ...manifest.screenshots]) {
  const bytes = await readFile(path.resolve(root, artifact.path));
  assert(
    artifactMatches(bytes, artifact),
    `${artifact.path} byte size or SHA-256 drift`,
  );
}

for (const screenshot of manifest.screenshots) {
  const bytes = await readFile(path.resolve(root, screenshot.path));
  assert(bytes.subarray(1, 4).toString("ascii") === "PNG", `${screenshot.path} is not PNG`);
  assert(bytes.readUInt32BE(16) === 1440, `${screenshot.path} width drift`);
  assert(bytes.readUInt32BE(20) === 1200, `${screenshot.path} height drift`);
}

const rec006 = JSON.parse(await readText("docs/recommendation/evidence/manifests/rec-ev-006.json"));
const reasonContract = JSON.parse(
  await readText("docs/recommendation/evidence/manifests/rec-ev-006-reason-contract.json"),
);
assert(rec006.validation?.status === "PASS", "REC-EV-006 validation must pass");
assert(rec006.validation?.reason_ui_approved === false, "REC-EV-006 must not approve reason UI");
assert(reasonContract.displayCountApproved === false, "reason display count must remain unapproved");
assert(reasonContract.uiCopyApproved === false, "reason copy must remain unapproved");

const app = await readText("frontend/src/App.tsx");
const localFeatureGate = await readText("frontend/src/config/localFeatures.ts");
assert(app.includes("localFeaturesEnabled"), "evidence route must use the shared local feature gate");
assert(localFeatureGate.includes("import.meta.env.DEV"), "local feature gate must include DEV mode");
assert(
  localFeatureGate.includes('VITE_LOCAL_FEATURES_ENABLED === "true"'),
  "local feature gate must require an explicit production-build opt-in",
);
assert(app.includes('/__evidence/rec-ev-008'), "DEV evidence route is missing");
const publicSources = [
  "frontend/src/pages/SearchHomePage.tsx",
  "frontend/src/pages/SearchResultsPage.tsx",
  "frontend/src/pages/MovieDetailPage.tsx",
  "frontend/src/pages/C1Pages.tsx",
];
for (const source of publicSources) {
  assert(!(await readText(source)).includes("__evidence"), `${source} exposes evidence navigation`);
}
assert(
  !(await readText("docs/api/openapi.yaml")).includes("rec-ev-008"),
  "main OpenAPI exposes REC-EV-008",
);

const productionFiles = await listFiles(path.resolve(root, "frontend/dist"));
assert(productionFiles.length > 0, "frontend production build is missing; run npm run build --prefix frontend");
for (const productionFile of productionFiles.filter((file) => /\.(?:html|js|css)$/.test(file))) {
  const bundle = await readFile(productionFile, "utf8");
  assert(!bundle.includes("REC-EV-008"), `${productionFile} bundles REC-EV-008`);
  assert(!bundle.includes("__evidence/rec-ev-008"), `${productionFile} registers the evidence route`);
  assert(!bundle.includes("INTERNAL EVIDENCE LAB"), `${productionFile} bundles internal lab copy`);
}

const report = await readText("docs/recommendation/evidence/REC-EV-008-ui-comparison.md");
for (const phrase of [
  "제품 UI 승인: `NO`",
  "실제 사용자 연구: `NOT_RUN`",
  "세 paired-bootstrap CI가 모두 0을 포함",
  "실제 typed coverage",
  "public navigation",
]) {
  assert(report.includes(phrase), `report missing boundary phrase: ${phrase}`);
}

if (errors.length) {
  console.error("REC-EV-008 verification failed:");
  for (const error of errors) console.error(`- ${error}`);
  process.exit(1);
}

console.log(
  "REC-EV-008 verification passed: 4 comparisons, 4 same-viewport screenshots, DEV-only route, no product/API approval.",
);

function assert(condition, message) {
  if (!condition) errors.push(message);
}

function assertSet(actual, expected, label) {
  const missing = [...expected].filter((value) => !actual.has(value));
  const extra = [...actual].filter((value) => !expected.has(value));
  assert(missing.length === 0 && extra.length === 0, `${label} drift: missing=${missing} extra=${extra}`);
}

async function readText(relativePath) {
  return readFile(path.resolve(root, relativePath), "utf8");
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function artifactMatches(bytes, artifact) {
  const candidates = [bytes];
  if (artifact.path.endsWith(".json")) {
    const lf = Buffer.from(bytes.toString("utf8").replace(/\r\n/g, "\n"), "utf8");
    const crlf = Buffer.from(lf.toString("utf8").replace(/\n/g, "\r\n"), "utf8");
    candidates.push(lf, crlf);
  }
  return candidates.some(
    (candidate) => candidate.length === artifact.bytes && sha256(candidate) === artifact.sha256,
  );
}

async function listFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...(await listFiles(target)));
    else if (entry.isFile()) files.push(target);
  }
  return files;
}
