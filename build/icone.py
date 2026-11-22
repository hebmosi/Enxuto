from PIL import Image, ImageDraw
S = 1024
im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
d.rounded_rectangle((40, 40, S-40, S-40), radius=200, fill=(32, 92, 196, 255))
# fita de filme
d.rounded_rectangle((170, 300, 854, 724), radius=60, fill=(245, 247, 250, 255))
for i in range(6):
    x = 215 + i * 110
    d.rounded_rectangle((x, 330, x + 60, 380), radius=12, fill=(32, 92, 196, 255))
    d.rounded_rectangle((x, 644, x + 60, 694), radius=12, fill=(32, 92, 196, 255))
d.rectangle((215, 410, 809, 614), fill=(120, 170, 240, 255))
# corte (A e B)
d.rectangle((430, 250, 462, 774), fill=(226, 54, 54, 255))
d.rectangle((600, 250, 632, 774), fill=(226, 54, 54, 255))
d.polygon([(410, 250), (482, 250), (446, 300)], fill=(226, 54, 54, 255))
d.polygon([(580, 250), (652, 250), (616, 300)], fill=(226, 54, 54, 255))
im.save("icone.ico", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
im.resize((256,256), Image.LANCZOS).save("icone_preview.png")
