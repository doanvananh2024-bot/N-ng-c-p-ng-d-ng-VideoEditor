"""Load the supplied OFL font variant used by the visual reference."""
from pathlib import Path
import sys

_loaded=False
def font_directory():
    return Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))/'ui_fonts'

def font_path(family,bold=False):
    if str(family).casefold().strip()!='ve ui': return None
    path=font_directory()/('VEUI-Bold.ttf' if bold else 'VEUI-Regular.ttf')
    return str(path) if path.is_file() else None

def load_ui_fonts():
    global _loaded
    if _loaded: return
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase,QFont
    app=QApplication.instance()
    if app is None: return
    folder=font_directory()
    for filename in ('VEUI-Regular.ttf','VEUI-Bold.ttf','VEUI-Italic.ttf'):
        path=folder/filename
        if path.is_file(): QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont('VE UI',10))
    _loaded=True
