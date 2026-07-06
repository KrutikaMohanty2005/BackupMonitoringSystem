from PIL import Image, ImageDraw, ImageFont
import os

WIDTH = 900
HEIGHT = 220
BG = (255, 255, 255)
HEADER_BG = (248, 250, 252)
BORDER = (226, 232, 240)
TEXT_DARK = (30, 41, 59)
TEXT_GRAY = (100, 116, 139)
GREEN = (16, 185, 129)
BLUE_BG = (239, 246, 255)
BLUE_TEXT = (37, 99, 235)

img = Image.new('RGB', (WIDTH, HEIGHT), BG)
draw = ImageDraw.Draw(img)

try:
    font_title = ImageFont.truetype("arial.ttf", 16)
    font_th = ImageFont.truetype("arial.ttf", 12)
    font_td = ImageFont.truetype("arial.ttf", 12)
    font_badge = ImageFont.truetype("arial.ttf", 11)
    font_btn = ImageFont.truetype("arial.ttf", 11)
except:
    font_title = ImageFont.load_default()
    font_th = ImageFont.load_default()
    font_td = ImageFont.load_default()
    font_badge = ImageFont.load_default()
    font_btn = ImageFont.load_default()

# Card border
draw.rounded_rectangle([10, 10, WIDTH-10, HEIGHT-10], radius=12, outline=BORDER, width=1)

# Title
draw.text((30, 28), "All Backup History", fill=TEXT_DARK, font=font_title)

# Buttons
buttons = [("Select", (248,250,252)), ("Export CSV", (248,250,252)), ("Refresh", (248,250,252))]
bx = WIDTH - 40
for label, bg in reversed(buttons):
    tw = draw.textlength(label, font=font_btn)
    bw = int(tw) + 20
    bx -= bw + 6
    draw.rounded_rectangle([bx, 28, bx+bw, 52], radius=6, fill=bg, outline=BORDER)
    draw.text((bx+10, 32), label, fill=TEXT_DARK, font=font_btn)

# Table header
y = 70
draw.line([(30, y), (WIDTH-30, y)], fill=BORDER, width=2)
cols = [("Date & Time", 30), ("Type", 230), ("Location", 350), ("Status", 600), ("Action", 750)]
y += 8
for label, x in cols:
    draw.text((x, y), label, fill=TEXT_GRAY, font=font_th)

# Table row
y = 100
draw.line([(30, y), (WIDTH-30, y)], fill=BORDER, width=1)
y += 10

draw.text((30, y), "4/7/2026, 12:38:56 am", fill=TEXT_DARK, font=font_td)

# Type badge
badge_text = "Immediate"
bx = 230
draw.rounded_rectangle([bx, y-2, bx+75, y+16], radius=6, fill=BLUE_BG)
draw.text((bx+8, y+1), badge_text, fill=BLUE_TEXT, font=font_badge)

# Location
draw.text((350, y), "backup_ICARD_03072026_190856.sql.gz", fill=(71,85,105), font=font_td)

# Status
draw.text((600, y), "Completed", fill=GREEN, font=font_td)

# Delete button
draw.rounded_rectangle([750, y-4, 810, y+18], radius=6, fill=(239,68,68))
draw.text((762, y), "Delete", fill=(255,255,255), font=font_btn)

# Bottom border
y = 145
draw.line([(30, y), (WIDTH-30, y)], fill=BORDER, width=1)

os.makedirs("screenshots", exist_ok=True)
img.save("screenshots/backup-history-delete.png", "PNG")
print("Screenshot saved to screenshots/backup-history-delete.png")
