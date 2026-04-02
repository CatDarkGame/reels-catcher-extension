🇰🇷 한국어 | [🇺🇸 English](README.en.md)

# reels-catcher-extension

Instagram DM으로 공유받은 릴스(Reels)를 자동으로 감지해 로컬 reels-catcher 파이프라인으로 전달하고, Notion 데이터베이스에 동기화하는 Chrome Extension + 로컬 서버.

<img width="424" height="666" alt="image" src="https://github.com/user-attachments/assets/be757d42-a3a5-4868-a97c-4d1730f84c72" />
<img width="2088" height="776" alt="image" src="https://github.com/user-attachments/assets/0c188c98-b5a7-407d-bda4-a004ff78cf9a" />

## 플랫폼 호환성

| 구성 요소 | macOS | Windows | Linux |
|---|---|---|---|
| Chrome Extension | ✅ | ✅ | ✅ |
| Python 서버 (`local_server.py`) | ✅ | ⚠️ 수동 설정 필요 | ⚠️ 수동 설정 필요 |
| `Start Server.command` (자동 실행) | ✅ | ❌ 미지원 | ❌ 미지원 |

> **현재 `Start Server.command` 및 설치 가이드는 macOS 기준으로 작성되어 있습니다.**  
> Windows/Linux에서는 Python 가상환경 활성화 및 서버 실행을 수동으로 진행해야 합니다.

## 동작 원리

Chrome Extension의 `chrome.debugger` API(Chrome DevTools Protocol)를 활용해 Instagram Web의 네트워크 트래픽을 passive하게 모니터링합니다.

```
Instagram Web (DM 수신)
  ↓ WebSocket / HTTP 응답 (CDP 모니터링)
background.js (릴스 URL 추출)
  ↓ POST /api/reels
local_server.py (중복 방지 + 시간 필터)
  ↓
reels-catcher 파이프라인
  ├── 다운로드 (yt-dlp)
  ├── 메타데이터 추출
  ├── AI 분류
  ├── Obsidian 노트 생성
  └── Notion 데이터베이스 동기화 (동영상 포함)
```

**특징:**
- Instagram 서버에 추가 요청을 만들지 않음 (passive 감지)
- 비공식 Private API 미사용 → 계정 정지 리스크 없음
- 실시간 감지 (새로고침 불필요)

## 구성 파일

```
reels-catcher-extension/
├── manifest.json          # Chrome Extension 설정 (MV3)
├── background.js          # Service Worker: CDP 이벤트 처리 + 서버 전송
├── content.js             # Content Script (보조)
├── page-interceptor.js    # Page Script (HTTP fetch 보조 감지)
├── popup/
│   ├── popup.html         # Extension 팝업 UI
│   └── popup.js           # 팝업 로직 (토글, 카운터, 서버 상태)
├── icons/                 # Extension 아이콘
├── local_server.py        # 로컬 API 서버 (reels-catcher + Notion 연동)
├── notion_writer.py       # Notion API v3 동기화 모듈
├── debug_server.py        # 개발용 테스트 서버 (수신 내용만 출력)
├── scripts/
│   └── backfill_notion.py # 기존 수집 데이터 Notion 백필 스크립트
└── Start Server.command   # 서버 실행 스크립트 (macOS 전용)
```

## 사전 요구사항

- [reels-catcher](https://github.com/CatDarkGame/reels-catcher) 설치 완료
- Python 3.11+
- Chrome 브라우저
- Instagram 계정으로 instagram.com 로그인 상태

## 설치 방법

### 1. 의존성 설치

reels-catcher의 가상환경에 추가 패키지를 설치합니다:

```bash
cd <reels-catcher 경로>
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install notion-client
```

### 2. 설정 파일 생성

```bash
mkdir -p ~/.local/share/reels-catcher-extension
```

`~/.local/share/reels-catcher-extension/config.json` 파일을 생성합니다:

```json
{
  "dataset_root": "/path/to/reels-catcher_output",
  "obsidian_vault": "/path/to/obsidian/vault",
  "reels_catcher_src": "/path/to/reels-catcher/src",
  "notion_api_key": "secret_...",
  "notion_db_id": "<Notion 데이터베이스 ID>"
}
```

| 키 | 설명 | 필수 |
|---|---|---|
| `dataset_root` | 릴스 다운로드 저장 경로 | ✅ |
| `obsidian_vault` | Obsidian vault 루트 경로 | ✅ |
| `reels_catcher_src` | reels-catcher Python 패키지 src 경로 | ✅ (레거시 통합 기간 중) |
| `notion_api_key` | Notion Integration API 키 | Notion 사용 시 |
| `notion_db_id` | Notion 데이터베이스 ID | Notion 사용 시 |

> **보안 주의:** `config.json`은 시크릿을 포함합니다. `.gitignore`에 등록되어 있으며 절대 커밋하지 마세요.  
> 파일 권한을 `600`으로 설정하는 것을 권장합니다: `chmod 600 config.json`

### 3. Notion 연동 설정

1. [Notion Integrations](https://www.notion.so/my-integrations)에서 Integration 생성
2. 대상 데이터베이스 → `...` → **Connections** → Integration 추가
3. 데이터베이스 URL에서 ID 추출: `notion.so/<workspace>/<DATABASE_ID>?v=...`
4. `config.json`에 `notion_api_key`와 `notion_db_id` 입력

> 데이터베이스 프로퍼티(컬럼)는 서버 최초 실행 시 자동으로 생성됩니다.

### 4. Chrome Extension 로드

1. `chrome://extensions` 접속
2. 우측 상단 **개발자 모드** 활성화
3. **압축해제된 확장 프로그램을 로드합니다** 클릭
4. 이 폴더(`reels-catcher-extension/`) 선택

### 5. 로컬 서버 실행

**macOS (권장):**
```bash
# Start Server.command 더블클릭
```

**수동 실행 (모든 OS):**
```bash
cd <reels-catcher 경로>
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python3 <reels-catcher-extension 경로>/local_server.py
```

서버 시작 시 출력:
```
🚀 reels-catcher 서버 시작: http://localhost:8000/api/reels
   서버 시작 시각: HH:MM:SS (이후 수신 DM만 처리)
   파이프라인: ✅ 활성
   Notion writer: ✅ 활성
```

## 사용 방법

1. **서버 시작** (`Start Server.command` 더블클릭 또는 수동 실행)
2. **instagram.com/direct** 접속
3. **Extension 팝업** 클릭 → "감시 중" + "연결됨" 확인
4. 다른 계정에서 릴스 링크 DM 전송
5. 서버 터미널에서 처리 확인:
   ```
   🎬 릴스 수신: https://www.instagram.com/reel/SHORTCODE/
   ⬇️  다운로드 시작: ...
   ✅ 처리 완료: Game Title → /path/to/note.md
   [notion] ✅ 생성: SHORTCODE
   ```

## Extension 팝업 상태

| 표시 | 의미 |
|---|---|
| 🟢 감시 중 + 연결됨 | 정상 동작 |
| 🟠 감시 중 + 서버 꺼짐 | 서버 실행 필요 |
| ⚫ 비활성 | 팝업에서 토글 OFF 상태 |

## 기존 데이터 Notion 백필

이미 수집된 데이터를 Notion에 일괄 업로드합니다:

```bash
# venv 활성화
source <venv 경로>/bin/activate          # Windows: <venv 경로>\Scripts\activate

# 항목 확인 (dry-run)
python3 <reels-catcher-extension 경로>/scripts/backfill_notion.py --dry-run

# 전체 업로드 (메타데이터 + 동영상)
python3 <reels-catcher-extension 경로>/scripts/backfill_notion.py

# 메타데이터만 (동영상 제외)
python3 <reels-catcher-extension 경로>/scripts/backfill_notion.py --no-video
```

## 참고 사항

- Instagram DM 탭 상단에 **"Chrome이 디버깅 중"** 배너가 표시됩니다 (CDP 사용으로 인한 정상 동작)
- 서버 시작 이전에 수신된 DM은 처리하지 않습니다
- 처리된 릴스 shortcode는 `~/.local/share/reels-catcher-extension/ext_seen.json`에 기록됩니다
- 포트 변경: `local_server.py --port 9000`
- Chrome을 업데이트하거나 Extension이 재로드되면 팝업 토글이 OFF로 초기화될 수 있습니다 → 팝업에서 다시 ON으로 변경 후 Instagram DM 탭 새로고침

## 개발용 테스트 서버

파이프라인 연동 없이 수신 내용만 터미널에 출력하는 테스트 서버:

```bash
python3 debug_server.py
```

## 라이선스

[MIT](LICENSE)
