# OpenCode Go Meter

OpenCode **Go 플랜** 사용량(5시간 / 주간 / 월간)을 시스템 트레이(메뉴 바)에서 보여주는 크로스 플랫폼(macOS / Windows) 앱입니다.

## 설치

Python 3.10+ 필요. 저장소 폴더에서:

- **macOS**: `setup.command` 더블클릭 (터미널에서는 `./setup.sh`)
- **Windows**: `setup.bat` 더블클릭 (python.org에서 Python 설치 시 "Add to PATH" 체크)
- **Linux**: `./setup.sh`

## 실행

설치가 끝나면 앱이 자동으로 시작됩니다. 이후 다시 실행할 때는:

- **macOS**: `run.command` 더블클릭 (터미널에서는 `./run.sh`)
- **Windows**: `run.bat` 더블클릭

실행 스크립트는 앱을 터미널에서 **분리(detach)** 해서 띄우므로 터미널 창을 닫아도 앱은 계속 동작합니다. 로그는 `~/.opencode-go-meter/app.log` (Windows: `%USERPROFILE%\.opencode-go-meter\app.log`). 터미널에 붙여서 디버그하려면 `.venv/bin/python -m go_meter` 로 직접 실행하세요.

실행하면 트레이(메뉴 바)에 아이콘이 생기고, 메뉴에서 기간별 사용량과 초기화까지 남은 시간을 확인할 수 있습니다.

```
5h:      $1.92 / $12 (16%) · resets in 3h 37m
Week:    $1.80 / $30 (6%)  · resets in 6d 8h
Month:   $3.60 / $60 (6%)  · resets in 23d 18h
잔액:    $16.13
```

마지막 줄은 OpenCode Zen 크레딧 잔액으로, 워크스페이스 홈 페이지에서 가져옵니다. 잔액 조회에 실패해도 사용량 표시에는 영향을 주지 않습니다. 툴팁에도 `Go 5h 16% | Week 6% | Month 6% | 잔액 $16.13` 형태로 표시됩니다.

남은 시간은 콘솔이 주는 `resetInSec` 기준이며, 재조회 없이도 1분마다 자동으로 카운트다운됩니다.

## 삭제

삭제 스크립트는 앱을 중지하고, 자동 시작 등록·데이터 폴더(`~/.opencode-go-meter`, 저장된 로그인 쿠키 포함)·`.venv` 를 제거합니다. (실행 전에 확인을 묻습니다.)

- **macOS**: `uninstall.command` 더블클릭 (터미널에서는 `./uninstall.sh`)
- **Windows**: `uninstall.bat` 더블클릭
- **Linux**: `./uninstall.sh`

스크립트가 지우는 것은 앱이 시스템에 만든 항목뿐입니다. 프로젝트 폴더 자체는 남으니, 완전히 지우려면 삭제 후 폴더를 직접 삭제하세요.

### 부팅 시 자동 시작

메뉴의 **Start at Login** 을 체크하면 로그인할 때 앱이 자동으로 실행됩니다.

- macOS: `~/Library/LaunchAgents/local.opencode-go-meter.plist` (LaunchAgent)
- Windows: `HKCU\...\CurrentVersion\Run` 레지스트리 (pythonw로 콘솔 창 없이 실행)
- 등록 정보에는 절대 경로가 들어가므로 프로젝트 폴더를 옮기면 다음 실행 때 자동으로 경로를 갱신합니다.
- 중복 실행은 잠금 파일로 차단됩니다 — 자동 시작된 상태에서 `run.command`를 또 실행해도 트레이 아이콘이 2개 생기지 않습니다.

## 로그인

앱은 세 가지 방법으로 opencode.ai 세션 쿠키를 얻습니다.

1. **자동 감지** — 실행 시 설치된 브라우저(Firefox → Chrome → Safari/Edge)에서 `auth` 쿠키를 자동으로 찾습니다. 브라우저에서 이미 https://opencode.ai/auth 에 로그인돼 있으면 그대로 붙습니다.
2. **Log in via Browser...** — 브라우저를 열어 로그인하는 동안 최대 3분간 쿠키를 폴링합니다.
3. **수동 입력** — 자동 감지가 안 될 때:
   - opencode.ai/auth 로그인 → DevTools(F12) → Application/Storage → Cookies → `https://opencode.ai` → `auth` 쿠키 **값** 복사
   - 메뉴의 **Paste Cookie from Clipboard** 클릭(복사해둔 값 사용) 또는 **Enter Cookie...** 로 직접 붙여넣기

### 플랫폼별 주의사항

| 환경 | 내용 |
|---|---|
| Windows + Chrome/Edge | Chrome 127+ 의 App-Bound Encryption 때문에 쿠키 자동 추출이 **불가능**합니다. Firefox를 쓰거나 수동 입력을 사용하세요. |
| macOS + Chrome | 최초 1회 키체인 접근("Chrome Safe Storage") 허용 창이 뜹니다. "항상 허용"을 눌러야 자동 추출이 됩니다. |
| macOS + Safari | 터미널(또는 실행 주체)에 **Full Disk Access** 권한이 있어야 쿠키를 읽을 수 있습니다. |
| Firefox | 쿠키 저장소가 암호화되지 않아 어느 OS에서든 가장 잘 동작합니다. |

## 설정

`~/.opencode-go-meter/config.json` (Windows: `%USERPROFILE%\.opencode-go-meter\config.json`)

```json
{
  "refresh_interval": 10,
  "limits": { "rolling": 12.0, "weekly": 30.0, "monthly": 60.0 }
}
```

- `limits` — 플랜 한도(달러). 콘솔은 사용률(%)만 제공하므로 메뉴의 달러 표시는 `% × limit` 로 계산합니다. 본인 플랜에 맞게 수정하세요.
- `refresh_interval` — 갱신 주기(분). 메뉴에서도 변경 가능.
- 세션 쿠키도 이 파일에 저장됩니다(권한 0600). 유출되면 계정 접근이 가능하니 공유하지 마세요.

## 문제 해결

- **로그**: `~/.opencode-go-meter/app.log`
- **"Could not read usage from console"**: 콘솔 페이지 구조가 바뀐 경우입니다. 파싱 실패 시 원본 HTML이 `~/.opencode-go-meter/last_fetch.html` 로 저장되므로 이슈 분석에 사용하세요.
- **네트워크 오류**: 세션은 유지되며 다음 주기에 자동 재시도합니다. 세션이 실제로 만료된 경우에만 로그아웃 처리됩니다.
- **진단 스크립트**: `./.venv/bin/python test_api.py` — 쿠키 추출 → 인증 확인 → 워크스페이스 탐색 → 사용량 조회를 단계별로 실행합니다.

## 테스트

```
.venv/bin/python tests/test_parser.py
```
