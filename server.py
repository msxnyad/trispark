"""
독립 Flask 서버 — 대시보드(main.py) 없이도 단독 실행 가능
실행: python server.py           (로컬 네트워크만)
     python server.py --ngrok   (QR 코드 + 외부 공개 URL)
"""
import os
import sys
import json
import logging
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

SHARED_DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shared_data.json")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

GITHUB_TOKEN = ""  # 사용 안 함 — 브라우저에서 직접 동기화
GITHUB_OWNER = "msxnyad"
GITHUB_REPO  = "trispark"
GITHUB_FILE  = "shared_data.json"

def push_to_github():
    """shared_data.json을 GitHub에 자동 업로드 (백그라운드)"""
    import threading
    def _push():
        try:
            import urllib.request, base64 as _b64
            url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GITHUB_FILE}"
            headers = {"Authorization": f"token {GITHUB_TOKEN}", "Content-Type": "application/json"}
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req) as r:
                    sha = json.loads(r.read())["sha"]
            except:
                sha = None
            if not os.path.exists(SHARED_DATA_FILE):
                return
            with open(SHARED_DATA_FILE, "r", encoding="utf-8") as f:
                content = f.read()
            encoded = _b64.b64encode(content.encode("utf-8")).decode("utf-8")
            body = json.dumps({"message": "auto sync", "content": encoded,
                               **({"sha": sha} if sha else {})}).encode("utf-8")
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


def create_app():
    app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")
    CORS(app)

    @app.route("/")
    def index():
        return send_from_directory(BASE_DIR, "index.html")

    @app.route("/sync", methods=["POST"])
    def sync():
        try:
            data = request.get_json()
            if not data:
                return jsonify({"error": "no data"}), 400

            user_id = data.get("userId", "")
            shared = load_shared_data()

            if "_appUsers" in data:
                shared["appUsers"] = data.pop("_appUsers")

            if user_id:
                shared["activeUser"] = user_id
                if "users" not in shared:
                    shared["users"] = {}
                user_fields = {k: v for k, v in data.items() if k != "userId"}
                if user_id not in shared["users"]:
                    shared["users"][user_id] = {}
                shared["users"][user_id].update(user_fields)
            else:
                shared.update(data)

            save_shared_data(shared)
            push_to_github()
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/status", methods=["GET"])
    def status():
        shared = load_shared_data()
        active = shared.get("activeUser", "")
        user_data = shared.get("users", {}).get(active, {}).copy() if active else {}

        return jsonify({
            "activeUser":              active,
            "completedTasks":          user_data.get("completedTasks", []),
            "urgentTasks":             user_data.get("urgentTasks", []),
            "totalStudySec":           user_data.get("totalStudySec", 0),
            "pomodoroCount":           user_data.get("pomodoroCount", 0),
            "pomodoroSeconds":         user_data.get("pomodoroSeconds", 1500),
            "totalPomodoroGoalSec":    user_data.get("totalPomodoroGoalSec", 0),
            "postureStats":            user_data.get("postureStats", {}),
            "hourlyData":              user_data.get("hourlyData", {}),
            "currentSessionHourly":    user_data.get("currentSessionHourly", {}),
            "hourlySegments":          user_data.get("hourlySegments", {}),
            "currentSessionSegments":  user_data.get("currentSessionSegments", {}),
            "timeScore":               user_data.get("timeScore", 0),
            "postureScore":            user_data.get("postureScore", 0),
            "immersionScore":          user_data.get("immersionScore", 0),
            "weeklyStudyData":         user_data.get("weeklyStudyData", {}),
            "monthlyData":             user_data.get("monthlyData", {}),
            "sessionActive":           user_data.get("sessionActive", False),
            "temp":                    user_data.get("temp"),
            "humidity":                user_data.get("humidity"),
        })

    @app.route("/reset-all", methods=["POST"])
    def reset_all():
        """전체 데이터 초기화 (모든 유저 기록 삭제)"""
        try:
            shared = load_shared_data()
            active = shared.get("activeUser", "")
            if not active or active not in shared.get("users", {}):
                return jsonify({"error": "활성 유저 없음"}), 400
            keys_to_reset = [
                "dailySessionScores", "pomodoroCount", "totalStudySec",
                "timeScore", "postureScore", "immersionScore", "totalPomodoroGoalSec",
                "postureStats", "dailyPostureAccum",
                "hourlyData", "hourlySegments",
                "currentSessionHourly", "currentSessionSegments",
                "lastSessionDate", "weeklyStudyData", "monthlyData",
            ]
            u = shared["users"][active]
            for k in keys_to_reset:
                if k in ["dailySessionScores"]:
                    u[k] = []
                elif k in ["dailyPostureAccum", "postureStats", "hourlyData",
                           "hourlySegments", "currentSessionHourly", "currentSessionSegments",
                           "weeklyStudyData", "monthlyData"]:
                    u[k] = {}
                else:
                    u[k] = 0
            u["lastSessionDate"] = ""
            save_shared_data(shared)
            return jsonify({"ok": True, "user": active})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/reset-today", methods=["POST"])
    def reset_today():
        """오늘 데이터 초기화 (시간/자세 점수, 세션 기록 등)"""
        try:
            shared = load_shared_data()
            active = shared.get("activeUser", "")
            if not active or active not in shared.get("users", {}):
                return jsonify({"error": "활성 유저 없음"}), 400

            u = shared["users"][active]
            keys_to_reset = [
                "dailySessionScores", "pomodoroCount", "totalStudySec",
                "timeScore", "postureScore", "immersionScore", "totalPomodoroGoalSec",
                "postureStats", "dailyPostureAccum",
                "hourlyData", "hourlySegments",
                "currentSessionHourly", "currentSessionSegments",
                "lastSessionDate",
            ]
            for k in keys_to_reset:
                if k in ["dailySessionScores"]:
                    u[k] = []
                elif k in ["dailyPostureAccum", "postureStats", "hourlyData",
                           "hourlySegments", "currentSessionHourly", "currentSessionSegments"]:
                    u[k] = {}
                else:
                    u[k] = 0
            u["lastSessionDate"] = ""
            save_shared_data(shared)
            return jsonify({"ok": True, "user": active})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/sync-time", methods=["POST"])
    def sync_time():
        """폰 브라우저 시각으로 라즈베리파이 시스템 시간 동기화"""
        try:
            data = request.get_json()
            ts_ms = data.get("timestamp")
            if not ts_ms:
                return jsonify({"error": "no timestamp"}), 400
            import datetime, subprocess
            dt = datetime.datetime.fromtimestamp(ts_ms / 1000)
            time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            result = subprocess.run(
                ["sudo", "date", "-s", time_str],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                print(f"[시각동기화] 폰 시각으로 설정: {time_str}")
                return jsonify({"ok": True, "set": time_str})
            else:
                return jsonify({"error": result.stderr}), 500
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # 정상 요청 로그 숨기기
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    return app


def _save_qr(url):
    """QR 코드를 qr_code.png로 저장, 경로 반환"""
    qr_path = os.path.join(BASE_DIR, "qr_code.png")
    try:
        import qrcode
        qr = qrcode.QRCode(border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(qr_path)
        print(f"[QR] 저장: {qr_path}  URL: {url}")
    except ImportError:
        print("[QR] qrcode 없음 — pip install 'qrcode[pil]' 로 설치하세요")
        qr_path = ""
    return qr_path


def _get_local_url(port):
    import subprocess
    try:
        # wlan0 IP 직접 읽기 (핫스팟/클라이언트 모두 동작)
        out = subprocess.check_output(["ip", "-4", "addr", "show", "wlan0"], text=True)
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("inet "):
                ip = line.split()[1].split("/")[0]
                if not ip.startswith("127."):
                    return f"http://{ip}:{port}"
    except Exception:
        pass
    # fallback: NetworkManager 핫스팟 기본 IP
    return f"http://10.42.0.1:{port}"


def start_server(use_ngrok=False, port=5001, on_qr_ready=None, ngrok_domain=None):
    app = create_app()
    public_url = None

    if use_ngrok:
        try:
            from pyngrok import ngrok
            opts = {"addr": port, "proto": "http"}
            if ngrok_domain:
                opts["domain"] = ngrok_domain
            tunnel = ngrok.connect(**opts)
            public_url = tunnel.public_url
            if public_url.startswith("http://"):
                public_url = "https://" + public_url[7:]
            print(f"[ngrok] 공개 URL: {public_url}")
            if ngrok_domain:
                print(f"[ngrok] 고정 도메인 사용 중 — 홈 화면 추가 후 오프라인에서도 접속 가능")
        except ImportError:
            print("[ngrok] pyngrok 없음 — pip install pyngrok 로 설치하세요")
        except Exception as e:
            print(f"[ngrok] 연결 실패: {e}")

    local_url = _get_local_url(port)
    if public_url is None:
        public_url = local_url
        print(f"[서버] 네트워크 URL: {public_url}")

    # ngrok URL을 shared_data.json에 저장 → GitHub Pages가 읽어서 API 주소로 사용
    if public_url and public_url.startswith("https://"):
        try:
            sd = load_shared_data()
            sd["ngrokUrl"] = public_url
            save_shared_data(sd)
            print(f"[서버] ngrok URL → shared_data.json 저장: {public_url}")
        except Exception as e:
            print(f"[서버] ngrok URL 저장 실패: {e}")

    qr_path = _save_qr(public_url)
    if on_qr_ready and qr_path:
        on_qr_ready(qr_path)

    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    use_ngrok = "--ngrok" in sys.argv
    # --domain <subdomain.ngrok-free.app> 로 고정 도메인 지정 가능
    ngrok_domain = None
    if "--domain" in sys.argv:
        idx = sys.argv.index("--domain")
        if idx + 1 < len(sys.argv):
            ngrok_domain = sys.argv[idx + 1]
    start_server(use_ngrok=use_ngrok, ngrok_domain=ngrok_domain)
