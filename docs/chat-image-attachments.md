# Funding Story 채팅 이미지 첨부 계약

AI 서비스에서 정한 계약이다. FE는 BE를 호출하고, BE가 이미지의 프로젝트 소유권과 실제 파일을
검증한 뒤 AI 요청을 구성한다. 대표·리워드 이미지에 사용하는 기존 `source_images` 처리와
레퍼런스 이미지 생성 경로를 재사용한다.

## 1. FE → BE: 메시지와 업로드된 이미지 URL

기존 프로젝트 이미지 업로드 API로 파일을 업로드한 뒤 메시지를 전송한다.

`POST /api/v1/ai/sessions/{session_id}/messages`

```json
{
  "message_id": "33333333-3333-4333-8333-333333333333",
  "revision": 2,
  "text": "이 제품 사진을 참고해 주세요.",
  "attachments": [
    {
      "file_url": "https://cdn.example.test/media/projects/22222222-2222-4222-8222-222222222222/story/11111111-1111-4111-8111-111111111111.png",
      "reward_id": null
    }
  ]
}
```

- `attachments`는 이번 메시지에 첨부한 이미지 목록이다. 생략하면 기존 텍스트 전송과 같다.
- 이미지가 있으면 `text`는 빈 문자열이거나 생략되어도 된다. 둘 다 없으면 거부한다.
- `reward_id`는 해당 프로젝트 리워드를 지정할 때만 사용하며, 일반 제품 사진은 `null`이다.
- FE는 `context`, 서명된 읽기 URL, MIME·크기·만료 시각을 직접 구성하지 않는다.
- 예시의 파일 URL은 기존 업로드 API가 반환한 값을 그대로 사용한다.

## 2. BE → AI: 검증된 이미지 참조

같은 메시지 endpoint에 다음 형식으로 전달한다.

```json
{
  "message_id": "33333333-3333-4333-8333-333333333333",
  "revision": 2,
  "text": "이 제품 사진을 참고해 주세요.",
  "attachments": [
    {
      "slot_id": "chat.11111111-1111-4111-8111-111111111111",
      "file_url": "https://cdn.example.test/media/projects/22222222-2222-4222-8222-222222222222/story/11111111-1111-4111-8111-111111111111.png",
      "reward_id": null,
      "read_url": "https://storage.example.test/media/projects/22222222-2222-4222-8222-222222222222/story/11111111-1111-4111-8111-111111111111.png?signature=example",
      "content_type": "image/png",
      "file_size": 12345,
      "expires_at": "2099-01-01T00:00:00Z"
    }
  ],
  "context": {
    "project": {
      "business_type": "SOLE",
      "category": {"major": "테크·가전", "minor": "생활가전"},
      "title": "무선 청소기",
      "goal_amount": 3000000
    },
    "rewards": [
      {
        "reward_id": 1,
        "name": "청소기 본품",
        "description": "본품 1대",
        "price": 39000,
        "is_limited": false,
        "quantity": null,
        "is_early_bird": false,
        "options": []
      }
    ],
    "source_images": []
  }
}
```

예시는 대표·리워드 이미지와 이전 첨부가 없는 세션의 첫 이미지 첨부 요청이다.
실제 요청에는 해당 프로젝트·리워드와 기존 이미지 참조를 넣는다.
AI의 스키마는 [OpenAPI](openapi.json)에 포함되어 있다.

| 필드 | 규칙 |
|---|---|
| `slot_id` | BE가 파일의 고정 ID로 `chat.{fileId}`를 구성. 같은 ID는 같은 파일·메타데이터에만 사용 |
| `file_url` | 업로드 API가 반환한 HTTPS 파일 URL. 서명·쿼리·인증정보를 포함하지 않음 |
| `read_url` · `expires_at` | BE가 S3 객체 확인 후 새로 발급하는 읽기 URL과 만료 시각 |
| `content_type` · `file_size` | 실제 객체 기준 JPEG·PNG·WebP, 파일당 최대 10 MiB |
| `context` | 최신 Core 정보 및 이전 첨부의 갱신된 읽기 URL. 하위 호환을 위해 선택 필드 |

BE는 `file_url`의 저장소·프로젝트 경로, 판매자 권한, 객체 존재·형식·크기를 검증한다.
이전에 첨부한 이미지는 세션 응답의 메시지 목록에서 조회할 수 있다. BE는 이후 메시지를
전달할 때 이 목록의 파일 URL을 다시 검증·서명하고 `context.source_images`에 포함한다.
이번 메시지의 새 이미지는 `attachments`에 넣는다. 기존 이미지가 만료되면 갱신 없이
접수하지 않는다.

## 3. AI 처리와 세션 응답

- 첨부를 TTL 세션에 보관하고 기존 이미지와 합쳐 대화 분석에 사용한다.
- 세션의 전체 참조 이미지 수는 대표·리워드·채팅 첨부를 합해 최대 30개다.
- 세션 조회의 사용자 메시지에는 첨부가 있을 때 다음 메타데이터를 추가한다.

```json
{
  "role": "user",
  "text": "이 제품 사진을 참고해 주세요.",
  "attachments": [
    {
      "slot_id": "chat.11111111-1111-4111-8111-111111111111",
      "file_url": "https://cdn.example.test/media/projects/22222222-2222-4222-8222-222222222222/story/11111111-1111-4111-8111-111111111111.png",
      "reward_id": null,
      "content_type": "image/png",
      "file_size": 12345
    }
  ]
}
```

서명된 `read_url`과 `expires_at`는 세션 응답에 노출하지 않는다. 첨부가 없는 기존 메시지는
기존 `{role, text}` 형식을 유지한다. BE는 첨부 정보를 FE에 전달해 미리보기·대화 복구에 사용한다.

같은 `message_id`의 재요청은 텍스트·revision·첨부 파일이 같으면 기존 chat을 반환한다.
읽기 URL의 서명과 만료 시각만 재발급한 경우도 같은 요청으로 처리한다. 다른 파일로
바꾸거나 첨부 목록을 바꾸면 `409`를 반환하므로 새 `message_id`를 사용한다.

## 4. 최종 생성: 첨부 누락과 URL 만료 방지

`POST /api/v1/ai/runs`의 기존 요청 형식을 유지한다. BE가 `context.source_images`를 만들 때
대표·리워드 이미지와 **세션에서 접수한 모든 채팅 첨부**를 함께 넣고 읽기 URL을 다시 발급한다.
`SourceImageRef`에는 `file_url`을 넣지 않는다.

- 채팅 첨부가 누락되거나 만료되면 `422`를 반환한다.
- 같은 첨부 ID의 파일·리워드·MIME·크기를 바꾸면 `409`를 반환한다.
- 아직 메시지로 접수하지 않은 `chat.*` 이미지를 생성 요청에 넣으면 `422`를 반환한다.
- 같은 생성 idempotency key로 서명만 바꿔 재요청하면 기존 run을 반환한다.
- 갱신된 이미지 바이트는 기존 레퍼런스 선택과 OpenAI `images.edit` 경로에 전달한다.
  기존 선택 규칙에 따라 생성 슬롯당 최대 16개를 사용하며 해당 리워드 이미지를 우선한다.

## 역할과 적용 순서

1. **AI:** 메시지 첨부 접수·세션 보관·갱신 검사·대화 분석·최종 생성 연결과 OpenAPI 제공.
2. **BE:** 위 계약의 공개/내부 DTO, 이미지 검증, 세션 조회 전달, 메시지·생성 시 URL 재발급 구현.
3. **FE:** 업로드·미리보기·첨부 전송·대화 복구 구현 후 버튼 활성화.

AI만 반영한 상태에서는 FE의 첨부 버튼이 활성화되지 않는다.
