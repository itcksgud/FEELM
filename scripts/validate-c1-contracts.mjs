import fs from "node:fs";
import path from "node:path";
import YAML from "yaml";

const root = process.cwd();
const c1 = path.join(root, "docs", "c1-draft");

function read(relative) {
  return fs.readFileSync(path.join(root, relative), "utf8");
}

function ids(text, pattern) {
  return new Set(text.match(pattern) ?? []);
}

function splitRefs(value) {
  return value ? value.split("|").map((item) => item.trim()).filter(Boolean) : [];
}

function fail(message) {
  throw new Error(`C1 contract validation failed: ${message}`);
}

const registry = JSON.parse(read("docs/spec/approved-slices.json"));
const publicSlices = new Map((registry.publicProductSlices ?? []).map((slice) => [slice.sliceId, slice]));
const c0Slice = publicSlices.get("C0_CATALOG");
const c1Slice = publicSlices.get("C1_RATING_FILM");
const c2bLocalSlice = publicSlices.get("C2B_LOCAL_BASELINE_DISCOVERY");
const allowedPublicSlices = new Set([
  "C0_CATALOG",
  "C1_RATING_FILM",
  "C2B_LOCAL_BASELINE_DISCOVERY",
]);
if (!c0Slice || !c1Slice || [...publicSlices.keys()].some((sliceId) => !allowedPublicSlices.has(sliceId))) {
  fail("public product authority must retain C0_CATALOG and C1_RATING_FILM without unknown slices");
}
if (c0Slice.status !== "APPROVED" || c0Slice.contractMode !== "BASE") {
  fail("C0 base registry state conflict");
}
if (c1Slice.status !== "APPROVED" || c1Slice.contractMode !== "EXTENSION"
    || c1Slice.root !== "docs/c1-draft" || c1Slice.stablePath !== true) {
  fail("C1 extension registry state conflict");
}
if (!c2bLocalSlice
    || c2bLocalSlice.status !== "APPROVED_LOCAL_BASELINE_WITH_BLOCKED_EXTENSIONS"
    || c2bLocalSlice.contractMode !== "LOCAL_BASELINE_EXTENSION"
    || c2bLocalSlice.root !== "docs/c2b-personal-discovery"
    || c2bLocalSlice.productionActivation !== false) {
  fail("C2B local-only registry state conflict or production promotion detected");
}
const c2Slice = (registry.internalSlices ?? []).find((slice) => slice.sliceId === "C2A_RECOMMENDATION_INTERNAL");
if (!c2Slice || c2Slice.status !== "APPROVED_C2A_INTERNAL_POPULARITY_ONLY"
    || c2Slice.includedInPublicProductAuthority !== false) {
  fail("C2A internal contract was promoted or its state conflicts");
}
const commonScope = read("docs/spec/00-product-scope.md");
if (!commonScope.includes("승인 공개 제품 Slice: C0 Catalog + C1 Rating·Film")) {
  fail("common product scope regressed to C0-only authority");
}
for (const bridge of ["docs/spec/README.md", "docs/ui/README.md", "docs/data/README.md", "docs/testing/README.md", "docs/traceability/README.md"]) {
  const text = read(bridge);
  if (!text.includes("docs/spec/approved-slices.json") || !text.includes("docs/c1-draft")) {
    fail(`${bridge} does not bridge C0 base and C1 extension`);
  }
}

const openapi = YAML.parse(read("docs/c1-draft/api/openapi.fragment.yaml"));
const mergedOpenapi = YAML.parse(read("docs/api/openapi.yaml"));
const operations = new Set();
for (const pathItem of Object.values(openapi.paths ?? {})) {
  for (const method of ["get", "post", "put", "patch", "delete"]) {
    const operation = pathItem?.[method];
    if (!operation) continue;
    if (!operation.operationId) fail(`${method.toUpperCase()} operationId missing`);
    if (operations.has(operation.operationId)) fail(`duplicate operationId ${operation.operationId}`);
    operations.add(operation.operationId);
  }
}

const mergedOperations = new Map();
for (const [url, pathItem] of Object.entries(mergedOpenapi.paths ?? {})) {
  for (const method of ["get", "post", "put", "patch", "delete"]) {
    const operation = pathItem?.[method];
    if (operation?.operationId) mergedOperations.set(operation.operationId, { url, method, operation });
  }
}
for (const operationId of operations) {
  const merged = mergedOperations.get(operationId);
  if (!merged) fail(`${operationId} is not merged into docs/api/openapi.yaml`);
  const requiredBearer = (merged.operation.security ?? []).some((entry) => Object.hasOwn(entry, "bearerAuth"));
  if (!requiredBearer) fail(`${operationId} does not require bearerAuth in merged OpenAPI`);
}

const screens = ids(read("docs/c1-draft/ui/screen-contracts.md"), /\bSCR-C1-\d{3}\b/g);
const rules = ids(read("docs/c1-draft/02-business-rules.md"), /\bBR-C1-\d{3}\b/g);
const acceptance = ids(read("docs/c1-draft/testing/acceptance-tests.md"), /\bAC-C1-\d{3}\b/g);
const decisions = ids(read("docs/c1-draft/decision-needed.md"), /\bDN-C1-\d{3}\b/g);
const backlog = YAML.parse(read("docs/c1-draft/tasks/implementation-backlog.yaml"));
const tasks = new Map((backlog.tasks ?? []).map((task) => [task.id, task]));

for (const task of tasks.values()) {
  for (const dependency of task.depends_on ?? []) {
    if (!tasks.has(dependency)) fail(`${task.id} has unknown dependency ${dependency}`);
  }
  const incomplete = (task.depends_on ?? []).filter((dependency) => tasks.get(dependency).status !== "DONE");
  if (task.status === "READY" && incomplete.length) fail(`${task.id} READY with incomplete ${incomplete.join(", ")}`);
  if (task.status === "DONE" && incomplete.length) fail(`${task.id} DONE with incomplete ${incomplete.join(", ")}`);
  if (task.status === "BLOCKED" && !incomplete.length) fail(`${task.id} BLOCKED without incomplete dependency`);
  for (const ac of task.acceptance_ids ?? []) {
    if (!acceptance.has(ac)) fail(`${task.id} references unknown ${ac}`);
  }
}

const traceLines = read("docs/c1-draft/traceability/requirements.csv").trim().split(/\r?\n/);
const headers = traceLines.shift().split(",");
const tracedTestIds = new Set();
for (const line of traceLines) {
  const values = line.split(",");
  if (values.length !== headers.length) fail(`trace row has ${values.length}/${headers.length} columns: ${line}`);
  const row = Object.fromEntries(headers.map((header, index) => [header, values[index]]));
  for (const ref of splitRefs(row.decision_ids)) if (!decisions.has(ref)) fail(`${row.requirement_id}: unknown ${ref}`);
  for (const ref of splitRefs(row.business_rule_ids)) if (!rules.has(ref)) fail(`${row.requirement_id}: unknown ${ref}`);
  for (const ref of splitRefs(row.screen_ids)) if (!screens.has(ref)) fail(`${row.requirement_id}: unknown ${ref}`);
  for (const ref of splitRefs(row.operation_ids)) if (!operations.has(ref)) fail(`${row.requirement_id}: unknown operation ${ref}`);
  for (const ref of splitRefs(row.acceptance_ids)) if (!acceptance.has(ref)) fail(`${row.requirement_id}: unknown ${ref}`);
  for (const ref of splitRefs(row.task_ids)) if (!tasks.has(ref)) fail(`${row.requirement_id}: unknown ${ref}`);
  for (const ref of splitRefs(row.test_ids)) tracedTestIds.add(ref);
}

const automatedTestDocument = read("docs/testing/c1-automated-tests.md");
const knownTestIds = ids(
  automatedTestDocument,
  /\b(?:TEST-(?:CONTRACT|BE|FE|E2E)-C1-[A-Z0-9-]+|TEST-SEC-C1)\b/g,
);
for (const testId of tracedTestIds) {
  if (!knownTestIds.has(testId)) fail(`traceability references undeclared test ID ${testId}`);
}

const mapLines = read("docs/testing/c1-ac-test-map.csv").trim().split(/\r?\n/);
const mapHeaders = mapLines.shift().split(",");
const expectedMapHeaders = ["acceptance_id", "test_id", "test_source", "test_locator", "evidence_state", "notes"];
if (JSON.stringify(mapHeaders) !== JSON.stringify(expectedMapHeaders)) fail("C1 AC test map columns conflict");
const mappedAcceptance = new Set();
let automatedCount = 0;
let gapCount = 0;
for (const line of mapLines) {
  const values = line.split(",");
  if (values.length !== mapHeaders.length) fail(`AC test map row has ${values.length}/${mapHeaders.length} columns: ${line}`);
  const row = Object.fromEntries(mapHeaders.map((header, index) => [header, values[index]]));
  if (!acceptance.has(row.acceptance_id)) fail(`AC test map references unknown ${row.acceptance_id}`);
  if (mappedAcceptance.has(row.acceptance_id)) fail(`AC test map duplicates ${row.acceptance_id}`);
  mappedAcceptance.add(row.acceptance_id);
  if (!knownTestIds.has(row.test_id)) fail(`${row.acceptance_id} references undeclared test ID ${row.test_id}`);
  if (row.evidence_state === "AUTOMATED") {
    automatedCount += 1;
    if (!row.test_source || !row.test_locator) fail(`${row.acceptance_id} automated evidence is incomplete`);
    const source = path.join(root, row.test_source);
    if (!fs.existsSync(source)) fail(`${row.acceptance_id} test source does not exist: ${row.test_source}`);
    if (!fs.readFileSync(source, "utf8").includes(row.test_locator)) {
      fail(`${row.acceptance_id} locator is absent from ${row.test_source}: ${row.test_locator}`);
    }
  } else if (row.evidence_state === "GAP") {
    gapCount += 1;
    if (row.test_source || row.test_locator) fail(`${row.acceptance_id} GAP must not claim source evidence`);
  } else {
    fail(`${row.acceptance_id} has invalid evidence_state ${row.evidence_state}`);
  }
}
for (const acceptanceId of acceptance) {
  if (!mappedAcceptance.has(acceptanceId)) fail(`acceptance has no machine mapping: ${acceptanceId}`);
}

const allFiles = [];
function walk(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const candidate = path.join(directory, entry.name);
    if (entry.isDirectory()) walk(candidate);
    else allFiles.push(candidate);
  }
}
walk(c1);
const allText = allFiles.map((file) => fs.readFileSync(file, "utf8")).join("\n");
for (const marker of ["EVIDENCE_REQUIRED", "BLOCKED_BY_DECISION", "x-evidence-required", "[BLOCKED DN-C1-001]", "[BLOCKED DN-C1-002]", "[BLOCKED DN-C1-003]"]) {
  if (allText.includes(marker)) fail(`resolved P0 marker remains: ${marker}`);
}

if (operations.size !== 11) fail(`expected 11 operations, found ${operations.size}`);
if (screens.size !== 8) fail(`expected 8 screens, found ${screens.size}`);
if (tasks.get("TASK-C1-001")?.status !== "DONE") fail("TASK-C1-001 must be DONE");

console.log(`C1 contract validation passed: ${operations.size} operations, ${screens.size} screens, ${rules.size} rules, ${acceptance.size} acceptance tests (${automatedCount} automated, ${gapCount} explicit gaps), ${traceLines.length} trace rows, ${tasks.size} tasks.`);
