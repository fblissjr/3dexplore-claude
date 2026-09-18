"""Upload API regression tests, independent of the HTTP listener."""
import io
import sys
from pathlib import Path
import unittest
from PIL import Image, ImageDraw
from x2d.validation import inspect_3mf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stock_capture import PROFILE  # noqa: E402
SETTINGS = __import__('base64').b64encode(Path(PROFILE).read_bytes()).decode()


class UploadTests(unittest.TestCase):
    def test_browser_payload_builds_downloadable_project(self):
        import base64
        from app import build
        img = Image.new('RGBA', (240, 180), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse((20, 20, 220, 160), fill=(222, 67, 67, 255))
        draw.rectangle((70, 60, 170, 120), fill=(255, 255, 255, 255))
        draw.ellipse((100, 75, 140, 105), fill=(0, 0, 0, 255))
        buf = io.BytesIO()
        img.save(buf, 'PNG')
        source = base64.b64encode(buf.getvalue()).decode()
        result = build({'file': source, 'name': 'Shapes & friends.png', 'width': 60,
                        'settings': SETTINGS})
        report = inspect_3mf(io.BytesIO(base64.b64decode(result['model'])))
        self.assertTrue(report['valid'], report['errors'])
        self.assertEqual(result['filename'], 'Shapes & friends_mosaic.3mf')
        with Image.open(io.BytesIO(base64.b64decode(result['preview']))) as preview:
            self.assertGreater(preview.width, 0)

    def test_upload_rejects_invalid_base64_and_palette(self):
        from app import build, decode_file
        with self.assertRaises(ValueError):
            decode_file('not a file!')
        with self.assertRaises(ValueError):
            build({'slots': [0], 'file': '', 'settings': SETTINGS})

if __name__ == '__main__':
    unittest.main()
