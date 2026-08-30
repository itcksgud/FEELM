import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import { parse } from 'yaml';

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(scriptDirectory, '..');
const repository = path.resolve(root, '..', '..');
const requiredFiles = [
  'README.md',
  '01-business-rules.md',
  '02-sequence-and-data-contract.md',
  '03-batch-candidate-contract.md',
  'api/openapi.fragment.yaml',
  'testing/acceptance-tests.md',
  'tasks/implementation-backlog.yaml',
  'traceability/requirements.csv',
  'data/recommendation-exposure-schema.md',
];

const failures = [];
const check = (condition, message) => {
  if (!condition) failures.push(message);
};
const read = (relative) => fs.readFileSync(path.join(root, relative), 'utf8');
const ids = (text, expression) => new Set([...text.matchAll(expression)].map((match) => match[0]));
const splitRefs = (value) => value ? value.split('|').filter(Boolean) : [];

for (const relative of requiredFiles) {
  check(fs.existsSync(path.join(root, relative)), `missing required file: ${relative}`);
}

const readme = read('README.md');
const rulesText = read('01-business-rules.md');
const acceptanceText = read('testing/acceptance-tests.md') + '\n' + read('03-batch-candidate-contract.md');
const rules = ids(rulesText, /BR-C2-\d{3}/g);
const decisions = ids(rulesText, /DN-C2-\d{3}/g);
const acceptance = ids(acceptanceText, /AC-C2-\d{3}/g);
const tests = ids(acceptanceText, /TEST-[A-Z0-9-]+/g);

check(
  readme.includes('상태: `APPROVED_C2A_INTERNAL_POPULARITY_ONLY`'),
  'README must declare the approved C2A internal Popularity-only slice',
);
check(rules.size >= 30, 'business rules are unexpectedly incomplete');
check(acceptance.size >= 30, 'acceptance criteria are unexpectedly incomplete');
check(decisions.size >= 8, 'decision gates are unexpectedly incomplete');
check(rulesText.includes('ranking alpha는 정확히 `0.0`'), 'ranking alpha=0 lock is missing');
check(rulesText.includes('REC-EV-003B') && rulesText.includes('champion'), 'non-champion boundary is missing');

const openapi = parse(read('api/openapi.fragment.yaml'));
check(openapi?.openapi === '3.1.0', 'OpenAPI fragment must use 3.1.0');
check(openapi?.security?.[0]?.serviceAuth instanceof Array, 'global serviceAuth is required');
const operationIds = new Set();
for (const [pathName, pathItem] of Object.entries(openapi?.paths ?? {})) {
  for (const method of ['get', 'post', 'put', 'patch', 'delete']) {
    const operation = pathItem?.[method];
    if (!operation) continue;
    check(Boolean(operation.operationId), `${method.toUpperCase()} ${pathName} has no operationId`);
    check(!operationIds.has(operation.operationId), `duplicate operationId: ${operation.operationId}`);
    operationIds.add(operation.operationId);
  }
}
for (const required of [
  'rankRecommendationsInternal',
  'getRecommenderLiveness',
  'getRecommenderReadiness',
]) {
  check(operationIds.has(required), `missing operationId: ${required}`);
}
const snapshot = openapi.components?.schemas?.ServingSnapshot;
check(snapshot?.properties?.rankingAlpha?.const === 0, 'OpenAPI rankingAlpha must be const 0');
check(snapshot?.properties?.rankingPolicy?.const === 'BAYESIAN_POPULARITY_ONLY', 'Popularity policy const is missing');
check(openapi.components?.schemas?.RecommendationReason?.properties?.code?.enum?.length === 1, 'unvalidated reason codes were added');

const backlog = parse(read('tasks/implementation-backlog.yaml'));
check(
  backlog?.status === 'APPROVED_C2A_INTERNAL_POPULARITY_ONLY',
  'backlog must declare the approved C2A internal Popularity-only slice',
);
const taskList = backlog?.tasks ?? [];
const taskIds = new Set(taskList.map((task) => task.id));
const taskAcceptanceRefs = new Set();
const taskTestRefs = new Set();
check(taskIds.size === taskList.length, 'backlog task IDs must be unique');
for (const task of taskList) {
  for (const dependency of task.depends_on ?? []) {
    check(taskIds.has(dependency), `${task.id} has unknown dependency ${dependency}`);
  }
  for (const criterion of task.acceptance ?? []) {
    taskAcceptanceRefs.add(criterion);
    check(acceptance.has(criterion), `${task.id} references unknown acceptance ${criterion}`);
  }
  for (const test of task.tests ?? []) {
    taskTestRefs.add(test);
    check(tests.has(test), `${task.id} references unknown test ${test}`);
  }
  for (const gate of task.decision_gates ?? []) {
    check(decisions.has(gate), `${task.id} references unknown decision ${gate}`);
  }
}
for (const item of backlog?.deferred ?? []) {
  check(decisions.has(item.gate), `${item.id} references unknown deferred gate ${item.gate}`);
}

const visiting = new Set();
const visited = new Set();
const byId = new Map(taskList.map((task) => [task.id, task]));
const visit = (taskId) => {
  if (visiting.has(taskId)) {
    failures.push(`dependency cycle at ${taskId}`);
    return;
  }
  if (visited.has(taskId)) return;
  visiting.add(taskId);
  for (const dependency of byId.get(taskId)?.depends_on ?? []) visit(dependency);
  visiting.delete(taskId);
  visited.add(taskId);
};
for (const taskId of taskIds) visit(taskId);

const csvLines = read('traceability/requirements.csv').trim().split(/\r?\n/);
const expectedHeader = 'requirement_id,decision_gates,business_rules,operation_ids,data_contracts,acceptance_criteria,tasks,automated_tests,status';
check(csvLines[0] === expectedHeader, 'traceability header is invalid');
const requirementIds = new Set();
const tracedRules = new Set();
const tracedAcceptance = new Set();
const tracedTasks = new Set();
const tracedTests = new Set();
for (const [index, line] of csvLines.slice(1).entries()) {
  const columns = line.split(',');
  check(columns.length === 9, `traceability row ${index + 2} must have 9 columns`);
  if (columns.length !== 9) continue;
  const [requirement, gateRefs, ruleRefs, operationRefs, , acceptanceRefs, taskRefs, testRefs, status] = columns;
  check(!requirementIds.has(requirement), `duplicate requirement ${requirement}`);
  requirementIds.add(requirement);
  check(
    status.startsWith('DRAFT') || status.startsWith('APPROVED_C2A'),
    `${requirement} must be either C2A-approved or explicitly draft-blocked`,
  );
  for (const gate of splitRefs(gateRefs)) check(decisions.has(gate), `${requirement} has unknown decision ${gate}`);
  for (const rule of splitRefs(ruleRefs)) {
    tracedRules.add(rule);
    check(rules.has(rule), `${requirement} has unknown rule ${rule}`);
  }
  for (const operation of splitRefs(operationRefs)) check(operationIds.has(operation), `${requirement} has unknown operation ${operation}`);
  for (const criterion of splitRefs(acceptanceRefs)) {
    tracedAcceptance.add(criterion);
    check(acceptance.has(criterion), `${requirement} has unknown acceptance ${criterion}`);
  }
  for (const task of splitRefs(taskRefs)) {
    tracedTasks.add(task);
    check(taskIds.has(task), `${requirement} has unknown task ${task}`);
  }
  for (const test of splitRefs(testRefs)) {
    tracedTests.add(test);
    check(tests.has(test), `${requirement} has unknown test ${test}`);
  }
}
check(requirementIds.size >= 10, 'traceability requirements are unexpectedly incomplete');
for (const rule of rules) check(tracedRules.has(rule), `${rule} is not traced to a requirement`);
for (const criterion of acceptance) {
  check(taskAcceptanceRefs.has(criterion), `${criterion} is not assigned to a task`);
  check(tracedAcceptance.has(criterion), `${criterion} is not traced to a requirement`);
}
for (const task of taskIds) check(tracedTasks.has(task), `${task} is not traced to a requirement`);
for (const test of tests) {
  check(taskTestRefs.has(test), `${test} is not assigned to a task`);
  check(tracedTests.has(test), `${test} is not traced to a requirement`);
}

const mainOpenApi = fs.readFileSync(path.join(repository, 'docs', 'api', 'openapi.yaml'), 'utf8');
check(!mainOpenApi.includes('/internal/v1/recommendations/rank'), 'C2 path was merged into main OpenAPI before approval');

if (failures.length > 0) {
  for (const failure of failures) console.error(`FAIL ${failure}`);
  process.exit(1);
}

console.log(JSON.stringify({
  status: 'PASS',
  rules: rules.size,
  decisions: decisions.size,
  acceptanceCriteria: acceptance.size,
  tasks: taskIds.size,
  requirements: requirementIds.size,
  operationIds: [...operationIds].sort(),
}));
