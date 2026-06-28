from __future__ import annotations

_FOOD_KEYWORDS = [
    "суп", "салат", "борщ", "картоф", "куриц", "котлет", "сок",
    "кола", "сырник", "трубоч", "чизбургер", "шаурм", "гарнир",
    "второе", "напит", "сладк", "десерт", "блюд",
]


def _count_cyrillic(text: str) -> int:
    return sum(1 for c in text if "Ѐ" <= c <= "ӿ")


def _score_text(text: str) -> int:
    t = text.lower()
    return _count_cyrillic(text) + sum(10 for kw in _FOOD_KEYWORDS if kw in t)


def _fix_exif_orientation(img: "Image.Image") -> "Image.Image":
    from PIL import ExifTags, Image
    try:
        exif = img._getexif()
        if not exif:
            return img
        for tag, val in exif.items():
            if ExifTags.TAGS.get(tag) == "Orientation":
                ops = {3: Image.ROTATE_180, 6: Image.ROTATE_270, 8: Image.ROTATE_90}
                if val in ops:
                    return img.transpose(ops[val])
                break
    except Exception:
        pass
    return img


def _preprocess(img: "Image.Image") -> "Image.Image":
    from PIL import Image, ImageFilter, ImageOps
    img = _fix_exif_orientation(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    if min(w, h) < 1200:
        scale = max(2, 1200 // max(min(w, h), 1))
        img = img.resize((w * scale, h * scale), Image.LANCZOS)
    img = img.convert("L")
    img = ImageOps.autocontrast(img, cutoff=1)
    img = img.filter(ImageFilter.SHARPEN)
    return img


def _threshold(img_gray: "Image.Image") -> "Image.Image":
    return img_gray.point(lambda x: 255 if x > 128 else 0, "L")


def ocr_image_to_text(image_path: str, lang: str = "rus+eng", psm: int = 6) -> dict:
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return {"ok": False, "text": "", "error": "ocr_dependency_missing"}

    available: list[str] = []
    try:
        available = pytesseract.get_languages(config="")
    except Exception:
        pass

    requested = [p.strip() for p in lang.replace("+", " ").split() if p.strip()]
    for rl in requested:
        if rl == "rus" and available and "rus" not in available:
            return {
                "ok": False,
                "text": "",
                "error": "ocr_language_missing",
                "availableLanguages": available,
                "message": (
                    "В Tesseract не установлен русский язык (rus). "
                    "Установите rus.traineddata в tessdata."
                ),
            }

    try:
        img = Image.open(image_path)
    except Exception as e:
        return {"ok": False, "text": "", "error": str(e)}

    ocr_cfg = f"--oem 3 --psm {psm} -c preserve_interword_spaces=1"

    try:
        img_gray = _preprocess(img)
    except Exception:
        img_gray = img.convert("L")

    candidates: list[str] = []

    # Variant 1: autocontrast + sharpen
    try:
        t = pytesseract.image_to_string(img_gray, lang=lang, config=ocr_cfg)
        candidates.append(t)
    except Exception as e:
        err = str(e)
        if "tesseract" in err.lower() or "not found" in err.lower():
            return {"ok": False, "text": "", "error": "tesseract_not_installed"}
        return {"ok": False, "text": "", "error": err}

    # Variant 2: threshold binarise
    try:
        t2 = pytesseract.image_to_string(_threshold(img_gray), lang=lang, config=ocr_cfg)
        candidates.append(t2)
    except Exception:
        pass

    best = max(candidates, key=_score_text) if candidates else ""

    warnings: list[dict] = []
    if _count_cyrillic(best) < 20 or not [ln for ln in best.splitlines() if ln.strip()]:
        warnings.append({
            "code": "ocr_low_quality",
            "message": (
                "Текст распознан плохо. Проверьте, установлен ли русский язык Tesseract "
                "и качество фото."
            ),
        })

    return {
        "ok": True,
        "text": best,
        "error": None,
        "warnings": warnings,
        "availableLanguages": available,
    }
