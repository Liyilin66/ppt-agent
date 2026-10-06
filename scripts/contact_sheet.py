"""Tile PNG slides into one overview image. Usage: contact_sheet.py out.png cols img..."""
import sys
from PIL import Image, ImageDraw
out, cols, files = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
ims = [Image.open(f).convert("RGB") for f in files]
w, h = ims[0].size
scale = 640 / w
tw, th = int(w * scale), int(h * scale)
rows = (len(ims) + cols - 1) // cols
pad = 16
sheet = Image.new("RGB", (cols * (tw + pad) + pad, rows * (th + pad) + pad), "#2A2D33")
for i, im in enumerate(ims):
    x = pad + (i % cols) * (tw + pad)
    y = pad + (i // cols) * (th + pad)
    sheet.paste(im.resize((tw, th), Image.LANCZOS), (x, y))
sheet.save(out)
