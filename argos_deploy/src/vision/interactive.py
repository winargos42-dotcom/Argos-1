"""Explicit one-frame vision. No cloud fallback, implicit capture or model pull."""
import base64
import http.client
import io
import ipaddress
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

MAX_IMAGE = 8 * 1024 * 1024
MAX_RESPONSE = 1024 * 1024


class LocalAnalyzer:
    def __init__(self, host=None, model=None):
        url = urlsplit(host or os.getenv('OLLAMA_HOST', 'http://127.0.0.1:11434'))
        hostname = url.hostname
        if hostname == 'localhost':
            hostname = '127.0.0.1'
        try:
            local = ipaddress.ip_address(hostname).is_loopback
        except (ValueError, TypeError):
            local = False
        if (not local or url.scheme != 'http' or url.username is not None
                or url.password is not None or url.path not in ('', '/')
                or url.query or url.fragment):
            raise ValueError('vision_requires_loopback_ollama')
        self.host, self.port = hostname, url.port or 11434
        self.model = model or os.getenv('ARGOS_VISION_MODEL', 'qwen3.5:latest')

    def _request(self, path, payload):
        # Direct HTTPConnection ignores proxy environment and never follows redirects.
        connection = http.client.HTTPConnection(self.host, self.port, timeout=30)
        deadline = time.monotonic() + 30
        def remaining():
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise TimeoutError('local_vision_deadline')
            return seconds
        try:
            connection.connect()
            transport = connection.sock
            transport.settimeout(remaining())
            connection.request('POST', path, json.dumps(payload).encode(),
                               {'Content-Type': 'application/json'})
            transport.settimeout(remaining())
            response = connection.getresponse()
            if response.status != 200:
                raise RuntimeError('local_vision_http_error')
            body = bytearray()
            while True:
                transport.settimeout(remaining())
                chunk = response.read1(min(65536, MAX_RESPONSE + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > MAX_RESPONSE:
                    raise RuntimeError('local_vision_response_too_large')
            return json.loads(body)
        finally:
            connection.close()

    def ensure_available(self):
        data = self._request('/api/show', {'model': self.model})
        if not isinstance(data, dict) or 'vision' not in data.get('capabilities', []):
            raise RuntimeError('vision_model_unavailable')

    def analyze(self, image, question):
        if not image or len(image) > MAX_IMAGE:
            raise ValueError('invalid_image_size')
        data = self._request('/api/generate', {
            'model': self.model, 'prompt': question[:4096],
            'images': [base64.b64encode(image).decode('ascii')], 'stream': False, 'think': False,
            'keep_alive': '60s', 'options': {'num_predict': 256, 'num_ctx': 2048},
        })
        answer = data.get('response') if isinstance(data, dict) else None
        if not isinstance(answer, str) or not answer.strip():
            raise RuntimeError('local_vision_empty_response')
        return answer.strip()


class ArgosVision:
    def __init__(self, api_key=None, *, analyzer=None):
        # api_key retained for call compatibility; intentionally never used.
        self.analyzer = analyzer if analyzer is not None else LocalAnalyzer()

    @staticmethod
    def _encode(image):
        image = image.convert('RGB')
        image.thumbnail((1024, 1024))
        output = io.BytesIO()
        image.save(output, format='JPEG', quality=85)
        result = output.getvalue()
        if len(result) > MAX_IMAGE:
            raise ValueError('invalid_image_size')
        return result

    @staticmethod
    def _capture_worker(kind, camera_index=0):
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), '--capture', kind, str(camera_index)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=8, check=True,
        )
        if not result.stdout or len(result.stdout) > MAX_IMAGE:
            raise ValueError('invalid_capture_size')
        return result.stdout

    def _capture_screen(self):
        return self._capture_worker('screen')

    def _capture_screen_direct(self):
        import mss
        from PIL import Image
        with mss.mss() as capture:
            shot = capture.grab(capture.monitors[1])
            return self._encode(Image.frombytes('RGB', shot.size, shot.rgb))

    def _capture_camera(self, camera_index):
        if type(camera_index) is not int or not 0 <= camera_index <= 63:
            raise ValueError('invalid_camera_index')
        return self._capture_worker('camera', camera_index)

    def _capture_camera_direct(self, camera_index):
        if type(camera_index) is not int or not 0 <= camera_index <= 63:
            raise ValueError('invalid_camera_index')
        import cv2
        camera = cv2.VideoCapture(camera_index)
        try:
            if not camera.isOpened():
                raise RuntimeError('camera_unavailable')
            success, frame = camera.read()
            if not success:
                raise RuntimeError('camera_frame_unavailable')
            height, width = frame.shape[:2]
            if max(height, width) > 1024:
                scale = 1024 / max(height, width)
                frame = cv2.resize(frame, (max(1, int(width * scale)), max(1, int(height * scale))))
            success, encoded = cv2.imencode('.jpg', frame)
            if not success:
                raise RuntimeError('camera_encode_failed')
            image = encoded.tobytes()
            if len(image) > MAX_IMAGE:
                raise ValueError('invalid_image_size')
            return image
        finally:
            camera.release()

    def _describe(self, capture, question):
        try:
            self.analyzer.ensure_available()
            return '👁️ VISION АНАЛИЗ:\n' + self.analyzer.analyze(capture(), question)
        except ImportError:
            return '❌ Vision: отсутствует локальная зависимость захвата (mss/Pillow/OpenCV).'
        except Exception as error:
            # Never expose provider response, image bytes, paths or environment values.
            reason = 'vision_model_unavailable' if str(error) == 'vision_model_unavailable' else type(error).__name__
            return '❌ Локальное зрение недоступно: ' + reason

    def look_at_screen(self, question='Что происходит на экране?'):
        return self._describe(self._capture_screen, question)

    def look_through_camera(self, question='Что ты видишь?', camera_index=0):
        return self._describe(lambda: self._capture_camera(camera_index), question)

    def analyze_image(self, image_path, question='Опиши изображение.'):
        def read():
            from PIL import Image
            path = Path(image_path)
            if not path.is_file() or path.stat().st_size > MAX_IMAGE:
                raise ValueError('invalid_image_file')
            with path.open('rb') as source:
                raw = source.read(MAX_IMAGE + 1)
            if len(raw) > MAX_IMAGE:
                raise ValueError('invalid_image_size')
            with Image.open(io.BytesIO(raw)) as image:
                if image.width * image.height > 20_000_000:
                    raise ValueError('image_dimensions_too_large')
                return self._encode(image)
        return self._describe(read, question)

    def analyze_file(self, path):
        return self.analyze_image(path)


if __name__ == '__main__':
    # Private bounded capture worker: never performs inference or emits diagnostics.
    try:
        if len(sys.argv) != 4 or sys.argv[1] != '--capture':
            raise ValueError('invalid_worker_arguments')
        vision = ArgosVision.__new__(ArgosVision)
        if sys.argv[2] == 'screen':
            data = vision._capture_screen_direct()
        elif sys.argv[2] == 'camera':
            data = vision._capture_camera_direct(int(sys.argv[3]))
        else:
            raise ValueError('invalid_capture_kind')
        sys.stdout.buffer.write(data)
    except Exception:
        sys.exit(1)
