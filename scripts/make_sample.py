"""Draw a stand-in logo so the pipeline can be exercised end to end."""
import argparse, os
from PIL import Image, ImageDraw, ImageFont

from x2d.fonts import bold_font

ap = argparse.ArgumentParser()
ap.add_argument("--out", default="out/sample_logo.png")
a = ap.parse_args()

W = H = 1200
img = Image.new("L", (W, H), 255)
d = ImageDraw.Draw(img)
d.ellipse((40, 40, W - 40, H - 40), outline=0, width=70)          # ring
d.ellipse((150, 150, W - 150, H - 150), outline=0, width=18)      # thin inner ring (should trip the check)
font_path = bold_font()
f = ImageFont.truetype(font_path, 330)
d.text((W // 2, H // 2), "3D", font=f, fill=0, anchor="mm")
os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
img.save(a.out)
print("wrote", a.out, img.size, "font:", font_path)
