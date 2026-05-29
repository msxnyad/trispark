import sys
import time
import threading
import json
import os

# ── 센서/카메라 (라즈베리파이에서만 동작) ──
try:
    from sensor_camera import sensor_mgr, camera_mgr, start_all, start_camera, stop_camera, cleanup as sensor_cleanup
    SENSOR_AVAILABLE = True
except Exception as e:
    print(f"[센서] 로드 실패 (PC 개발환경): {e}")
    SENSOR_AVAILABLE = False
from PySide6.QtGui import QGuiApplication, QFontDatabase, QImage
from PySide6.QtQml import QQmlApplicationEngine, QQmlImageProviderBase
from PySide6.QtCore import QTimer, QObject, Signal, Property, Slot
from PySide6.QtQuick import QQuickImageProvider

# Flask 서버 (server.py에서 독립 실행 가능)
try:
    from server import start_server
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    print("server.py 로드 실패 — flask flask-cors 설치 확인")

SHARED_DATA_FILE = "shared_data.json"

# ── GitHub 자동 동기화 설정 ──
GITHUB_TOKEN = ""  # 사용 안 함 — 브라우저에서 직접 동기화
GITHUB_OWNER = "msxnyad"
GITHUB_REPO  = "trispark"
GITHUB_FILE  = "shared_data.json"

def push_to_github():
    """shared_data.json을 GitHub에 자동 업로드 (백그라운드)"""
    import threading
    def _push():
        try:
            import urllib.request, base64
            url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GITHUB_FILE}"
            headers = {
                "Authorization": f"token {GITHUB_TOKEN}",
                "Content-Type": "application/json"
            }
            # 현재 파일 SHA 가져오기
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req) as r:
                    sha = json.loads(r.read())["sha"]
            except:
                sha = None
            # 파일 내용 읽기
            if not os.path.exists(SHARED_DATA_FILE):
                return
            with open(SHARED_DATA_FILE, "r", encoding="utf-8") as f:
                content = f.read()
            encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
            body = json.dumps({
                "message": "auto sync",
                "content": encoded,
                **({"sha": sha} if sha else {})
            }).encode("utf-8")
            req2 = urllib.request.Request(url, data=body, headers=headers, method="PUT")
            with urllib.request.urlopen(req2) as r:
                print(f"[GitHub] 동기화 완료")
        except Exception as e:
            print(f"[GitHub] 동기화 실패: {e}")
    threading.Thread(target=_push, daemon=True).start()

def load_shared_data():
    if os.path.exists(SHARED_DATA_FILE):
        with open(SHARED_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_shared_data(data):
    with open(SHARED_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_active_user():
    """현재 활성 유저 ID 반환"""
    return load_shared_data().get("activeUser", "")

def get_user_data(user_id):
    """특정 유저의 데이터 반환"""
    if not user_id:
        return {}
    shared = load_shared_data()
    return shared.get("users", {}).get(user_id, {})

def save_user_data(user_id, user_data):
    """특정 유저의 데이터 저장"""
    if not user_id:
        return
    shared = load_shared_data()
    if "users" not in shared:
        shared["users"] = {}
    shared["users"][user_id] = user_data
    save_shared_data(shared)


class StudyCube(QObject):
    timeChanged       = Signal()
    tasksChanged      = Signal()
    studyTimeChanged  = Signal()
    postureChanged    = Signal()
    clockFormatChanged= Signal()
    pomodoroChanged   = Signal()
    memoWordChanged   = Signal()
    themeChanged      = Signal()
    currentTaskChanged= Signal()
    weekDataChanged      = Signal()
    immersionChanged     = Signal()
    postureScoreChanged  = Signal()
    qrChanged            = Signal()
    postureStatsChanged  = Signal()
    # ── 센서/카메라 ──
    sensorChanged          = Signal()
    modeChanged            = Signal()
    postureAlertChanged      = Signal()
    postureCandidateChanged  = Signal()
    liveSessionScoreChanged  = Signal()

    def __init__(self):
        super().__init__()
        self._current_time        = ""
        self._tasks               = []
        self._completed_tasks     = set()
        self._has_base_posture    = self._load_base_posture()
        self._clock_format        = self._load_clock_format()
        self._theme               = self._load_theme()
        self._qr_path             = ""
        self._posture_stats       = {}

        # 현재 활성 유저
        self._active_user         = get_active_user()

        # 유저별 공부시간 로드
        self._total_study_seconds = self._load_study_time()
        self._immersion_score     = 0
        self._posture_score       = 0
        self._live_session_score  = 0
        self._live_smooth_score   = None
        self._live_score_date     = ""

        # 유저 데이터 로드
        user_data = get_user_data(self._active_user)
        self._daily_posture_accum = user_data.get("dailyPostureAccum", {})
        self._pomodoro_seconds    = user_data.get("pomodoroSeconds", 25 * 60)
        self._memo_words          = user_data.get("memoWords", [])
        self._memo_index          = 0
        import random
        self._current_memo_word   = random.choice(self._memo_words) if self._memo_words else "영어 단어 넣기"
        self._dday_label          = user_data.get("ddayLabel", "")
        self._dday_count          = user_data.get("ddayCount", "")
        self._tasks               = user_data.get("urgentTasks", [])
        self._completed_tasks     = set(user_data.get("completedTasks", []))

        # _check_daily_reset에서 참조하는 속성 미리 초기화
        self._focus_start_time            = None
        self._session_start_study_seconds = 0

        # 새벽 3시 기준 날짜 초기화 체크
        self._check_daily_reset()

        # 시계 타이머
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_time)
        self.timer.start(1000)
        self.update_time()

        # 암기 단어 5분마다 교체
        self.memo_timer = QTimer()
        self.memo_timer.timeout.connect(self.rotate_memo_word)
        self.memo_timer.start(5 * 60 * 1000)

        # shared_data.json 감지 (2초마다)
        self.sync_timer = QTimer()
        self.sync_timer.timeout.connect(self._check_shared_data)
        self.sync_timer.start(2000)
        self._last_shared_mtime = 0

        # ── 모드 상태 ──
        self._mode = "daily"          # "daily" | "focus" | "rest"
        self._in_rest = False         # 휴식 화면 표시 중 → 데이터 업데이트 차단
        self._rest_entry_time   = None  # 현재 휴식 진입 시각
        self._total_rest_seconds = 0.0  # 이번 세션 누적 휴식 시간 (초)
        self._posture_alert     = ""  # 현재 자세 경고 (5초 후)
        self._posture_candidate = ""  # 조건 충족 즉시
        self._temp = None
        self._humidity = None

        # ── 모드 전환 타이머 ──
        self._focus_candidate_start = None   # 집중모드 진입 후보 시작
        self._absent_start          = None   # 자리 비움 시작
        self._absent_popup_start    = None   # 자리비움 팝업 시작
        self._away_popup_active     = False  # QML 자리비움 팝업 표시 중 여부
        self._was_absent            = True   # 처음엔 자리비움으로 간주
        self._focus_cooldown        = 0.0    # 집중모드 종료 시각
        self._popup_cancelled       = False  # 사용자가 팝업 취소함
        self._cancel_absent_start   = None   # 취소 후 자리비움 시작 시각
        self._focus_session_started = False  # "시작하기" 눌러야 True (팝업만 뜬 상태와 구분)
        self._daily_screen_active   = True   # 리포트/설정 화면 진입 시 False
        self._away_streak           = 0      # 연속 away 판정 횟수 (노이즈 필터)
        self._present_streak        = 0      # 연속 present 판정 횟수
        
        # ── 집중모드 추적용 ──
        self._focus_start_time      = None   # 집중모드 시작 시각
        self._last_study_time_added = 0      # 마지막 추가된 공부시간 (중복 방지)
        self._session_goal_committed = False  # 현재 세션 목표 시간이 이미 totalPomodoroGoalSec에 합산됐는지

        # 센서 폴링 타이머 (1초마다)
        if SENSOR_AVAILABLE:
            self.sensor_timer = QTimer()
            self.sensor_timer.timeout.connect(self._check_sensors)
            self.sensor_timer.start(1000)

        # 카메라 프레임 타이머 (100ms = 10fps)
        self.frame_timer = QTimer()
        self.frame_timer.timeout.connect(self._update_frame)
        self.frame_timer.start(100)

    # ── 시계 ──
    def update_time(self):
        if self._clock_format == "12":
            new_time = time.strftime("%I:%M %p")
        else:
            new_time = time.strftime("%H:%M")
        if self._current_time != new_time:
            self._current_time = new_time
            self.timeChanged.emit()
            # 매 분마다 새벽 3시 초기화 체크 (분이 바뀔 때만)
            # 매 분마다 날짜 변경 체크 (시간 동기화 후 빠르게 감지)
            self._check_daily_reset()
            # 비센서 모드: 30초마다 주별/월별 라이브 업데이트
            if not SENSOR_AVAILABLE:
                sec = time.localtime().tm_sec
                if sec == 0 or sec == 30:
                    self._live_update_weekly_monthly_nosensor()

    @Property(str, notify=timeChanged)
    def currentTime(self):
        return self._current_time

    # ── 뽀모도로 ──
    @Property(int, notify=pomodoroChanged)
    def pomodoroSeconds(self):
        return self._pomodoro_seconds

    # ── 암기 단어 ──
    @Property(str, notify=memoWordChanged)
    def memoWord(self):
        return self._current_memo_word

    def rotate_memo_word(self):
        if not self._memo_words:
            return
        self._memo_index = (self._memo_index + 1) % len(self._memo_words)
        self._current_memo_word = self._memo_words[self._memo_index]
        self.memoWordChanged.emit()

    # ── 디데이 ──
    @Property(str, notify=pomodoroChanged)
    def ddayLabel(self):
        return self._dday_label

    @Property(str, notify=pomodoroChanged)
    def ddayCount(self):
        return self._dday_count

    # ── shared_data 감지 ──
    def _check_shared_data(self):
        if not os.path.exists(SHARED_DATA_FILE):
            return
        mtime = os.path.getmtime(SHARED_DATA_FILE)
        if mtime == self._last_shared_mtime:
            return
        self._last_shared_mtime = mtime
        shared = load_shared_data()

        # ── 활성 유저 변경 감지 ──
        new_user = shared.get("activeUser", "")
        user_switched = (new_user != self._active_user)
        if user_switched:
            self._active_user = new_user
            self._total_study_seconds = self._load_study_time()
            self._check_daily_reset()
            new_user_data = get_user_data(new_user)
            self._daily_posture_accum = new_user_data.get("dailyPostureAccum", {})
            self.studyTimeChanged.emit()
            self.weekDataChanged.emit()

        # 현재 유저 데이터 읽기
        user_data = shared.get("users", {}).get(self._active_user, {}) if self._active_user else {}

        # 뽀모도로 시간 업데이트
        new_pomo = user_data.get("pomodoroSeconds", self._pomodoro_seconds)
        if new_pomo != self._pomodoro_seconds:
            self._pomodoro_seconds = new_pomo
            self.pomodoroChanged.emit()

        # 암기 단어 업데이트
        new_words = user_data.get("memoWords", self._memo_words)
        if new_words != self._memo_words:
            self._memo_words = new_words
            self._memo_index = 0
            import random
            self._current_memo_word = random.choice(new_words) if new_words else "영어 단어 넣기"
            self.memoWordChanged.emit()

        # 긴급+중요 할일 업데이트
        new_tasks = user_data.get("urgentTasks", self._tasks)
        new_completed = set(user_data.get("completedTasks", []))
        tasks_changed = new_tasks != self._tasks
        completed_changed = new_completed != self._completed_tasks

        if tasks_changed:
            self._tasks = new_tasks
            self.tasksChanged.emit()
        if tasks_changed or completed_changed or user_switched:
            self._completed_tasks = new_completed
            self.currentTaskChanged.emit()

        # 몰입 점수 업데이트
        new_immersion = user_data.get("immersionScore", self._immersion_score)
        if new_immersion != self._immersion_score:
            self._immersion_score = new_immersion
            self.immersionChanged.emit()

        # 자세 점수 업데이트
        new_posture_score = user_data.get("postureScore", self._posture_score)
        if new_posture_score != self._posture_score:
            self._posture_score = new_posture_score
            self.postureScoreChanged.emit()

        # 디데이 업데이트
        self._dday_label = user_data.get("ddayLabel", self._dday_label)
        self._dday_count = user_data.get("ddayCount", self._dday_count)
        self.pomodoroChanged.emit()

    # ── 기존 기능 유지 ──
    @Property('QVariantList', notify=tasksChanged)
    def tasks(self):
        return self._tasks

    @Property('QVariantList', notify=currentTaskChanged)
    def completedTasksList(self):
        return list(self._completed_tasks)

    @Property(str, notify=currentTaskChanged)
    def currentTask(self):
        for task in self._tasks:
            if task not in self._completed_tasks:
                return task
        return ""

    @Slot(str)
    def toggleTask(self, task):
        if task in self._completed_tasks:
            self._completed_tasks.discard(task)
        else:
            self._completed_tasks.add(task)
        self._save_completed_tasks()
        self.currentTaskChanged.emit()

    def _save_completed_tasks(self):
        if not self._active_user:
            return
        user_data = get_user_data(self._active_user)
        user_data["completedTasks"] = list(self._completed_tasks)
        save_user_data(self._active_user, user_data)

    @Slot()
    def checkCurrentTask(self):
        task = self.currentTask
        if task:
            self._completed_tasks.add(task)
            self._save_completed_tasks()
            self.currentTaskChanged.emit()

    @Property(bool, notify=postureChanged)
    def hasBasePosture(self):
        return self._has_base_posture

    @Slot()
    def saveBasePosture(self):
        self._has_base_posture = True
        self._save_base_posture()
        self.postureChanged.emit()

    @Slot()
    def resetBasePosture(self):
        self._has_base_posture = False
        self._save_base_posture()
        self.postureChanged.emit()
        if SENSOR_AVAILABLE:
            camera_mgr.analyzer.reset_reference()

    # ── 센서 폴링 ──
    def _check_sensors(self):
            if not SENSOR_AVAILABLE:
                return
            state = sensor_mgr.get_state()
            now   = time.time()

            # 온습도 업데이트
            if state["temp"] is not None:
                self._temp     = state["temp"]
                self._humidity = state["humidity"]
                
                # ★ 추가: shared_data에 저장
                if self._active_user:
                    user_data = get_user_data(self._active_user)
                    user_data["temp"] = self._temp
                    user_data["humidity"] = self._humidity
                    save_user_data(self._active_user, user_data)
                
                self.sensorChanged.emit()

            # ── 모드별 로직 ──
            if self._mode == "daily":
                self._handle_daily_mode(state, now)
            elif self._mode == "focus":
                self._handle_focus_mode(state, now)

    @Slot(bool)
    def setDailyActive(self, active: bool):
        """리포트/설정 화면 진입·복귀 시 QML에서 호출"""
        if active and not self._daily_screen_active:
            # 일상모드 화면으로 돌아올 때 후보 타이머 리셋 → 즉시 팝업 안 뜨게
            self._focus_candidate_start = None
        self._daily_screen_active = active

    def _handle_daily_mode(self, state, now):
        """데일리 모드: 집중모드 진입 조건 감지
        - 리포트/설정 화면 중엔 판단 중단 (_daily_screen_active=False)
        - 자리비움 후 복귀했을 때만 팝업
        - 집중모드 끝나고 바로는 팝업 안 뜸 (_was_absent=False)
        - 팝업 취소 후 조건 유지 중엔 팝업 안 뜸 (_popup_cancelled=True)
        - 취소 후 5초 이상 자리비움 되면 팝업 재활성화
        """
        if not self._daily_screen_active:
            return  # 다른 화면 보는 중 → 조건 판단 안 함

        user_present = sensor_mgr.should_focus()

        if user_present:
            self._cancel_absent_start = None  # 자리 있으면 취소-자리비움 타이머 리셋
            if self._popup_cancelled:
                # 취소 상태에서 계속 앉아있음 → 팝업 안 띄움
                pass
            elif self._was_absent:
                # 자리비움 후 복귀 → 10초 타이머 시작
                if self._focus_candidate_start is None:
                    self._focus_candidate_start = now
                    print(f"[팝업] 집중모드 후보 시작 (pir={state['pir']} dist={state['distance']})")
                elif now - self._focus_candidate_start >= 10:
                    self._focus_candidate_start = None
                    self._was_absent = False
                    self._mode = "focus"  # QML이 focus로 인식하게
                    print(f"[팝업] 집중모드 팝업 emit! mode={self._mode}")
                    self.modeChanged.emit()
            # 계속 앉아있음 (_was_absent=False, _popup_cancelled=False) → 아무것도 안 함
        else:
            if self._popup_cancelled:
                # 팝업 취소 후 자리 비움 → 5초 타이머
                if self._cancel_absent_start is None:
                    self._cancel_absent_start = now
                elif now - self._cancel_absent_start >= 10:
                    # 10초 이상 자리비움 → 취소 상태 해제, 다음 복귀 시 팝업 뜸
                    print(f"[팝업] 취소 후 10초 자리비움 → 팝업 재활성화")
                    self._popup_cancelled = False
                    self._cancel_absent_start = None
                    self._was_absent = True
                    self._focus_candidate_start = None
            else:
                # 자리비움 감지 → present→absent 전환 시에만 타이머 리셋
                if not self._was_absent:
                    print(f"[팝업] 자리비움 감지 (pir={state['pir']} dist={state['distance']})")
                    self._focus_candidate_start = None
                self._was_absent = True

    def _handle_focus_mode(self, state, now):
        """집중모드: 자리 비움 감지 (실제 세션 시작 후에만 동작)"""
        if not self._focus_session_started:
            return  # 팝업만 뜬 상태 → 자리비움 감지 안 함

        pir      = state.get("pir")
        distance = state.get("distance")

        # distance가 None이면 센서 미응답 → streak 변경 없이 건너뜀
        if distance is None:
            if not hasattr(self, '_none_dist_streak'):
                self._none_dist_streak = 0
            self._none_dist_streak += 1
            # 5초마다 경고 로그
            if int(now) % 5 == 0:
                print(f"[집중모드 센서] PIR={pir}, 거리=None({self._none_dist_streak}회 연속), away_streak={self._away_streak}, 자리비움경과={round(now - self._absent_start, 1) if self._absent_start else '-'}s")
            return
        else:
            self._none_dist_streak = 0

        # ── 연속 판정으로 노이즈 필터링 (1초 폴링 기준) ──
        # 카메라에서 얼굴이 감지되는 동안은 초음파 away 판정 무시
        # (기대기 자세 등으로 거리가 멀어져도 오판 방지)
        face_visible = SENSOR_AVAILABLE and (
            "absent" not in (camera_mgr.latest_posture or [])
        )
        # 3회 연속 away → 자리비움 확정 / 2회 연속 present → 복귀 확정
        if distance > 80 and not face_visible:
            self._away_streak    += 1
            self._present_streak  = 0
        else:
            self._present_streak += 1
            self._away_streak     = 0

        confirmed_away    = self._away_streak    >= 3
        confirmed_present = self._present_streak >= 2

        # 5초마다 센서 상태 출력
        if int(now) % 5 == 0:
            print(f"[집중모드 센서] PIR={pir}, 거리={distance}cm, away_streak={self._away_streak}, 자리비움경과={round(now - self._absent_start, 1) if self._absent_start else '-'}s")

        if confirmed_away:
            if self._absent_start is None:
                self._absent_start = now
                print(f"[자리비움] 확정 (3연속 >80cm, PIR={pir}, 거리={distance}cm)")
            else:
                elapsed = now - self._absent_start
                if elapsed >= 10 and self._absent_popup_start is None:
                    self._absent_popup_start = now
                    print(f"[자리비움] 10초 경과 → 팝업 emit (PIR={pir}, 거리={distance}cm)")
                    self.modeChanged.emit()
                # 백업 자동복귀 (QML 타이머가 정상 동작하면 여기까지 안 옴)
                if elapsed >= 120:
                    print(f"[자리비움] 2분 경과 → 강제 일상모드 복귀")
                    self._go_to_daily()
        elif confirmed_present:
            # 확실히 앉아있을 때만 타이머 리셋
            if self._absent_start is not None:
                if self._away_popup_active:
                    # 팝업이 떠있는 중: 사용자가 버튼 누르기 전까지 자동 해제 안 함
                    self._absent_start = None  # 재이탈 시 타이머 새로 시작하도록 리셋만
                else:
                    print(f"[자리비움] 조건 해제 (PIR={pir}, 거리={distance}cm)")
                    self._absent_start       = None
                    self._absent_popup_start = None
                    self.modeChanged.emit()

        # 자세 통계 → shared_data 저장 (30초마다, 집중모드 + 비휴식 중에만)
        # _focus_start_time=None이면 세션 없음 → 저장 건너뜀 (종료 후 점수 덮어쓰기 방지)
        if SENSOR_AVAILABLE and self._focus_start_time and not self._in_rest and int(now) % 30 == 0:
            self._save_posture_stats()

    def _go_to_daily(self, skip_posture_stats=False):
        """데일리 모드로 복귀
        skip_posture_stats=True: exitFocusMode에서 이미 올바른 값을 저장한 경우
        → _save_posture_stats()의 재계산이 user_data를 덮어쓰지 않도록 방지
        """
        import time as _time
        self._mode               = "daily"
        self._absent_start       = None
        self._absent_popup_start = None
        self._was_absent         = False  # 집중모드 끝난 직후엔 팝업 안 뜨게
        self._focus_cooldown     = _time.time()  # 집중모드 종료 시각 기록
        self._focus_candidate_start = None
        self._focus_start_time   = None  # 다음 세션에서 오래된 시작시간 사용 방지
        self._session_goal_committed = False  # 세션 종료 시 초기화
        self._popup_cancelled       = False  # 집중모드 정상 종료 → 취소 상태 초기화
        self._cancel_absent_start   = None
        self._focus_session_started = False  # 세션 종료
        self._away_streak           = 0
        self._present_streak        = 0
        stop_camera()
        if not skip_posture_stats:
            self._save_posture_stats()  # 비정상 종료(센서 자리비움 등)에서만 재계산 필요
        # 세션 종료 시 실시간 세그먼트 데이터 반드시 초기화
        if self._active_user:
            _ud = get_user_data(self._active_user)
            _ud["currentSessionSegments"] = {}
            _ud["currentSessionHourly"]   = {}
            _ud["sessionActive"]          = False
            save_user_data(self._active_user, _ud)
        self.modeChanged.emit()

    def _live_update_weekly_monthly_nosensor(self):
        """비센서 모드: 집중 중 30초마다 일별 데이터 실시간 업데이트
        주별/월별은 세션 종료(exitFocusMode) 시에만 업데이트"""
        if not self._active_user or not self._focus_start_time:
            return
        if self._in_rest:  # 휴식 중 → 데이터 업데이트 차단
            return
        session_elapsed = time.time() - self._focus_start_time - self._total_rest_seconds
        live_total = getattr(self, '_session_start_study_seconds', self._total_study_seconds) + session_elapsed
        user_data  = get_user_data(self._active_user)
        # 일일 누적 목표 기준 시간 점수 — 화면 표시 timeScore와 동일한 계산식
        prev_goal  = user_data.get("totalPomodoroGoalSec", 0)
        total_goal = prev_goal + (self._pomodoro_seconds if self._pomodoro_seconds > 0 else 1500)
        time_score = min(100, round(live_total / total_goal * 100)) if total_goal > 0 else 0
        immersion  = round((time_score + self._posture_score) / 2)
        # 일별 데이터만 실시간 업데이트 (주별/월별은 세션 종료 시에만)
        user_data["totalStudySec"]   = round(live_total)
        user_data["timeScore"]       = round(time_score)
        user_data["immersionScore"]  = immersion
        user_data["pomodoroSeconds"] = self._pomodoro_seconds
        user_data["sessionActive"]   = True
        # 현재 세션 시간대별 분포 (비센서 모드 실시간 업데이트)
        current_time = time.time()
        default_score = int(user_data.get("postureScore", 0))
        user_data["currentSessionHourly"]   = self._simple_hourly(self._focus_start_time, current_time, default_score)
        user_data["currentSessionSegments"] = self._simple_segments(self._focus_start_time, current_time)
        save_user_data(self._active_user, user_data)

    def _save_posture_stats(self):
        """자세 통계 shared_data에 저장 (30초마다 + 세션 종료 시 호출)"""
        if not SENSOR_AVAILABLE:
            print("[자세점수] SENSOR_AVAILABLE=False → 저장 건너뜀")
            return
        if not self._active_user:
            print("[자세점수] active_user 없음 → 저장 건너뜀")
            return

        import time
        import math as _math

        # ★ 타임스탬프·open_records를 먼저 고정 → 세션 점수와 시간대별 점수가 동일한 기준 사용
        current_time = time.time()
        _open      = camera_mgr.analyzer._open_records.copy() if self._focus_start_time else {}
        _eff_start = (camera_mgr.analyzer._analysis_start_time or self._focus_start_time) if self._focus_start_time else None

        # 세션 자세 점수: generate_hourly_data와 완전히 동일한 방식 (타임스탬프 공유)
        _session_ps = 0.0
        if _eff_start:
            _session_ps = camera_mgr.analyzer.compute_session_score(
                _eff_start, current_time, open_records=_open
            )

        # 이전 세션 누적과 합산
        _accum_total = self._daily_posture_accum.get("totalSec", 0)
        _accum_good  = self._daily_posture_accum.get("goodSec", 0)
        stats = camera_mgr.get_stats()
        stats = self._merge_posture_counts(stats)
        total_sec = stats.get("totalSec", 0)
        if total_sec > 0:
            if _accum_total <= 0:
                # 단일 세션: compute_session_score 결과 그대로 → 시간대별 점수와 정확히 일치
                stats["postureRatio"] = _session_ps
            else:
                # 멀티 세션: 현재(기록 기반) + 이전(카운터 기반) 가중 평균
                _cur_study = camera_mgr.analyzer._study_seconds(_eff_start, current_time) if _eff_start else 0
                _cur_good  = _session_ps / 100.0 * _cur_study
                _combined  = _cur_study + _accum_total
                stats["postureRatio"] = _math.trunc((_cur_good + _accum_good) / _combined * 1000) / 10 if _combined > 0 else 0.0

        user_data = get_user_data(self._active_user)
        user_data["postureStats"]    = stats
        user_data["pomodoroSeconds"] = self._pomodoro_seconds

        posture_score = stats.get("postureRatio", 0)
        # 현재 세션 진행 중이면 실시간 시간 점수 계산 (이전 세션값 오염 방지)
        if self._focus_start_time and self._pomodoro_seconds > 0:
            session_elapsed = current_time - self._focus_start_time - self._total_rest_seconds
            live_total = getattr(self, '_session_start_study_seconds', self._total_study_seconds) + session_elapsed
            # 일일 누적 목표 기준 시간 점수 — 화면 표시 timeScore와 동일한 계산식
            prev_goal  = user_data.get("totalPomodoroGoalSec", 0)
            total_goal = prev_goal + self._pomodoro_seconds   # 현재 세션 목표 포함
            time_score = min(100, round(live_total / total_goal * 100)) if total_goal > 0 else 0
        else:
            live_total = self._total_study_seconds
            time_score = user_data.get("timeScore", 0)
        immersion_score = (time_score + posture_score) / 2

        user_data["totalStudySec"]   = round(live_total)
        user_data["postureScore"]    = posture_score
        user_data["timeScore"]       = round(time_score)
        user_data["immersionScore"]  = round(immersion_score)
        user_data["sessionActive"]   = bool(self._focus_start_time)
        print(f"[자세점수] sessionScore={_session_ps}, postureRatio={posture_score}, timeScore={time_score}, immersion={immersion_score:.3f} → {int(immersion_score+0.5)}")

        # 현재 진행 중인 세션의 시간대별 분포 (실시간 업데이트용)
        if self._focus_start_time:
            session_hourly = camera_mgr.analyzer.generate_hourly_data(
                _eff_start, current_time, open_records=_open  # ★ 동일한 타임스탬프 사용
            )
            user_data["currentSessionHourly"] = session_hourly
            session_segs = camera_mgr.analyzer.generate_hourly_segments(
                _eff_start, current_time, open_records=_open
            )
            user_data["currentSessionSegments"] = session_segs

        print(f"[자세] score={posture_score}, time_score={time_score}, immersion={immersion_score:.1f}")
        # 주별/월별은 세션 종료(exitFocusMode) 시에만 업데이트 — 여기선 일별 데이터만 저장

        save_user_data(self._active_user, user_data)
        self._immersion_score = round(immersion_score)
        self.immersionChanged.emit()
        self._posture_score = round(posture_score, 2)   # float 유지 (정수 변환 금지)
        self.postureScoreChanged.emit()
        self._posture_stats = stats
        self.postureStatsChanged.emit()

    # ── 모드 전환 Slot (QML에서 호출) ──
    @Slot()
    def enterFocusMode(self):
        """집중모드 진입 (자세 설정 화면에서도 호출됨)"""
        print(f"[모드] enterFocusMode 호출 → _focus_session_started=True, 카메라 시작")

        # ★ 연속 집중 세션 감지: 휴식 후 재시작 시 이전 세션 데이터를 주별/월별에 저장
        # exitFocusMode()를 거치지 않고 바로 enterFocusMode()가 다시 호출되는 경우 (휴식→재시작)
        prev_session_studied = self._total_study_seconds - getattr(self, '_session_start_study_seconds', 0)
        if (self._focus_start_time is not None           # 이전 세션이 아직 살아있고
                and prev_session_studied > 0              # 실제로 공부한 시간이 있고
                and not self._session_goal_committed      # 목표 시간이 아직 미합산
                and self._active_user):
            import time as _time_module
            _now = _time_module.time()
            user_data = get_user_data(self._active_user)
            today = self._get_date_key()
            if user_data.get("lastSessionDate", "") != today:
                user_data["totalPomodoroGoalSec"] = 0
                user_data["dailySessionScores"]   = []
                user_data["lastSessionDate"]      = today
            prev_goal = self._pomodoro_seconds
            user_data["totalPomodoroGoalSec"] = user_data.get("totalPomodoroGoalSec", 0) + prev_goal
            self._session_goal_committed = True
            print(f"[연속세션] 이전 목표 {prev_goal}s 누적 → 총 {user_data['totalPomodoroGoalSec']}s")

            # ★ 주별/월별 데이터 업데이트 (휴식 → 다시 시작 경로에서 exitFocusMode 대신 수행)
            target_sec    = self._pomodoro_seconds if self._pomodoro_seconds > 0 else 1500
            session_score = min(100, round(prev_session_studied / target_sec * 100))
            if "dailySessionScores" not in user_data:
                user_data["dailySessionScores"] = []
            user_data["dailySessionScores"].append(session_score)
            user_data["totalStudySec"] = self._total_study_seconds
            _total_goal = user_data["totalPomodoroGoalSec"]
            user_data["timeScore"] = min(100, round(self._total_study_seconds / _total_goal * 100)) if _total_goal > 0 else 0

            # 시간대별 분포 계산
            try:
                if SENSOR_AVAILABLE:
                    _eff_s = camera_mgr.analyzer._analysis_start_time or self._focus_start_time
                    _session_hourly = camera_mgr.analyzer.generate_hourly_data(_eff_s, _now, open_records={})
                    user_data["hourlyData"] = self._merge_hourly(user_data.get("hourlyData", {}), _session_hourly)
                    _session_segs = camera_mgr.analyzer.generate_hourly_segments(_eff_s, _now, open_records={})
                    user_data["hourlySegments"] = self._merge_segments(user_data.get("hourlySegments", {}), _session_segs)
                else:
                    _eff_s = self._focus_start_time
                    _session_hourly = self._simple_hourly(_eff_s, _now, round(self._posture_score, 1))
                    user_data["hourlyData"] = self._merge_hourly(user_data.get("hourlyData", {}), _session_hourly)
                    _session_segs = self._simple_segments(_eff_s, _now)
                    user_data["hourlySegments"] = self._merge_segments(user_data.get("hourlySegments", {}), _session_segs)
                user_data["currentSessionHourly"]  = {}
                user_data["currentSessionSegments"] = {}
            except Exception as _e:
                print(f"[연속세션] hourlyData 계산 실패: {_e}")

            # 자세 통계 누적 후 카메라 초기화 (다음 세션 준비)
            _ps_for_wm = self._posture_score
            _posture_counts_for_wm = None
            if SENSOR_AVAILABLE:
                _session_stats = camera_mgr.get_stats()
                for _k in self._POSTURE_COUNT_KEYS + self._POSTURE_SEC_KEYS:
                    self._daily_posture_accum[_k] = self._daily_posture_accum.get(_k, 0) + _session_stats.get(_k, 0)
                user_data["dailyPostureAccum"] = self._daily_posture_accum
                _posture_counts_for_wm = self._daily_posture_accum
                # 기록 기반 자세 점수 (시간대별과 동일한 방식, reset 전에 계산)
                _ps_for_wm = camera_mgr.analyzer.compute_session_score(_eff_s, _now)
                camera_mgr.analyzer.reset_stats()  # 다음 세션 준비 (기록 초기화)

            user_data["postureScore"]   = _ps_for_wm
            user_data["immersionScore"] = round((user_data["timeScore"] + _ps_for_wm) / 2)
            self._immersion_score       = user_data["immersionScore"]
            self._update_weekly_data(
                user_data, session_score,
                immersion_score=self._immersion_score,
                posture_score=_ps_for_wm,
                posture_counts=_posture_counts_for_wm,
            )
            self._update_monthly_data(
                user_data, today,
                immersion_score=self._immersion_score,
                posture_score=_ps_for_wm,
                posture_counts=_posture_counts_for_wm,
            )
            save_user_data(self._active_user, user_data)
            print(f"[연속세션] 주별/월별 업데이트 완료: session_score={session_score}, posture={_ps_for_wm}")

        self._mode = "focus"
        self._focus_start_time = time.time()
        self._session_start_study_seconds = self._total_study_seconds  # QML 타이머 기준 elapsed 계산용
        self._last_study_time_added = 0  # 초기화
        self._rest_entry_time    = None  # 새 세션 → 휴식 추적 초기화
        self._total_rest_seconds = 0.0   # 새 세션 → 누적 휴식 시간 초기화
        self._focus_session_started = True  # 실제 세션 시작
        self._session_goal_committed = False  # 새 세션 시작 → 아직 미합산
        self.modeChanged.emit()
        if SENSOR_AVAILABLE:
            camera_mgr._frozen_info  = None  # 이전 고정값 초기화
            camera_mgr._frozen_frame = None
            start_camera()
            # 기준 자세 미설정이거나 재설정 중이면 setup_mode
            camera_mgr.setup_mode = not self._has_base_posture
            if not self._has_base_posture:
                self.postureChanged.emit()
            # 저장된 기준 자세 수치 복원
            if self._has_base_posture and self._active_user:
                _bpv = get_user_data(self._active_user).get("basePostureValues", {})
                if _bpv:
                    _a = camera_mgr.analyzer
                    _a.ref_ear            = _bpv.get("ear")
                    _a.ref_tilt_angle     = _bpv.get("tiltAngle", 0.0)
                    _a.ref_shoulder_width = _bpv.get("shoulderWidth")
                    _a.ref_nose_diff      = _bpv.get("noseDiff")
                    _a.ref_shoulder_y     = _bpv.get("shoulderY")
                    print(f"[기준자세] 복원: ear={_a.ref_ear}, tilt={_a.ref_tilt_angle:.1f}, w={_a.ref_shoulder_width}, nose={_a.ref_nose_diff}")
        # 웹앱에 즉시 세션 시작 반영 (30초 타이머 기다리지 않음)
        if self._active_user:
            _ud = get_user_data(self._active_user)
            _ud["sessionActive"]   = True
            _ud["pomodoroSeconds"] = self._pomodoro_seconds
            save_user_data(self._active_user, _ud)

    @Slot()
    def freezeSetupFrame(self):
        """카운트다운 0 시점에 프레임·HUD 값 고정"""
        if SENSOR_AVAILABLE:
            camera_mgr.freeze_setup_hud()
            print("[기준자세] 프레임 고정 완료")

    @Slot()
    def enterSetupMode(self):
        """자세 재설정 화면 진입 (설정에서 호출)"""
        if SENSOR_AVAILABLE:
            camera_mgr._frozen_info  = None  # 이전 고정값 초기화
            camera_mgr._frozen_frame = None
            start_camera()
            camera_mgr.setup_mode = True
            print("[카메라] 자세 설정 모드 시작")

    def _simple_hourly(self, start_time, end_time, default_score=0):
        """센서 없을 때: 시간 범위를 시간대별로 분배 (로컬 기준)"""
        result = {}
        current = start_time
        while current < end_time:
            lt = time.localtime(current)
            hour = lt.tm_hour
            hour_start_ts = current - (lt.tm_min * 60 + lt.tm_sec)
            next_hour_ts  = hour_start_ts + 3600
            seg_end = min(end_time, next_hour_ts)
            secs = round(seg_end - current)
            mins = round(secs / 60, 1)
            if mins > 0:
                key = str(hour)
                if key in result:
                    result[key]["mins"] = round(result[key]["mins"] + mins, 1)
                    result[key]["secs"] = result[key].get("secs", 0) + secs
                else:
                    result[key] = {"mins": mins, "secs": secs, "score": default_score}
            current = next_hour_ts
        return result

    def _simple_segments(self, start_time, end_time):
        """센서 없을 때: 5분 슬롯을 1분 단위로 분할, 세션 범위 내 분은 'g'로 마킹"""
        import datetime as _dt
        result = {}
        current = start_time
        while current < end_time:
            dt            = _dt.datetime.fromtimestamp(current)
            hour          = dt.hour
            slot_idx      = dt.minute // 5
            hour_start_ts = _dt.datetime(dt.year, dt.month, dt.day, hour, 0, 0).timestamp()
            slot_start_ts = hour_start_ts + slot_idx * 300
            next_slot_ts  = hour_start_ts + (slot_idx + 1) * 300
            key = str(hour)
            if key not in result:
                result[key] = [['n', 'n', 'n', 'n', 'n'] for _ in range(12)]
            minute_colors = list(result[key][slot_idx])
            for m in range(5):
                min_start    = slot_start_ts + m * 60
                min_end      = slot_start_ts + (m + 1) * 60
                actual_start = max(min_start, start_time)
                actual_end   = min(min_end, end_time)
                if actual_end >= actual_start + 30:  # 30초 이상 겹칠 때만 색칠
                    minute_colors[m] = 'g'
            result[key][slot_idx] = minute_colors
            current = next_slot_ts
        return result

    def _merge_segments(self, existing, new_session):
        """기존 hourlySegments에 새 세션 데이터 합산 (분 단위 병합 지원)"""
        def is_per_minute(s):
            return isinstance(s, list) and len(s) == 5 and isinstance(s[0], str) and len(s[0]) == 1

        def seg_has_data(s):
            if is_per_minute(s):
                return any(c != 'n' for c in s)
            return (s[0] if isinstance(s, list) else s) != 'n'

        merged = {k: [list(s) if isinstance(s, list) else s for s in v]
                  for k, v in existing.items()}
        for hk, segs in new_session.items():
            if hk not in merged:
                merged[hk] = [['n', 'n', 'n', 'n', 'n'] for _ in range(12)]
            for i, c in enumerate(segs):
                if not seg_has_data(c):
                    continue
                ex = merged[hk][i]
                if is_per_minute(c) and is_per_minute(ex):
                    # 분 단위 병합: 새 데이터 우선, 없으면 기존 유지
                    merged[hk][i] = [c[m] if c[m] != 'n' else ex[m] for m in range(5)]
                else:
                    merged[hk][i] = c
        return merged

    def _merge_hourly(self, existing, new_session):
        """기존 hourlyData에 새 세션 데이터를 시간 가중 평균으로 합산"""
        merged = dict(existing)
        for hk, hv in new_session.items():
            new_mins  = hv.get("mins", 0)
            new_secs  = hv.get("secs", round(new_mins * 60))
            new_score = hv.get("score", 0)
            if hk in merged:
                old = merged[hk]
                old_mins  = old.get("mins", 0)
                old_secs  = old.get("secs", round(old_mins * 60))
                total_secs = old_secs + new_secs
                total_mins = round(total_secs / 60, 1)
                avg_score = ((old.get("score", 0) * old_mins + new_score * new_mins) / (old_mins + new_mins)
                             if (old_mins + new_mins) > 0 else 0)
                merged[hk] = {"mins": total_mins, "secs": total_secs, "score": round(avg_score, 1)}
            else:
                merged[hk] = {"mins": new_mins, "secs": new_secs, "score": new_score}
        return merged

    @Slot()
    def exitFocusMode(self):
        """집중모드 종료 → 시간 점수 계산 및 저장"""
        if not self._active_user or not self._focus_start_time:
            self._go_to_daily()
            return

        # ★ 시간 점수 계산 (QML 타이머 경과시간 우선 → 벽시계 폴백)
        import time as time_module
        end_time       = time_module.time()
        qml_elapsed    = self._total_study_seconds - getattr(self, "_session_start_study_seconds", 0)
        # 벽시계 폴백 시 휴식 시간 제외 (restScreen 닫히면 _total_rest_seconds에 이미 합산됨)
        actual_seconds = qml_elapsed if qml_elapsed > 0 else max(0, end_time - self._focus_start_time - self._total_rest_seconds)
        target_seconds = self._pomodoro_seconds if self._pomodoro_seconds > 0 else 1500
        session_score  = min(100, round((actual_seconds / target_seconds) * 100))

        print(f"[시간 점수] 설정={target_seconds}초, 실제={actual_seconds}초(QML={qml_elapsed}), 점수={session_score}")

        # ★ shared_data에 저장
        user_data = get_user_data(self._active_user)

        # dailySessionScores 초기화 (매일 새로 시작)
        today     = self._get_date_key()
        last_date = user_data.get("lastSessionDate", "")

        if last_date != today:
            user_data["dailySessionScores"]  = []
            user_data["lastSessionDate"]     = today
            user_data["totalPomodoroGoalSec"] = 0

        # 세션 점수 기록 (참고용)
        if "dailySessionScores" not in user_data:
            user_data["dailySessionScores"] = []
        user_data["dailySessionScores"].append(session_score)

        # 누적 목표 시간 (세션별 설정한 뽀모도로 시간 합산)
        # ★ 연속 세션: enterFocusMode에서 이미 합산된 경우 중복 방지
        if not self._session_goal_committed:
            user_data["totalPomodoroGoalSec"] = user_data.get("totalPomodoroGoalSec", 0) + target_seconds
        self._session_goal_committed = False  # 종료 후 초기화
        user_data["totalStudySec"]        = self._total_study_seconds

        # 시간 점수: 누적 공부시간 / 누적 목표시간 × 100 (JS 화면과 동일한 계산식, 반올림)
        total_goal = user_data["totalPomodoroGoalSec"]
        user_data["timeScore"] = min(100, round(self._total_study_seconds / total_goal * 100)) if total_goal > 0 else 0

        # ★ 카메라 스레드를 먼저 중지해 posture_stats 동시 접근(race condition) 방지
        # (자세 점수·몰입 점수는 자세 통계 누적 완료 후 아래에서 최종 계산)
        if SENSOR_AVAILABLE:
            stop_camera()  # 스레드 join 후 반환 → 이후 posture_stats 단독 접근 보장
            # 아직 닫히지 않은 열린 기록을 end_time 기준으로 수동 종료
            _a = camera_mgr.analyzer
            for _p, _rec_s in list(_a._open_records.items()):
                _dur = round(end_time - _rec_s, 1)
                if _dur >= 1:
                    _a.posture_stats[_p]["records"].append({"start": _rec_s, "duration": _dur})
            _a._open_records    = {}
            _a._active_postures = frozenset()   # 모든 자세 종료 반영
            print(f"[종료] 카메라 정지 및 열린 기록 수동 종료 완료")

        # ★ 시간대별 공부시간 분포 계산 (집중 시작~종료 시간 기준)
        # try-except로 감싸서 그래프 계산 실패 시에도 timeScore는 반드시 저장
        _eff_start = self._focus_start_time  # 기본값 (try 실패해도 접근 가능하도록)
        _end_for_hourly = _eff_start + actual_seconds
        _final_session_ps = self._posture_score  # 기본값
        try:
            _eff_start = (camera_mgr.analyzer._analysis_start_time or self._focus_start_time) if SENSOR_AVAILABLE else self._focus_start_time
            # ★ QML 측정 시간 기준으로 종료 시각 계산
            # → 벽시계(end_time)는 "학습종료 버튼 누르는 시간"까지 포함되어 totalStudySec보다 길어짐
            # → _eff_start + actual_seconds 로 QML 타이머와 동일한 기준 맞춤
            _end_for_hourly = _eff_start + actual_seconds
            if SENSOR_AVAILABLE:
                # open_records는 이미 수동 종료됨 → 별도 전달 불필요
                session_hourly = camera_mgr.analyzer.generate_hourly_data(
                    _eff_start, _end_for_hourly
                )
                # 세션 전체 자세 점수 — generate_hourly_data와 동일한 타임스탬프로 계산
                _final_session_ps = camera_mgr.analyzer.compute_session_score(
                    _eff_start, _end_for_hourly
                )
                # 시간대별 점수는 generate_hourly_data가 계산한 per-hour 값을 그대로 사용
                # (세션 전체 평균으로 덮어쓰면 꺾은선이 평평해져 실제 자세 반영 안 됨)
            else:
                _session_ps = round(self._posture_score, 1)  # 센서 없을 때 기본 점수
                session_hourly = self._simple_hourly(
                    _eff_start, _end_for_hourly, _session_ps
                )
            existing_hourly = user_data.get("hourlyData", {})
            user_data["hourlyData"]            = self._merge_hourly(existing_hourly, session_hourly)
            user_data["currentSessionHourly"]  = {}   # 세션 종료 → 실시간 데이터 초기화

            # ★ 5분 단위 자세 세그먼트 계산
            if SENSOR_AVAILABLE:
                session_segs = camera_mgr.analyzer.generate_hourly_segments(
                    _eff_start, _end_for_hourly
                )
            else:
                session_segs = self._simple_segments(_eff_start, _end_for_hourly)
            existing_segs = user_data.get("hourlySegments", {})
            user_data["hourlySegments"]           = self._merge_segments(existing_segs, session_segs)
            user_data["currentSessionSegments"]   = {}   # 세션 종료 → 초기화

            print(f"[시간대] _eff_start={_eff_start:.0f}, end={end_time:.0f}, "
                  f"delta={end_time-_eff_start:.0f}s")
            for _hk in sorted(session_hourly.keys()):
                _hv = session_hourly[_hk]
                print(f"  [{_hk}시] mins={_hv['mins']:.1f}, score={_hv['score']}")
        except Exception as e:
            print(f"[경고] 시간대 데이터 계산 실패 (timeScore는 정상 저장됨): {e}")
            import traceback; traceback.print_exc()

        scores = user_data["dailySessionScores"]
        avg_score = sum(scores) / len(scores) if scores else 0
        print(f"[시간 점수] 세션 점수={session_score}, 평균={int(avg_score)}, 총 세션={len(scores)}")

        # 세션 종료 시 자세 횟수 + 시간 누적 업데이트
        _prev_accum_total = self._daily_posture_accum.get("totalSec", 0)
        _prev_accum_good  = self._daily_posture_accum.get("goodSec", 0)
        if SENSOR_AVAILABLE:
            session_stats = camera_mgr.get_stats()
            for k in self._POSTURE_COUNT_KEYS + self._POSTURE_SEC_KEYS:
                self._daily_posture_accum[k] = self._daily_posture_accum.get(k, 0) + session_stats.get(k, 0)
            user_data["dailyPostureAccum"] = self._daily_posture_accum
            # 누적 후 초기화 → _save_posture_stats가 0을 받아 accum+0 = 정확한 누적값
            camera_mgr.analyzer.reset_stats()

        # ★ 최종 자세 점수: compute_session_score 기반 (generate_hourly_data와 동일한 타임스탬프)
        import math as _math_final
        _accum_total = self._daily_posture_accum.get("totalSec", 0)
        if SENSOR_AVAILABLE and _accum_total > 0:
            _sess_study = camera_mgr.analyzer._study_seconds(_eff_start, _end_for_hourly)
            if _prev_accum_total <= 0:
                # 단일 세션: compute_session_score 결과 그대로 → 시간대별과 정확히 일치
                _ps_for_wm = _final_session_ps
            else:
                # 멀티 세션: 현재(기록 기반) + 이전(카운터 기반) 가중 평균
                _sess_good = _final_session_ps / 100.0 * _sess_study
                _combined  = _sess_study + _prev_accum_total
                _ps_for_wm = _math_final.trunc((_sess_good + _prev_accum_good) / _combined * 1000) / 10 if _combined > 0 else 0.0
            # 일별 postureStats도 30초 스냅샷이 아닌 정확한 종료값으로 덮어쓰기
            _final_stats = dict(self._daily_posture_accum)
            _final_stats["postureRatio"] = _ps_for_wm
            user_data["postureStats"] = _final_stats
            user_data["postureScore"] = _ps_for_wm
            self._posture_score       = _ps_for_wm
            self.postureScoreChanged.emit()
        else:
            _ps_for_wm = self._posture_score

        # ★ 몰입 점수: 최종 자세 점수 기준 (일별/주별/월별 모두 동일한 값)
        final_immersion = round((user_data["timeScore"] + _ps_for_wm) / 2)
        user_data["immersionScore"] = final_immersion
        self._immersion_score       = final_immersion
        self.immersionChanged.emit()

        # ★ 주별 데이터 업데이트 (자세 누적 후 호출해야 posture_counts 정확)
        self._update_weekly_data(
            user_data, session_score,
            immersion_score=final_immersion,
            posture_score=_ps_for_wm,
            posture_counts=self._daily_posture_accum if SENSOR_AVAILABLE else None,
        )
        # ★ 월별 데이터 업데이트
        self._update_monthly_data(
            user_data, self._get_date_key(),
            immersion_score=final_immersion,
            posture_score=_ps_for_wm,
            posture_counts=self._daily_posture_accum if SENSOR_AVAILABLE else None,
        )

        user_data["sessionActive"] = False  # ★ 저장 전 즉시 False → JS 라이브 타이머 시작 방지
        save_user_data(self._active_user, user_data)  # 항상 실행 (그래프 계산 실패해도)
        try:
            push_to_github()  # GitHub에 자동 동기화
        except Exception as e:
            print(f"[GitHub] 동기화 실패: {e}")
        finally:
            # exitFocusMode에서 이미 정확한 값 저장 → _save_posture_stats 재계산 건너뜀
            self._go_to_daily(skip_posture_stats=True)
            print(f"[모드] 집중모드 종료 → 데일리, 쿨다운 시작")

    @Slot(bool)
    def setRestMode(self, in_rest: bool):
        """휴식 화면 진입·복귀 시 QML에서 호출 → 휴식 중 데이터 업데이트 차단"""
        self._in_rest = in_rest
        if in_rest:
            self._rest_entry_time = time.time()  # 휴식 진입 시각 기록
            # ★ 자세 통계 일시 정지 → 휴식 구간이 세그먼트에 색 칠해지지 않도록
            if SENSOR_AVAILABLE:
                now = time.time()
                a = camera_mgr.analyzer
                a.stats_paused = True
                # 현재 열린 기록을 지금 시각 기준으로 닫기
                for p, start in list(a._open_records.items()):
                    duration = round(now - start, 1)
                    if duration >= 1:
                        a.posture_stats[p]["records"].append({"start": start, "duration": duration})
                a._open_records = {}
        elif self._rest_entry_time is not None:
            now = time.time()
            rest_start = self._rest_entry_time
            rest_duration = now - rest_start
            self._total_rest_seconds += rest_duration  # 누적 휴식 시간에 합산
            self._rest_entry_time = None
            print(f"[휴식모드] 복귀 → 이번 휴식 {rest_duration:.1f}초, 세션 누적 휴식 {self._total_rest_seconds:.1f}초 제외")
            # ★ 자세 통계 재개 → 휴식 이후 집중 구간부터 다시 기록
            if SENSOR_AVAILABLE:
                a = camera_mgr.analyzer
                # 휴식 구간을 기록 → 세그먼트에서 빈칸('n')으로 처리됨
                a.add_rest_interval(rest_start, now)
                a.stats_paused = False
                a._last_stats_time = None  # 휴식 공백이 통계에 포함되지 않도록 리셋
                # _analysis_start_time은 이미 설정돼 있으므로 건드리지 않음
        print(f"[휴식모드] {'진입' if in_rest else '복귀'} → 자세 추적 {'일시정지' if in_rest else '재개'}")

    @Slot()
    def cancelFocusPopup(self):
        """집중모드 팝업 취소 → 데일리 (카메라/통계 저장 없이)
        취소 후 조건 유지 중엔 팝업 안 뜸. 5초 이상 자리비움 후 다시 복귀해야 팝업.
        """
        self._mode = "daily"
        self._popup_cancelled       = True   # 취소 상태: 조건 유지돼도 팝업 안 뜸
        self._cancel_absent_start   = None
        self._focus_candidate_start = None
        self._focus_session_started = False  # 세션 시작 안 했으므로 False 유지
        print(f"[모드] 집중모드 팝업 취소 → 10초 이상 자리비움 후 재활성화")

    def _update_frame(self):
        """카메라 프레임을 FrameProvider로 전달 + 자세 실시간 업데이트"""
        if not SENSOR_AVAILABLE:
            return
        if camera_mgr._running and camera_mgr.latest_frame is not None:
            frame_provider.update_frame(camera_mgr.latest_frame)
            # 5초 후 확정 자세 (복수 자세 전부 뱃지로 표시)
            postures = camera_mgr.latest_posture  # list
            posture_str = ",".join(postures) if isinstance(postures, list) else postures
            if posture_str != self._posture_alert:
                print(f"[자세 변경] {self._posture_alert} → {posture_str}")
                self._posture_alert = posture_str
                self.postureAlertChanged.emit()
            # 조건 충족 즉시 candidate
            candidates = camera_mgr.latest_posture_candidate  # list
            candidate_str = ",".join(candidates) if isinstance(candidates, list) else candidates
            if candidate_str != self._posture_candidate:
                self._posture_candidate = candidate_str
                self.postureCandidateChanged.emit()

    @Slot()
    def captureBasePosture(self):
        """기준 자세 캡처 (QML 카운트다운 후 호출) — 백그라운드 스레드로 실행"""
        if not SENSOR_AVAILABLE:
            self.saveBasePosture()
            return

        def _do_capture():
            ok = False
            for attempt in range(8):
                ok = camera_mgr.capture_reference()
                if ok:
                    break
                print(f"[기준자세] 캡처 실패 (시도 {attempt+1}/8), 0.3초 후 재시도")
                time.sleep(0.3)

            if ok:
                self.saveBasePosture()
                camera_mgr.setup_mode = False
                if self._active_user:
                    _a = camera_mgr.analyzer
                    _ud = get_user_data(self._active_user)
                    _ud["basePostureValues"] = {
                        "ear":           _a.ref_ear,
                        "tiltAngle":     round(_a.ref_tilt_angle, 3),
                        "shoulderWidth": round(_a.ref_shoulder_width, 1) if _a.ref_shoulder_width is not None else None,
                        "noseDiff":      round(_a.ref_nose_diff, 1) if _a.ref_nose_diff is not None else None,
                        "shoulderY":     round(_a.ref_shoulder_y, 1) if _a.ref_shoulder_y is not None else None,
                    }
                    save_user_data(self._active_user, _ud)
                    print(f"[기준자세] 저장 완료: ear={_a.ref_ear}, tilt={_a.ref_tilt_angle:.1f}, w={_a.ref_shoulder_width}, nose={_a.ref_nose_diff}")
            else:
                print("[기준자세] 캡처 8회 모두 실패 — 카메라/포즈 감지 확인 필요")

        threading.Thread(target=_do_capture, daemon=True).start()

    # ── Property ──
    @Property(str, notify=modeChanged)
    def currentMode(self):
        return self._mode

    @Property(str, notify=postureAlertChanged)
    def postureAlert(self):
        return self._posture_alert

    @Property(str, notify=postureCandidateChanged)
    def postureCandidate(self):
        return self._posture_candidate

    @Property(float, notify=sensorChanged)
    def temperature(self):
        return self._temp if self._temp is not None else 0.0

    @Property(float, notify=sensorChanged)
    def humidity(self):
        return self._humidity if self._humidity is not None else 0.0

    @Property(bool, notify=modeChanged)
    def isAbsent(self):
        """자리 비움 팝업 표시 여부"""
        return self._absent_popup_start is not None

    def _load_base_posture(self):
        if os.path.exists("settings.json"):
            with open("settings.json", "r") as f:
                return json.load(f).get("has_base_posture", False)
        return False

    def _save_base_posture(self):
        data = {}
        if os.path.exists("settings.json"):
            with open("settings.json", "r") as f:
                data = json.load(f)
        data["has_base_posture"] = self._has_base_posture
        with open("settings.json", "w") as f:
            json.dump(data, f)

    def _load_clock_format(self):
        if os.path.exists("settings.json"):
            with open("settings.json", "r") as f:
                return json.load(f).get("clock_format", "24")
        return "24"

    def _save_clock_format(self):
        data = {}
        if os.path.exists("settings.json"):
            with open("settings.json", "r") as f:
                data = json.load(f)
        data["clock_format"] = self._clock_format
        with open("settings.json", "w") as f:
            json.dump(data, f)

    @Property(str, notify=clockFormatChanged)
    def clockFormat(self):
        return self._clock_format

    @Slot(str)
    def setClockFormat(self, fmt):
        self._clock_format = fmt
        self._save_clock_format()
        self.clockFormatChanged.emit()
        self.update_time()

    # ── 테마 ──
    @Property(str, notify=themeChanged)
    def theme(self):
        return self._theme

    @Slot(str)
    def setTheme(self, theme):
        self._theme = theme
        self._save_theme()
        self.themeChanged.emit()

    def _load_theme(self):
        if os.path.exists("settings.json"):
            with open("settings.json", "r") as f:
                return json.load(f).get("theme", "light")
        return "light"

    def _save_theme(self):
        data = {}
        if os.path.exists("settings.json"):
            with open("settings.json", "r") as f:
                data = json.load(f)
        data["theme"] = self._theme
        with open("settings.json", "w") as f:
            json.dump(data, f)

    def _get_date_key(self):
        """새벽 3시 기준 오늘 날짜 키 반환 (YYYY-MM-DD)"""
        now = time.localtime()
        # 오전 3시 이전이면 전날로 처리
        if now.tm_hour < 3:
            import datetime
            d = datetime.date.today() - datetime.timedelta(days=1)
            return d.strftime("%Y-%m-%d")
        return time.strftime("%Y-%m-%d")

    def _get_week_start(self, date_str=None):
        """이번 주 월요일 날짜 반환 (YYYY-MM-DD, 새벽 3시 기준)"""
        import datetime
        today_str = date_str if date_str else self._get_date_key()
        today = datetime.date.fromisoformat(today_str)
        monday = today - datetime.timedelta(days=today.weekday())  # 0=월
        return monday.isoformat()

    @staticmethod
    def _compute_posture_pct(stats):
        """자세 stats → 퍼센트 분류 (일별 화면 표시와 동일 방식)"""
        import math as _m
        total_sec = stats.get("totalSec", 0)
        if total_sec <= 0:
            return {"good": 0.0, "warn": 0.0, "bad": 0.0, "absent": 0.0, "overlap": 0.0}
        if "turtleExcSec" in stats:          # sensor_camera.py 최신 포맷
            warn_s  = stats.get("turtleExcSec",    0) + stats.get("reclinedExcSec", 0)
            bad_s   = stats.get("bentExcSec",       0) + stats.get("drowsyExcSec",  0)
            abs_s   = stats.get("absentExcSec",     0)
            ov_s    = stats.get("overlapTotalSec",  0)
        elif "turtleExc" in stats:           # 중간 포맷
            warn_s  = stats.get("turtleExc",   0) + stats.get("reclinedExc", 0)
            bad_s   = stats.get("bentExc",      0) + stats.get("drowsyExc",  0)
            abs_s   = stats.get("absentExc",    0)
            ov_s    = stats.get("overlapTotal", 0)
        else:                                # 구버전: turtleSec 등 사용
            warn_s  = stats.get("turtleSec",   0) + stats.get("reclinedSec", 0)
            bad_s   = stats.get("bentSec",      0) + stats.get("drowsySec",  0)
            abs_s   = stats.get("absentSec",    0)
            ov_s    = 0
        def t1(v): return _m.trunc(v / total_sec * 1000) / 10
        warn_p = t1(warn_s); bad_p = t1(bad_s); abs_p = t1(abs_s); ov_p = t1(ov_s)
        good_p = round(100 - warn_p - bad_p - abs_p - ov_p, 1)
        return {"good": good_p, "warn": warn_p, "bad": bad_p, "absent": abs_p, "overlap": ov_p}

    def _update_weekly_data(self, user_data, session_score, date_str=None,
                             immersion_score=None, posture_score=None, posture_counts=None,
                             live_update=False):
        """주별 공부 데이터 업데이트 (세션 종료 시 호출)
        date_str: 강제로 특정 날짜 기준으로 저장 (3시 분리용), None이면 오늘 기준"""
        import datetime
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        today_str = date_str if date_str else self._get_date_key()
        today = datetime.date.fromisoformat(today_str)
        day_key    = day_names[today.weekday()]
        week_start = self._get_week_start(today_str)

        _empty_day  = lambda: {"mins": 0, "score": 0, "sessions": 0,
                                "immersion": 0, "postureScore": 0,
                                "posture": {"turtle": 0, "bent": 0, "reclined": 0, "absent": 0, "drowsy": 0}}
        weekly = user_data.get("weeklyStudyData", {})

        # 새로운 주 시작이면 전체 리셋
        if weekly.get("weekStart") != week_start:
            weekly = {"weekStart": week_start, "days": {d: _empty_day() for d in day_names}}

        if "days" not in weekly:
            weekly["days"] = {d: _empty_day() for d in day_names}

        day_data = weekly["days"].get(day_key) or _empty_day()

        # 누적 공부 시간 (totalStudySec 기준) — secs는 정확한 초 단위
        today_secs = int(user_data.get("totalStudySec", 0))
        today_mins = round(today_secs / 60)
        day_data["mins"] = today_mins
        day_data["secs"] = today_secs

        # 점수 업데이트 — live_update면 현재 값으로 덮어쓰기, 아니면 세션 누적 평균
        n = day_data.get("sessions", 0)
        if live_update:
            day_data["score"]        = round(session_score)
            if immersion_score is not None:
                day_data["immersion"]    = round(immersion_score)
            if posture_score is not None:
                day_data["postureScore"] = round(posture_score, 2)
        else:
            # 시간 점수만 세션별 평균 — 자세/몰입은 이미 일별 누적값이므로 최신값으로 덮어씀
            day_data["score"]    = round((day_data.get("score", 0) * n + session_score) / (n + 1))
            day_data["sessions"] = n + 1
            if immersion_score is not None:
                day_data["immersion"] = round(immersion_score)      # 누적 일별값 → 덮어쓰기
            if posture_score is not None:
                day_data["postureScore"] = round(posture_score, 2)  # 누적 일별값 → 덮어쓰기

        # 자세 횟수 + 퍼센트 + 시간 — 항상 최신 누적값으로 덮어쓰기
        if posture_counts:
            day_data["posture"] = {
                "turtle":   posture_counts.get("turtleCount",   0),
                "bent":     posture_counts.get("bentCount",     0),
                "reclined": posture_counts.get("reclinedCount", 0),
                "absent":   posture_counts.get("absentCount",   0),
                "drowsy":   posture_counts.get("drowsyCount",   0),
            }
            day_data["posturePct"] = self._compute_posture_pct(posture_counts)
            day_data["postureSec"] = {
                "total":        posture_counts.get("totalSec",        0),
                "turtle":       posture_counts.get("turtleExcSec",    0),
                "bent":         posture_counts.get("bentExcSec",      0),
                "reclined":     posture_counts.get("reclinedExcSec",  0),
                "absent":       posture_counts.get("absentExcSec",    0),
                "drowsy":       posture_counts.get("drowsyExcSec",    0),
                "overlapTotal": posture_counts.get("overlapTotalSec", 0),
                "turtleExc":    posture_counts.get("turtleExcSec",    0),
                "reclinedExc":  posture_counts.get("reclinedExcSec",  0),
                "bentExc":      posture_counts.get("bentExcSec",      0),
                "drowsyExc":    posture_counts.get("drowsyExcSec",    0),
                "absentExc":    posture_counts.get("absentExcSec",    0),
            }

        # 누적 뽀모도로 목표 시간 저장
        day_data["goalSec"] = int(user_data.get("totalPomodoroGoalSec", 0))

        weekly["days"][day_key] = day_data

        # 주별 집계 — 공부한 날 기준 평균 (live_update 중이면 오늘도 포함)
        active = [v for v in weekly["days"].values() if v.get("sessions", 0) > 0 or v.get("mins", 0) > 0]
        if active:
            weekly["avgImmersion"]    = round(sum(v.get("immersion",    0) for v in active) / len(active))
            weekly["avgTimeScore"]    = round(sum(v.get("score",        0) for v in active) / len(active))
            weekly["avgPostureScore"] = round(sum(v.get("postureScore", 0) for v in active) / len(active), 1)
        else:
            weekly["avgImmersion"] = weekly["avgTimeScore"] = weekly["avgPostureScore"] = 0

        posture_keys = ["turtle", "bent", "reclined", "absent", "drowsy"]
        weekly["totalPosture"] = {
            k: sum(v.get("posture", {}).get(k, 0) for v in weekly["days"].values())
            for k in posture_keys
        }

        # 자세 시간 합산 (exclusive seconds)
        posture_sec_keys = ["total", "turtle", "bent", "reclined", "absent", "drowsy", "overlapTotal",
                            "turtleExc", "reclinedExc", "bentExc", "drowsyExc", "absentExc"]
        weekly["totalPostureSec"] = {
            k: round(sum(v.get("postureSec", {}).get(k, 0) for v in weekly["days"].values()), 1)
            for k in posture_sec_keys
        }

        # 목표 시간 합산
        weekly["totalGoalSec"] = sum(v.get("goalSec", 0) for v in weekly["days"].values())

        # 자세 퍼센트 평균 (posturePct 있는 날만)
        active_pct = [v["posturePct"] for v in active if v.get("posturePct")]
        if active_pct:
            pk = ["good", "warn", "bad", "absent", "overlap"]
            avg_pct = {k: round(sum(p.get(k, 0) for p in active_pct) / len(active_pct), 1) for k in pk}
            weekly["avgPosturePct"] = avg_pct

        user_data["weeklyStudyData"] = weekly
        print(f"[주별] {day_key} 업데이트: {today_mins}분, timeScore={day_data['score']}, "
              f"immersion={day_data.get('immersion', 0)}, postureScore={day_data.get('postureScore', 0)}")

    def _update_monthly_data(self, user_data, date_str,
                              immersion_score=None, posture_score=None, posture_counts=None):
        """월별 일별 공부 데이터 저장 (달력·주차별 집계용)"""
        import datetime
        today     = datetime.date.fromisoformat(date_str)
        month_key = today.strftime("%Y-%m")
        day_key   = str(today.day)

        monthly_all = user_data.get("monthlyData", {})
        month_data  = monthly_all.get(month_key, {})

        prev_posture = month_data.get(day_key, {}).get("posture",
                       {"turtle": 0, "bent": 0, "reclined": 0, "absent": 0, "drowsy": 0})
        _pct_stats = posture_counts if posture_counts else {}
        _day_posture_pct = self._compute_posture_pct(posture_counts) if posture_counts else None
        _day_posture_sec = {
            "total":        _pct_stats.get("totalSec",        0),
            "turtle":       _pct_stats.get("turtleExcSec",    0),
            "bent":         _pct_stats.get("bentExcSec",      0),
            "reclined":     _pct_stats.get("reclinedExcSec",  0),
            "absent":       _pct_stats.get("absentExcSec",    0),
            "drowsy":       _pct_stats.get("drowsyExcSec",    0),
            "overlapTotal": _pct_stats.get("overlapTotalSec", 0),
            "turtleExc":    _pct_stats.get("turtleExcSec",    0),
            "reclinedExc":  _pct_stats.get("reclinedExcSec",  0),
            "bentExc":      _pct_stats.get("bentExcSec",      0),
            "drowsyExc":    _pct_stats.get("drowsyExcSec",    0),
            "absentExc":    _pct_stats.get("absentExcSec",    0),
        } if posture_counts else {}
        _daily_scores = user_data.get("dailySessionScores", [])
        _avg_time_score = round(sum(_daily_scores) / len(_daily_scores)) if _daily_scores else 0
        month_data[day_key] = {
            "mins":         round(user_data.get("totalStudySec", 0) / 60),
            "secs":         int(user_data.get("totalStudySec", 0)),
            "score":        _avg_time_score,
            "immersion":    round(immersion_score, 2) if immersion_score is not None else 0,
            "postureScore": round(posture_score, 2)   if posture_score   is not None else 0,
            "goalSec":      int(user_data.get("totalPomodoroGoalSec", 0)),
            "posturePct":   _day_posture_pct,
            "postureSec":   _day_posture_sec,
            "posture": {
                "turtle":   posture_counts.get("turtleCount",   0) if posture_counts else prev_posture.get("turtle",   0),
                "bent":     posture_counts.get("bentCount",     0) if posture_counts else prev_posture.get("bent",     0),
                "reclined": posture_counts.get("reclinedCount", 0) if posture_counts else prev_posture.get("reclined", 0),
                "absent":   posture_counts.get("absentCount",   0) if posture_counts else prev_posture.get("absent",   0),
                "drowsy":   posture_counts.get("drowsyCount",   0) if posture_counts else prev_posture.get("drowsy",   0),
            }
        }
        monthly_all[month_key] = month_data
        user_data["monthlyData"] = monthly_all
        print(f"[월별] {date_str} 업데이트: {month_data[day_key]['mins']}분, "
              f"immersion={month_data[day_key]['immersion']}")

    def _archive_session_at_reset(self, reset_ts, prev_date_str):
        """새벽 3시 경계에서 이전 날 세션 데이터를 저장하고 카메라 통계 초기화"""
        if not self._focus_start_time or not self._active_user:
            return
        print(f"[3시분리] 세션 중 날짜 전환 감지 → {prev_date_str} 데이터 저장 시작")
        import time as _t

        end_time = reset_ts  # 3:00:00 타임스탬프

        # 시간 점수 (이전 날 공부시간 기준)
        elapsed = self._total_study_seconds - getattr(self, "_session_start_study_seconds", 0)
        actual_sec = elapsed if elapsed > 0 else max(0, end_time - self._focus_start_time - self._total_rest_seconds)
        target_sec = self._pomodoro_seconds if self._pomodoro_seconds > 0 else 1500
        session_score = min(100, round(actual_sec / target_sec * 100))

        user_data = get_user_data(self._active_user)

        # 이전 날 기준으로 dailySessionScores 업데이트
        if user_data.get("lastSessionDate", "") != prev_date_str:
            user_data["dailySessionScores"]  = []
            user_data["lastSessionDate"]     = prev_date_str
            user_data["totalPomodoroGoalSec"] = 0
        user_data["dailySessionScores"].append(session_score)
        user_data["totalPomodoroGoalSec"] = user_data.get("totalPomodoroGoalSec", 0) + target_sec

        scores = user_data["dailySessionScores"]
        user_data["timeScore"]     = round(sum(scores) / len(scores))
        user_data["totalStudySec"] = self._total_study_seconds

        # 시간대별 분포 (session_start ~ 3시)
        try:
            _eff_start_arc = (camera_mgr.analyzer._analysis_start_time or self._focus_start_time) if SENSOR_AVAILABLE else self._focus_start_time
            _end_arc = _eff_start_arc + actual_sec
            _arc_ps = round(self._posture_score, 1)
            if SENSOR_AVAILABLE:
                _open_arc = camera_mgr.analyzer._open_records.copy()
                session_hourly = camera_mgr.analyzer.generate_hourly_data(
                    _eff_start_arc, _end_arc, open_records=_open_arc)
                for _hk in session_hourly:
                    session_hourly[_hk]["score"] = _arc_ps
            else:
                session_hourly = self._simple_hourly(
                    _eff_start_arc, _end_arc, _arc_ps)
            user_data["hourlyData"] = self._merge_hourly(
                user_data.get("hourlyData", {}), session_hourly)

            if SENSOR_AVAILABLE:
                session_segs = camera_mgr.analyzer.generate_hourly_segments(
                    _eff_start_arc, _end_arc, open_records=_open_arc)
            else:
                session_segs = self._simple_segments(_eff_start_arc, _end_arc)
            user_data["hourlySegments"] = self._merge_segments(
                user_data.get("hourlySegments", {}), session_segs)
        except Exception as e:
            print(f"[3시분리] hourlyData 저장 실패: {e}")

        # 자세 통계 누적 후 카메라 리셋 (weekly 저장 전에 먼저 실행)
        _archive_posture_counts = None
        _archive_posture_score  = self._posture_score
        if SENSOR_AVAILABLE:
            session_stats = camera_mgr.get_stats()
            for k in self._POSTURE_COUNT_KEYS + self._POSTURE_SEC_KEYS:
                self._daily_posture_accum[k] = self._daily_posture_accum.get(k, 0) + session_stats.get(k, 0)
            user_data["dailyPostureAccum"] = self._daily_posture_accum
            _archive_posture_counts = self._daily_posture_accum
            import math as _math
            _total = self._daily_posture_accum.get("totalSec", 0)
            _good  = self._daily_posture_accum.get("goodSec", 0)
            if _total > 0:
                _archive_posture_score = _math.trunc(_good / _total * 1000) / 10
            camera_mgr.analyzer.reset_stats()

        _archive_immersion = round((session_score + _archive_posture_score) / 2)

        # 주별 데이터 (이전 날 기준, 자세 누적 완료 후)
        self._update_weekly_data(
            user_data, session_score, date_str=prev_date_str,
            immersion_score=_archive_immersion,
            posture_score=_archive_posture_score,
            posture_counts=_archive_posture_counts,
        )
        # 월별 데이터 (이전 날 기준)
        self._update_monthly_data(
            user_data, prev_date_str,
            immersion_score=_archive_immersion,
            posture_score=_archive_posture_score,
            posture_counts=_archive_posture_counts,
        )

        save_user_data(self._active_user, user_data)
        push_to_github()
        print(f"[3시분리] {prev_date_str} 저장 완료 (공부 {actual_sec:.0f}초, 점수 {session_score})")

    def _check_daily_reset(self):
        """새벽 3시 기준으로 날짜가 넘어갔으면 오늘 공부시간 초기화 및 뽀모도로 카운트 초기화"""
        today = self._get_date_key()
        # 이미 오늘 날짜로 체크했으면 파일 I/O 없이 스킵
        if getattr(self, '_last_reset_check_date', None) == today:
            return
        path = self._study_time_file()
        data = {}
        if os.path.exists(path):
            with open(path, "r") as f:
                data = json.load(f)

        last_date = data.get("last_date", "")

        if last_date != today:
            import datetime as _dt
            # 3:00:00 정각 타임스탬프 계산
            now_dt = _dt.datetime.now()
            reset_dt = now_dt.replace(hour=3, minute=0, second=0, microsecond=0)
            reset_ts = reset_dt.timestamp()

            # 집중모드 중이면 3시 이전 데이터를 전날로 저장
            if self._focus_start_time and self._active_user and last_date:
                self._archive_session_at_reset(reset_ts, last_date)
                self._daily_posture_accum = {}  # archive에서 누적했으므로 초기화

            # 어제 공부시간을 날짜별 기록에 저장
            saved_seconds = data.get("total_seconds", 0)
            if last_date and saved_seconds > 0:
                daily = data.get("daily", {})
                daily[last_date] = saved_seconds
                data["daily"] = daily
            # 오늘 공부시간 초기화
            self._total_study_seconds = 0
            data["total_seconds"] = 0
            data["last_date"] = today
            data["daily_pomodoro_count"] = 0

            with open(path, "w") as f:
                json.dump(data, f)
            self._last_reset_check_date = today  # 리셋 완료 → 캐시 갱신
            self.studyTimeChanged.emit()

            # shared_data 오늘 기준 초기화
            if self._active_user:
                user_data = get_user_data(self._active_user)
                user_data["pomodoroCount"]         = 0
                user_data["totalStudySec"]         = 0
                user_data["totalPomodoroGoalSec"]  = 0
                user_data["hourlyData"]            = {}
                user_data["hourlySegments"]        = {}
                user_data["currentSessionHourly"]  = {}
                user_data["currentSessionSegments"]= {}
                user_data["dailySessionScores"]    = []
                user_data["lastSessionDate"]       = ""
                user_data["timeScore"]             = 0
                user_data["postureScore"]          = 0
                user_data["immersionScore"]        = 0
                user_data["dailyPostureAccum"]     = {}
                user_data["postureStats"]          = {}
                self._daily_posture_accum          = {}
                self._posture_stats                = {}
                self._posture_score                = 0
                self._immersion_score              = 0
                self.postureScoreChanged.emit()
                self.immersionChanged.emit()

                # 주별 리셋 체크 (월요일 3시: weekStart가 바뀌면 초기화)
                new_week_start = self._get_week_start(today)
                old_week_start = user_data.get("weeklyStudyData", {}).get("weekStart", "")
                if old_week_start and old_week_start != new_week_start:
                    _day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                    _empty     = lambda: {"mins": 0, "score": 0, "sessions": 0,
                                          "immersion": 0, "postureScore": 0,
                                          "posture": {"turtle": 0, "bent": 0, "reclined": 0,
                                                      "absent": 0, "drowsy": 0}}
                    user_data["weeklyStudyData"] = {
                        "weekStart":       new_week_start,
                        "days":            {d: _empty() for d in _day_names},
                        "avgImmersion":    0,
                        "avgTimeScore":    0,
                        "avgPostureScore": 0,
                        "totalPosture":    {"turtle": 0, "bent": 0, "reclined": 0,
                                            "absent": 0, "drowsy": 0},
                    }
                    print(f"[주별리셋] {old_week_start} → {new_week_start}")

                save_user_data(self._active_user, user_data)

            # 집중모드 중이면 세션 시작점을 3시로 갱신 (오늘 데이터부터 새로 쌓임)
            if self._focus_start_time:
                self._focus_start_time = reset_ts
                self._session_start_study_seconds = 0
                print(f"[3시분리] 세션 계속 진행 중 → focus_start_time을 {reset_dt.strftime('%H:%M:%S')}으로 갱신")
        else:
            # 리셋 불필요 확인 → 캐시 갱신 (반복 파일 I/O 방지)
            self._last_reset_check_date = today

    def _study_time_file(self):
        """유저별 공부시간 파일 경로"""
        if self._active_user:
            safe = "".join(c for c in self._active_user if c.isalnum() or c in "-_")
            return f"study_time_{safe}.json"
        return "study_time.json"

    def _load_study_time(self):
        path = self._study_time_file()
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f).get("total_seconds", 0)
        return 0

    def _save_study_time(self):
        path = self._study_time_file()
        data = {}
        if os.path.exists(path):
            with open(path, "r") as f:
                data = json.load(f)
        data["total_seconds"] = self._total_study_seconds
        data["last_date"] = self._get_date_key()
        with open(path, "w") as f:
            json.dump(data, f)

    def _get_week_data(self):
        """이번 주(월~일) 날짜별 공부시간(분) 반환"""
        import datetime
        today = datetime.date.today()
        # 새벽 3시 이전이면 전날 기준
        now = time.localtime()
        if now.tm_hour < 3:
            today = today - datetime.timedelta(days=1)

        # 이번 주 월요일 구하기
        monday = today - datetime.timedelta(days=today.weekday())
        path = self._study_time_file()
        daily = {}
        if os.path.exists(path):
            with open(path, "r") as f:
                daily = json.load(f).get("daily", {})

        result = []
        days_ko = ["월", "화", "수", "목", "금", "토", "일"]
        days_en = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        for i in range(7):
            d = monday + datetime.timedelta(days=i)
            key = d.strftime("%Y-%m-%d")
            seconds = daily.get(key, 0)
            # 오늘이면 현재 진행중인 공부시간 포함
            if key == self._get_date_key():
                seconds += self._total_study_seconds
            result.append({
                "day": days_ko[i],
                "dayEn": days_en[i],
                "minutes": seconds // 60,
                "seconds": seconds,
                "isToday": (key == self._get_date_key())
            })
        return result

    @Property('QVariantList', notify=weekDataChanged)
    def weekData(self):
        return self._get_week_data()

    @Property(int, notify=weekDataChanged)
    def todayIndex(self):
        import datetime
        today = datetime.date.today()
        now = time.localtime()
        if now.tm_hour < 3:
            today = today - datetime.timedelta(days=1)
        return today.weekday()  # 0=월, 6=일

    @Slot(bool)
    def setPostureStatsPaused(self, paused):
        """자리비움 팝업 중 자세 통계 일시정지/재개"""
        self._away_popup_active = paused
        if SENSOR_AVAILABLE:
            camera_mgr.analyzer.stats_paused = paused
            if paused:
                # 팝업 뜨는 순간 열린 기록을 지금 시각 기준으로 닫아서 저장
                # → 재개 후 record가 닫힐 때 팝업 대기 시간이 포함되지 않음
                now = time.time()
                a = camera_mgr.analyzer
                for p, start in list(a._open_records.items()):
                    duration = round(now - start, 1)
                    if duration >= 1:
                        a.posture_stats[p]["records"].append(
                            {"start": start, "duration": duration}
                        )
                a._open_records = {}
                # _active_postures는 유지 → 재개 시 count 중복 증가 방지
            else:
                # 재개 시 타임스탬프 리셋 → 자리비움 시간이 통계에 포함되지 않음
                camera_mgr.analyzer._last_stats_time = None
        if not paused:
            # 버튼 눌러 팝업 닫힐 때 → 자리비움 타이머 정리
            self._absent_start       = None
            self._absent_popup_start = None

    @Property(int, notify=immersionChanged)
    def immersionScore(self):
        return int(self._immersion_score + 0.5)

    @Property(float, notify=postureScoreChanged)
    def postureScore(self):
        return round(self._posture_score, 1)

    @Property(int, notify=studyTimeChanged)
    def totalStudySeconds(self):
        return self._total_study_seconds

    @Slot(int)
    def addStudyTime(self, seconds):
        """공부시간 추가 (중복 방지)"""
        # ★ FIX: 동일한 값이 중복으로 추가되지 않도록 체크
        if seconds <= 0:
            print(f"[경고] 0초 이하의 공부시간 추가 시도: {seconds}초")
            return
        
        if self._last_study_time_added == seconds:
            print(f"[경고] 중복된 공부시간 추가 방지: {seconds}초")
            return
        
        self._last_study_time_added = seconds
        self._total_study_seconds += seconds
        
        if self._active_user:
            user_data = get_user_data(self._active_user)
            user_data["pomodoroCount"] = user_data.get("pomodoroCount", 0) + 1
            user_data["totalStudySec"] = self._total_study_seconds
            _total_goal = user_data.get("totalPomodoroGoalSec", 0) + self._pomodoro_seconds
            if _total_goal > 0:
                time_score = min(100, round(self._total_study_seconds / _total_goal * 100))
                user_data["timeScore"] = time_score
            save_user_data(self._active_user, user_data)
        
        self._save_study_time()
        self.studyTimeChanged.emit()
        self.weekDataChanged.emit()
        
        print(f"[공부시간] {seconds}초 추가 → 총 {self._total_study_seconds}초")

    _POSTURE_COUNT_KEYS = ["drowsyCount", "bentCount", "turtleCount", "reclinedCount", "absentCount", "proneCount"]
    _POSTURE_SEC_KEYS   = ["goodSec", "drowsySec", "bentSec", "reclinedSec", "absentSec", "turtleSec", "totalSec",
                           "turtleExcSec", "bentExcSec", "reclinedExcSec", "drowsyExcSec", "absentExcSec",
                           "overlapTotalSec",
                           "drowsyOverlapSec", "bentOverlapSec", "reclinedOverlapSec", "turtleOverlapSec"]

    def _merge_posture_counts(self, session_stats):
        """이전 세션 누적 + 현재 세션 합산"""
        merged = dict(session_stats)
        for k in self._POSTURE_COUNT_KEYS + self._POSTURE_SEC_KEYS:
            merged[k] = self._daily_posture_accum.get(k, 0) + session_stats.get(k, 0)
        return merged

    @Property('QVariantMap', notify=postureStatsChanged)
    def postureStatsMap(self):
        return self._posture_stats

    @Slot()
    def resetLiveScore(self):
        """카메라 팝업 열릴 때 - 하루 첫 번째만 100점, 이후엔 현재 점수 유지"""
        today = self._get_date_key()
        if self._live_score_date != today:
            self._live_score_date    = today
            self._live_session_score = 100
            self._live_smooth_score  = 100.0
            self.liveSessionScoreChanged.emit()

    @Property(int, notify=liveSessionScoreChanged)
    def liveSessionScore(self):
        return self._live_session_score

    @Slot()
    def refreshLiveStats(self):
        """카메라 팝업용 실시간 자세 통계 업데이트 (디스크 저장 없음)"""
        if not SENSOR_AVAILABLE:
            return
        import math as _math
        cur_stats  = camera_mgr.get_stats()
        _cur_rr    = cur_stats.get("postureRatio", 0)  # 기록 기반 비율 (머지 전)
        _cur_total = cur_stats.get("totalSec", 0)
        stats = self._merge_posture_counts(cur_stats)
        total_sec = stats.get("totalSec", 0)
        if total_sec > 0:
            _accum_total = self._daily_posture_accum.get("totalSec", 0)
            if _accum_total <= 0 or _cur_total <= 0:
                stats["postureRatio"] = _cur_rr
            else:
                _cur_good = _cur_rr / 100.0 * _cur_total
                _acc_good = self._daily_posture_accum.get("goodSec", 0)
                stats["postureRatio"] = _math.trunc((_cur_good + _acc_good) / total_sec * 1000) / 10
        self._posture_stats = stats
        self.postureStatsChanged.emit()

    @Property(str, notify=qrChanged)
    def qrImagePath(self):
        return self._qr_path

    def _set_qr_path(self, path):
        self._qr_path = "file:" + path
        self.qrChanged.emit()

    @Slot(int)
    def setBrightness(self, value):
        try:
            brightness = int(value * 31 / 100)
            with open("/sys/class/backlight/10-0045/brightness", "w") as f:
                f.write(str(brightness))
        except Exception as e:
            print(f"밝기 조절 실패: {e}")

    @Slot()
    def systemShutdown(self):
        """라즈베리파이 전원 종료"""
        import subprocess
        print("[시스템] 라즈베리파이 종료 명령 실행")
        subprocess.Popen(['sudo', 'shutdown', '-h', 'now'])

    @Slot()
    def resetStudyData(self):
        self._total_study_seconds = 0
        self._daily_posture_accum = {}
        self._posture_score = 0
        self._immersion_score = 0
        self.studyTimeChanged.emit()
        self.weekDataChanged.emit()
        self.postureScoreChanged.emit()
        self.immersionChanged.emit()

        path = self._study_time_file()
        if os.path.exists(path):
            os.remove(path)
        if os.path.exists("settings.json"):
            os.remove("settings.json")
        self._has_base_posture = False
        self.postureChanged.emit()
        self._clock_format = "24"
        self.clockFormatChanged.emit()

        # shared_data.json의 유저 데이터도 초기화 (웹앱에 반영)
        if self._active_user:
            user_data = get_user_data(self._active_user)
            keys_to_reset = [
                "dailySessionScores", "pomodoroCount", "totalStudySec",
                "timeScore", "postureScore", "immersionScore",
                "postureStats", "dailyPostureAccum",
                "hourlyData", "hourlySegments",
                "currentSessionHourly", "currentSessionSegments",
                "weeklyStudyData", "lastSessionDate",
            ]
            for k in keys_to_reset:
                if k == "dailySessionScores":
                    user_data[k] = []
                elif k in ("postureStats", "dailyPostureAccum", "hourlyData", "hourlySegments",
                           "currentSessionHourly", "currentSessionSegments", "weeklyStudyData"):
                    user_data[k] = {}
                else:
                    user_data[k] = 0
            user_data["lastSessionDate"] = ""
            save_user_data(self._active_user, user_data)




# ── 카메라 프레임 Provider (QML Image source로 사용) ──
class FrameProvider(QQuickImageProvider):
    frameChanged = Signal()

    def __init__(self):
        super().__init__(QQmlImageProviderBase.ImageType.Image)
        self._image = QImage(640, 480, QImage.Format.Format_RGB888)
        self._image.fill(0)
        self._lock = threading.Lock()

    def requestImage(self, id, size, requestedSize):
        with self._lock:
            return self._image.copy()

    def update_frame(self, rgb_frame):
        """RGB 프레임 → QImage 변환 (거울모드/HUD는 sensor_camera에서 처리됨)"""
        try:
            h, w, ch = rgb_frame.shape
            with self._lock:
                self._image = QImage(rgb_frame.data.tobytes(), w, h, ch * w, QImage.Format.Format_RGB888).copy()
            self.frameChanged.emit()
        except Exception as e:
            print(f"[프레임] 변환 오류: {e}")


frame_provider = FrameProvider()


def is_raspberry_pi():
    try:
        with open("/proc/device-tree/model", "r") as f:
            return "Raspberry Pi" in f.read()
    except:
        return False


def run():
    app = QGuiApplication(sys.argv)

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    id1 = QFontDatabase.addApplicationFont(os.path.join(BASE_DIR, "Pretendard-Black.otf"))
    id2 = QFontDatabase.addApplicationFont(os.path.join(BASE_DIR, "Pretendard-Bold.otf"))
    id3 = QFontDatabase.addApplicationFont(os.path.join(BASE_DIR, "Pretendard-Medium.otf"))
    print("Black id:", id1, QFontDatabase.applicationFontFamilies(id1))
    print("Bold id:", id2, QFontDatabase.applicationFontFamilies(id2))
    print("Medium id:", id3, QFontDatabase.applicationFontFamilies(id3))

    engine = QQmlApplicationEngine()
    engine.addImageProvider("camera", frame_provider)
    cube = StudyCube()
    engine.rootContext().setContextProperty("cube", cube)
    engine.rootContext().setContextProperty("frameProvider", frame_provider)

    # Flask 서버 스레드 시작 (cube 생성 후 — QR 콜백 연결)
    if FLASK_AVAILABLE:
        use_ngrok = "--ngrok" in sys.argv
        flask_thread = threading.Thread(
            target=start_server,
            kwargs={"use_ngrok": use_ngrok, "port": 5001, "on_qr_ready": cube._set_qr_path},
            daemon=True
        )
        flask_thread.start()
        print("Flask 서버 시작: http://localhost:5001")

    engine.load("main.qml")
    if not engine.rootObjects():
        sys.exit(-1)
    if is_raspberry_pi():
        engine.rootObjects()[0].showFullScreen()

    # 센서 시작
    if SENSOR_AVAILABLE:
        start_all()
        print("[센서] 백그라운드 시작")

    ret = app.exec()
    if SENSOR_AVAILABLE:
        sensor_cleanup()
    sys.exit(ret)


if __name__ == "__main__":
    run()
