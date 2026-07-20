# Windows 트레이 아이콘 후속 작업

작성일: 2026-07-07 (macOS 환경에서 작성 — Windows 실기 미검증)

> **갱신 2026-07-07 (Windows 실기):** 아래 작업 1·2를 모두 적용했고, 실기에서
> 발견된 추가 이슈 2건(아이콘 크기·좌클릭 메뉴)도 함께 수정했다. 상세는 문서 맨 끝
> [적용 결과](#적용-결과-2026-07-07-windows-실기) 절 참고.

## 배경

macOS 메뉴바 아이콘을 Apple HIG(Menu Bar Extra)에 맞춰 재작업했다. 핵심은
[`go_meter/icon.py`](../go_meter/icon.py):

- 공식 OpenCode 로고([`go_meter/assets/opencode-logo.svg`](../go_meter/assets/opencode-logo.svg),
  출처 `https://opencode.ai/favicon.svg`) 기하를 사용.
- **모노크롬 템플릿**으로 렌더: 같은 잉크를 2단계 불투명도로 그려 원본의 2톤을 재현
  — 프레임(외곽−홀)은 alpha 255, 회색 인셋(홀 하단 2/3)은 부분 alpha
  `round(0x5A/0xFF*255)=90` (브랜드 회색:흰색 비율), 상단 슬롯은 투명.
- HIG 내부 여백을 위해 실루엣을 캔버스의 0.72로 축소 + 시각 중앙 정렬.
- macOS: `apply_macos_template()`가 pystray의 NSImage에 `setTemplate_(True)`를 찔러
  OS가 라이트/다크/반투명을 자동 재도색.

**이미 Windows에도 반영되는 부분:** `_render_logo()`는 플랫폼 중립이라 2톤 알파·여백·
모노크롬 실루엣이 그대로 RGBA 비트맵으로 나온다. Windows 트레이는 이 비트맵을
작업표시줄에 합성만 하므로 템플릿 개념 없이도 2톤 입체감은 그대로 보인다.
`apply_macos_template()`는 macOS 밖에서 no-op이고, `get_icon()`은 non-darwin에서
`_is_light_theme()`로 흑/백 잉크를 골라 렌더한다.

즉 **자동 재도색 흉내 부분**만 손보면 된다. 아래 2건.

---

## 작업 1 — (버그) 트레이용 테마 감지 키 교정 · **권장, 우선순위 높음**

### 문제
`_is_light_theme()`의 Windows 분기가 `AppsUseLightTheme`(= 앱 **창** 모드)를 읽는다.
그러나 트레이 아이콘은 **작업표시줄** 위에 있고, 작업표시줄 색은
`SystemUsesLightTheme`(= Windows 모드)를 따른다. 두 값은 서로 독립적이고,
Windows 기본값은 "앱=라이트, 작업표시줄=다크" 조합이다.

결과: 기본 라이트 모드 PC에서 현재 코드는 `AppsUseLightTheme=1`을 보고 **검정 잉크**를
고르는데, 실제 작업표시줄은 어두워서 **아이콘이 거의 안 보인다.**

### 위치
[`go_meter/icon.py`](../go_meter/icon.py) — `_is_light_theme()` 함수의 `win32` 분기.

### 변경 (Before → After)
```python
# Before
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return value == 1
```
```python
# After — 트레이는 작업표시줄 위에 있으므로 시스템(작업표시줄) 테마를 읽는다.
                # AppsUseLightTheme 은 앱 창 모드, SystemUsesLightTheme 이 작업표시줄/
                # Start 색을 결정한다. 트레이 아이콘의 배경은 작업표시줄이다.
                value, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
                return value == 1
```
- 키 경로(`...\Themes\Personalize`)는 동일하고, 두 값 모두 그 아래에 있으므로
  `QueryValueEx`의 값 이름만 바꾸면 된다.
- 값이 없는 구형 Windows에서는 기존 `except OSError` 폴백(dark 가정, 흰 잉크)이
  그대로 유효하다.

### 검증 (Windows)
1. 설정 > 개인 설정 > 색 > "모드 선택"을 **어둡게** → 아이콘이 **흰색** 실루엣으로
   또렷하게 보이는지.
2. **밝게** → **검정** 실루엣으로 보이는지.
3. "Windows 모드"와 "앱 모드"를 서로 다르게(예: 앱=라이트, Windows=다크) 설정해도
   **작업표시줄 색 기준**으로 올바른 잉크가 선택되는지 (이게 이번 수정의 핵심).
4. 두 톤(프레임 vs 인셋)이 구분되어 인셋이 더 흐리게 보이는지.

> 참고: 잉크색은 **아이콘 생성 시점(startup)** 에 한 번 고정된다. 실행 중 테마를
> 바꾸면 작업 2 없이는 재시작 전까지 갱신되지 않는다.

---

## 작업 2 — (개선) 실행 중 테마 변경 동적 반영 · **선택**

### 문제
macOS 템플릿은 실행 중 테마가 바뀌면 OS가 자동으로 따라오지만, Windows는 잉크색이
startup에 고정되어 라이브 전환을 못 따라온다(재시작 필요). 세션 중 테마 전환은 드물어
우선순위는 낮다.

### 접근 (스케치)
[`go_meter/tray.py`](../go_meter/tray.py)에 이미 도는 타이머(예: `_ui_tick`,
60초)를 활용해 가볍게 재렌더:

```python
# tray.py — 예시 스케치 (Windows에서 의미 있음; macOS는 템플릿이 알아서 처리)
self._icon_is_light = None  # __init__ 에 초기화

def _refresh_tray_icon_theme(self):
    """Windows: 작업표시줄 테마가 바뀌었으면 잉크를 다시 골라 아이콘 재렌더."""
    if sys.platform != "win32" or not self.tray_icon:
        return
    light = icon._is_light_theme()
    if light == self._icon_is_light:
        return
    self._icon_is_light = light
    self.tray_icon.icon = icon.get_icon()  # pystray Windows: 재할당으로 갱신
```
- 이 함수를 기존 UI 틱(`_ui_tick`)에서 호출하면 최대 60초 내 반영된다.
- 더 즉각적으로 하려면 `WM_SETTINGCHANGE`(문자열 "ImmersiveColorSet") 훅이 정석이나,
  pystray가 메시지 루프를 소유하므로 폴링이 훨씬 단순하고 충분하다.
- macOS는 `get_icon()`이 항상 검정 템플릿을 반환하고 재도색은 OS가 하므로 이 로직에서
  제외(위 `win32` 가드). 불필요한 재렌더 방지.

### 검증 (Windows)
앱 실행 중 설정에서 Windows 모드를 라이트↔다크로 토글 → 최대 60초 내 아이콘 잉크가
자동으로 바뀌는지.

---

## 제약 / 주의
- 이 문서의 코드는 macOS에서 작성되어 **Windows 실기 검증이 되지 않았다.** 정확성만
  보장. 실제 적용 후 위 검증 절차로 확인 필요.
- `_render_logo()`는 건드릴 필요 없다 — 2톤·여백·형상은 이미 플랫폼 공통으로 올바르다.
- 16px 다운스케일에서 얇은 프레임/작은 인셋이 뭉개질 수 있다. 필요하면 `_GLYPH_FRACTION`
  (현재 0.72)이나 `_INSET_ALPHA`(현재 90)를 Windows에서 미세 조정하는 것도 검토 가능.

---

## 적용 결과 (2026-07-07, Windows 실기)

Windows 환경으로 넘어와 실기에서 검토·수정 완료. 네 가지를 반영했다.

### 작업 1 — 테마 감지 키 교정 · **완료**
`icon.py`의 `_is_light_theme()` win32 분기를 `AppsUseLightTheme` →
`SystemUsesLightTheme`로 교체(트레이는 작업표시줄 위에 있으므로). 값이 없는 구형
Windows 폴백(dark 가정)은 그대로 유효.

### 작업 2 — 실행 중 테마 변경 동적 반영 · **완료**
`tray.py`에 win32 전용 테마 폴링 타이머 추가:
- `_schedule_theme_tick()`/`_theme_tick()`/`_refresh_icon_for_theme()` 신설,
  `THEME_TICK_SECONDS = 5`초 주기.
- 로그인 상태와 무관하게 동작해야 하므로(로그아웃 상태에서도 아이콘은 보임)
  `_ui_tick`이 아닌 **독립 타이머**로 구현. `_on_ready`에서 win32일 때 시작,
  `stop()`에서 취소. `_icon_is_light`로 실제 변경 시에만 재렌더.
- 테마가 바뀌면 `self.tray_icon.icon = icon.get_icon()` 재할당으로 갱신(pystray
  win32는 `.icon` setter가 `NIM_MODIFY`로 아이콘 교체).

### 작업 3 (추가) — 아이콘이 작게 보이는 문제 · **완료**
macOS HIG 여백용 `_GLYPH_FRACTION = 0.72`가 Windows 작업표시줄에선 과한 여백이라
글리프가 작아 보였다. `_GLYPH_FRACTION_TRAY = 0.92`를 신설해 비-darwin(`get_icon`)에서
사용. `_render_logo(ink, fraction=...)`로 파라미터화. 실측 결과 글리프가 캔버스의
72% → 92%를 채운다.

### 작업 4 (추가) — 좌클릭 시 메뉴가 안 뜨는 문제 · **완료**
pystray win32 백엔드(`_win32.py:_on_notify`)는 `WM_RBUTTONUP`에서만 메뉴를 띄우고
`WM_LBUTTONUP`은 기본 항목만 호출한다(여기선 기본 항목이 없어 무반응). 신설한
`go_meter/win_tray.py`의 `enable_left_click_menu()`가 아이콘의 `WM_NOTIFY` 핸들러를
감싸 좌·우 클릭 모두 메뉴를 띄운다(`TrackPopupMenuEx` 경로 재사용). pystray 내부에
의존하므로 best-effort(실패 시 기존 우클릭 전용 동작 유지). `_on_ready`에서 win32일
때 설치.

### 검증
- 임포트/렌더 스모크: `icon.get_icon()` 정상, 글리프 92% 채움, `_is_light_theme()`
  동작 확인. pystray win32 심볼(`WM_LBUTTONUP`, `TrackPopupMenuEx` 등) 존재 확인.
- 실기 UI 확인 항목: 좌클릭 메뉴 표시 / Windows 모드 라이트↔다크 토글 시 5초 내
  잉크 자동 전환 / 아이콘 크기.
