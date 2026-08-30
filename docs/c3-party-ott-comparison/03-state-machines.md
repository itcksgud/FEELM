# C3 State Machines

> 상태: `APPROVED` — `LOCAL_MVP_ONLY`

## Party

```text
DRAFT(owner 1) --first invitation accept--> ACTIVE(2..4)
ACTIVE --later invitation accept---------> ACTIVE(up to 4)
```

local MVP에는 CLOSED나 member removal 전이가 없다. maximumMemberCount=4를 넘는 accept는 transaction을
변경하지 않고 409다.

## Invitation

```text
PENDING --recipient accept--> ACCEPTED
```

다른 terminal state는 local MVP에 없다. accept transaction은 Party capacity와 invitation ownership/revision을
다시 검증한다. same key/same body replay는 저장된 ACCEPTED response, same key/different body는 409다.

## OTT catalog comparison

```text
REQUESTED --COMPLETE fixture materialization--> READY (immutable)
REQUESTED --missing/incomplete materialization--> FAILED_RETRYABLE (partial rows 0)
```

refresh는 READY를 수정하지 않고 새 comparisonId를 만든다.

## Party baseline

```text
REQUESTED --Party owner/member + COMPLETE materialization--> READY_PAGE
REQUESTED --private actor-------------------------------> hidden 404
REQUESTED --missing materialization---------------------> 503
```

별도 model compute/cache 상태는 없다. immutable materialization과 고정 정렬에서 page를 읽는다.

## Production-only blocked states

OAuth/JWT actor, DECLINED/CANCELLED/EXPIRED/CLOSED, Party taste analysis, personalized recommendation compute,
event projection은 local MVP 상태 기계에 넣지 않는다.

