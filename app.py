#!/usr/bin/env python3
"""Local browser workbench. Run: python app.py (no UI framework required)."""
import argparse
import base64
import binascii
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
from urllib.parse import urlparse
import zipfile

from PIL import Image

from x2d import Machine, default_capture, project_settings, write_3mf
from x2d.mosaic import make_mosaic, video_frame
from x2d.report import filaments
from x2d.validation import inspect_3mf

ROOT = Path(__file__).resolve().parent
MAX_REQUEST = 140 * 1024 * 1024


def machine_info(settings):
    """The printer and the loaded filament, as data, for the browser.

    The viewer used to carry its own copies of these -- a 220 mm bed, 1.24
    g/cm3, a 45 degree overhang line, 20% infill -- and three of the four were
    wrong for this machine. A page that hardcodes a printer constant is the bug
    `machine.py` exists to prevent, so the page asks instead.
    """
    machine = Machine.from_settings(settings)
    return {
        'name': machine.name,
        'bed_mm': list(machine.bed),
        'nozzles': [{'id': e.id, 'feed': e.feed, 'diameter': e.dia,
                     'x_mm': list(e.x), 'y_mm': list(e.y), 'z_max_mm': e.z_max}
                    for e in machine.extruders],
        'layer_h_mm': machine.layer_h,
        'first_layer_h_mm': machine.first_layer_h,
        'line_width_mm': machine.line_width,
        'min_feature_mm': machine.min_feature,
        # From the capture, not from a constant. support_threshold_angle is
        # the slicer's own overhang line; sparse_infill_density is what it
        # will actually put inside the part.
        'overhang_deg': float(settings.get('support_threshold_angle', 30)),
        'infill': float(str(settings.get('sparse_infill_density', '15%')).rstrip('%')) / 100.0,
        'filaments': {s: {'slot': f.slot, 'colour': f.colour, 'kind': f.kind,
                          'density': f.density, 'cost_per_kg': f.cost_per_kg}
                      for s, f in filaments(settings).items()},
    }


def inspect_upload(data):
    """Preflight an uploaded .3mf exactly as verify.py would.

    The browser can read geometry out of a 3MF on its own; what it cannot do is
    tell you that part 2 prints in slot 4, or that two colours are a hairline
    apart. That lives in model_settings.config and in cross_check, so it comes
    from here.
    """
    blob = decode_file(data.get('file', ''))
    name = str(data.get('name', 'upload.3mf'))
    if not name.lower().endswith('.3mf'):
        raise ValueError('Preflight reads .3mf projects; an STL carries no part or slot metadata.')
    return {'name': name, 'report': inspect_3mf(io.BytesIO(blob))}


def decode_file(value):
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ValueError("Invalid uploaded file") from exc


def settings_for(data):
    if data.get('settings'):
        ref = io.BytesIO(decode_file(data['settings']))
    else:
        ref = default_capture(ROOT)
        if ref is None:
            raise ValueError('No printer capture yet. Choose a project saved from Bambu Studio '
                             'under "Settings project", or save one as profiles/default.3mf.')
    cfg = project_settings(ref)
    # Any printer the capture describes completely. What the mosaic needs
    # from it (layer height, line width, bed) is read, not assumed.
    Machine.from_settings(cfg)
    colors = cfg.get('filament_colour', [])
    if not colors:
        raise ValueError('The settings project has no filament colors.')
    return cfg


def palette_info(cfg):
    types = cfg.get('filament_type', [])
    return [{'slot': i + 1, 'color': color, 'material': types[i] if i < len(types) else '?'}
            for i, color in enumerate(cfg['filament_colour'])]


def image_for(data):
    raw = decode_file(data['file'])
    suffix = Path(data.get('name', 'image.png')).suffix.lower()
    if suffix in {'.mp4', '.mov', '.avi', '.mkv'}:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ('video' + suffix)
            path.write_bytes(raw)
            return video_frame(path, float(data.get('seconds', 0)))[0]
    with Image.open(io.BytesIO(raw)) as opened:
        opened.load()
        return opened.copy()


def build(data):
    cfg = settings_for(data)
    colors = cfg['filament_colour']
    slots = data.get('slots', list(range(1, len(colors) + 1)))
    if not isinstance(slots, list) or any(type(s) is not int or not 1 <= s <= len(colors) for s in slots):
        raise ValueError('Choose valid filament slots from your settings project.')
    machine = Machine.from_settings(cfg)
    options = {key: float(data.get(key, default)) for key, default in
               dict(width=90, cell=1.2, margin=3).items()}
    asked = lambda key: float(data[key]) if key in data else None
    options['base'], options['relief'] = machine.band(asked('base'), asked('relief'), default=(1.6, 0.6))
    result = make_mosaic(image_for(data), {s: colors[s-1] for s in slots},
                         base_slot=int(data.get('base_slot', 1)),
                         machine=machine, **options)
    output, preview = io.BytesIO(), io.BytesIO()
    name = Path(data.get('name', 'artwork')).stem
    write_3mf(result.parts, output, name=name, settings=cfg)
    # Same verdict verify.py would give, before anyone downloads it.
    verdict = inspect_3mf(io.BytesIO(output.getvalue()))
    if not verdict['valid']:
        raise ValueError('Project failed preflight: ' + '; '.join(verdict['errors']))
    result.preview.save(preview, format='PNG')
    return {'model': base64.b64encode(output.getvalue()).decode(),
            'preview': base64.b64encode(preview.getvalue()).decode(),
            'filename': name + '_mosaic.3mf', 'report': result.report}


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, body, mime='application/json'):
        if mime == 'application/json':
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def local_request(self):
        expected = f'127.0.0.1:{self.server.server_port}'
        localhost = f'localhost:{self.server.server_port}'
        if self.headers.get('Host') not in (expected, localhost):
            return False
        origin = self.headers.get('Origin')
        return origin is None or (urlparse(origin).scheme == 'http' and urlparse(origin).netloc in (expected, localhost))

    def do_GET(self):
        if not self.local_request():
            return self.respond(403, {'error': 'Local requests only.'})
        if self.path == '/':
            return self.respond(200, (ROOT / 'web/index.html').read_bytes(), 'text/html; charset=utf-8')
        if self.path == '/viewer':
            return self.respond(200, (ROOT / 'web/viewer.html').read_bytes(), 'text/html; charset=utf-8')
        if self.path in ('/api/machine', '/api/palette'):
            try:
                cfg = settings_for({})
            except ValueError as exc:
                # No capture saved: say so rather than answer for a printer
                # nobody here owns. The viewer shows its generic-numbers banner.
                return self.respond(409, {'error': str(exc)})
            info = machine_info if self.path == '/api/machine' else palette_info
            return self.respond(200, info(cfg))
        if self.path == '/api/sample':
            return self.respond(200, {'name': 'worried_guy.png', 'file': base64.b64encode((ROOT / 'samples/worried_guy.png').read_bytes()).decode()})
        self.respond(404, {'error': 'Not found'})

    def do_POST(self):
        if not self.local_request():
            return self.respond(403, {'error': 'Local requests only.'})
        if self.headers.get('Content-Type') != 'application/json':
            return self.respond(415, {'error': 'Expected JSON upload.'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= MAX_REQUEST:
                return self.respond(413, {'error': 'Upload is too large. Use a file under 100 MB.'})
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError('Expected an upload object.')
            if self.path == '/api/palette':
                result = palette_info(settings_for(data))
            elif self.path == '/api/machine':
                result = machine_info(settings_for(data))
            elif self.path == '/api/build':
                result = build(data)
            elif self.path == '/api/inspect':
                result = inspect_upload(data)
            else:
                return self.respond(404, {'error': 'Not found'})
            self.respond(200, result)
        except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile) as exc:
            self.respond(400, {'error': str(exc)})
        except Exception as exc:
            # A malformed upload can fail anywhere in the geometry stack (cv2,
            # trimesh, shapely). Answer with what broke rather than closing the
            # socket and leaving the page to guess.
            self.respond(500, {'error': f'{type(exc).__name__}: {exc}'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'3dexplore-claude workbench ready at http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
