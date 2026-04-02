#!/usr/bin/env python3
"""
reels-catcher Extension 테스트 서버
Chrome Extension에서 보내는 POST 요청을 받아 터미널에 출력합니다.

실행:
    python3 debug_server.py
    python3 debug_server.py --port 8000
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")

        try:
            data = json.loads(body)
            print("\n✅ 릴스 수신!")
            print(f"   URL      : {data.get('url', '-')}")
            print(f"   shortcode: {data.get('shortcode', '-')}")
            print(f"   thread_id: {data.get('thread_id', '-')}")
            print(f"   sender_id: {data.get('sender_id', '-')}")
            print(f"   timestamp: {data.get('timestamp', '-')}")
            print(f"   source   : {data.get('source', '-')}")
        except Exception:
            print(f"\n📦 raw body: {body}")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        pass  # 기본 로그 출력 억제


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="reels-catcher Extension 테스트 서버")
    parser.add_argument("--port", type=int, default=8000, help="포트 번호 (기본값: 8000)")
    args = parser.parse_args()

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"🚀 테스트 서버 실행 중: http://localhost:{args.port}/api/reels")
    print("   Chrome Extension에서 릴스 DM을 수신하면 여기 출력됩니다.")
    print("   종료: Ctrl+C\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n서버 종료")
