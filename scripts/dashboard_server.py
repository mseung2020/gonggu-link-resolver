#!/usr/bin/env python3
"""링크 해석 모듈 케이스 테스트 대시보드 서버.
- / → dashboard/case_test.html
- GET /api/status → data/results/case_test_run.json 내용 (크롤 모듈이 test_run_store.update()로 갱신)
- GET /media/{id}.jpg → data/cache/case_test/{id}.jpg (홉에서 저장한 최종 상품 이미지)

실행: python3 scripts/dashboard_server.py  →  http://localhost:8010/
"""
import json
import os
import pathlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import test_run_store

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC_DIR = str(ROOT / 'dashboard')
MEDIA_DIR = ROOT / 'data/cache/case_test'
PORT = 8010


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def do_GET(self):
        if self.path == '/':
            self.path = '/case_test.html'
            return super().do_GET()
        if self.path == '/api/status':
            body = json.dumps(test_run_store.load(), ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith('/media/'):
            fname = os.path.basename(self.path)
            path = MEDIA_DIR / fname
            if path.exists():
                data = path.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_error(404)
            return
        return super().do_GET()

    def log_message(self, *a):
        pass


if __name__ == '__main__':
    os.makedirs(MEDIA_DIR, exist_ok=True)
    test_run_store.load()
    print(f'테스트 대시보드: http://localhost:{PORT}/')
    ThreadingHTTPServer(('', PORT), Handler).serve_forever()
