"""Load the supplied OFL font variant used by the visual reference."""
from pathlib import Path
import sys

_loaded=False
_family='Be Vietnam Pro'

def ui_font_family():
    return _family
def font_directory():
    return Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))/'ui_fonts'

def font_path(family,bold=False):
    if str(family).casefold().strip() not in ('ve ui', 'be vietnam pro'): return None
    suffix='Bold.ttf' if bold else 'Regular.ttf'
    for prefix in ('VEUI-', 'BeVietnamPro-'):
        path=font_directory()/(prefix+suffix)
        if path.is_file(): return str(path)
    return None

def load_ui_fonts():
    global _loaded, _family
    if _loaded: return
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase,QFont
    app=QApplication.instance()
    if app is None: return
    folder=font_directory()
    families=[]
    for prefix in ('BeVietnamPro-', 'VEUI-'):
        for suffix in ('Regular.ttf', 'Bold.ttf', 'Italic.ttf'):
            path=folder/(prefix+suffix)
            if path.is_file():
                font_id=QFontDatabase.addApplicationFont(str(path))
                families.extend(QFontDatabase.applicationFontFamilies(font_id))
    _family='VE UI' if 'VE UI' in families else 'Be Vietnam Pro'
    app.setFont(QFont(_family,10))
    _loaded=True
