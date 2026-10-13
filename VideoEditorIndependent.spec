from pathlib import Path
import hashlib
import sys
from PyInstaller.config import CONF
from PyInstaller.utils.hooks import collect_all, collect_submodules

root = Path(SPECPATH)
runtime_root = Path(r'C:\Users\doanv\AppData\Local\Programs\Media Studio\MediaFusion\.venv\Lib\site-packages')
gpu_dependencies = root.parent / 'gpu-deps'
sys.path.insert(0, str(gpu_dependencies))
speech_dependencies=root.parent/'speech-deps'
sys.path.insert(0,str(speech_dependencies))
datas = [(str(root / 'tools'), 'tools'),(str(root / 'ui_fonts'),'ui_fonts'),(str(root / 'ui_icons'),'ui_icons')]
binaries = []
hiddenimports = collect_submodules('numpy.core', filter=lambda name: '.tests' not in name)
hiddenimports += ['desktop', 'dialogs', 'preview_canvas', 'voice_catalog', 'subtitle_editor', 'tts_providers', 'local_tts', 'gpu_runtime', 'studio_ui', 'render_pipeline', 'workflow',
                  'subtitle_tools', 'advanced_render', 'media_ai', 'providers', 'scenario_store']
for name in ['faster_whisper', 'ctranslate2', 'onnxruntime', 'tokenizers', 'av', 'edge_tts',
             'deep_translator', 'huggingface_hub', 'hf_xet', 'certifi', 'httpx2', 'httpcore2',
             'rapidocr_onnxruntime', 'easyocr', 'demucs', 'piper', 'gtts']:
    d, b, h = collect_all(name)
    datas += d
    binaries += b
    hiddenimports += h
binaries += [(str(runtime_root / 'PySide6' / 'vcomp140.dll'), '.')]
binaries += [(str(runtime_root / 'scipy' / '_cyutility.cp312-win_amd64.pyd'), 'scipy')]
a = Analysis([str(root / 'main.py')], pathex=[str(root), str(gpu_dependencies),str(speech_dependencies)], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, hookspath=[str(root / 'packaging_hooks')],
             hooksconfig={}, runtime_hooks=[],
             excludes=['tkinter', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
                       'PySide6.QtQml', 'PySide6.QtQuick'], noarchive=False)
# Compile our owned modules from the final files, independently of modulegraph
# snapshots. The content-named archive also invalidates PYZ when source changes.
owned_modules = ['desktop', 'dialogs', 'preview_canvas', 'voice_catalog', 'subtitle_editor', 'tts_providers', 'local_tts', 'gpu_runtime', 'app', 'studio_ui', 'engine', 'advanced_render',
                 'ai_tools', 'media_ai', 'workflow', 'subtitle_tools', 'render_pipeline',
                 'providers', 'scenario_store', 'reference_dialogs','source_composition','layer_blend','audio_layers',
                 'text_effects','color_filters','foreign_tts','online_speech','caption_raster',
                 'layer_groups','subtitle_detail','ui_theme']
code_cache = CONF['code_cache'].get(id(a.pure)) or {}
source_digest = hashlib.sha256()
for module_name in owned_modules:
    module_path = root / (module_name + '.py')
    source_bytes = module_path.read_bytes()
    source_digest.update(module_name.encode())
    source_digest.update(source_bytes)
    code_cache[module_name] = compile(source_bytes, str(module_path), 'exec')
CONF['code_cache'][id(a.pure)] = code_cache
pyz = PYZ(a.pure, name=str(Path(CONF['workpath']) / ('PYZ-' + source_digest.hexdigest()[:12] + '.pyz')))
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='VideoEditorIndependent',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, icon=str(root / 'app.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='VideoEditorIndependent')
