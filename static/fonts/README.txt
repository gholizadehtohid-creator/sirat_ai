VazirmatnFD-wght.woff -- the panel's UI font (design system v3, 2026-11-23).

Source:  Vazirmatn v33.003, https://github.com/rastikerdar/vazirmatn
         release asset vazirmatn-v33.003.zip
         sha256 0a9afd41967e6f57096a56a181a23f81a2b999b62f1f2a4e4b26736580854fdb
         file   fonts/variable/Vazirmatn[wght].ttf  (variable, wght 100-900)
License: SIL Open Font License 1.1 -- OFL.txt beside this file, copied from
         the release. No Reserved Font Name is declared.

The one change: the Unicode cmap maps the Latin digits U+0030-0039 to the
font's own Persian digit glyphs (U+06F0-06F9), and the family name reads
"Vazirmatn FD". The panel formats most numbers in Persian already; this keeps
the few it prints unformatted (chart ticks, codes such as E28) in Persian too,
as B Yekan did before. Saved as WOFF 1.0 (zlib) with fontTools 4.63.

Rebuild, in a Python with fontTools:

    from fontTools.ttLib import TTFont
    f = TTFont("Vazirmatn[wght].ttf")
    for t in f["cmap"].tables:
        if t.isUnicode():
            for i in range(10):
                if 0x06F0 + i in t.cmap:
                    t.cmap[0x30 + i] = t.cmap[0x06F0 + i]
    f.flavor = "woff"
    f.save("VazirmatnFD-wght.woff")
