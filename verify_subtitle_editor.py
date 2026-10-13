"""Targeted, offline integration verification of the new subtitle modal."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback
import uuid
from unittest.mock import patch


ROOT=Path(__file__).resolve().parent


def require(value,message):
    if not value:
        raise AssertionError(message)


def main():
    run=ROOT / "qa" / ("subtitles_"+datetime.now().strftime("%Y%m%d_%H%M%S")+"_"+uuid.uuid4().hex[:6])
    run.mkdir(parents=True,exist_ok=False)
    os.environ.update(LOCALAPPDATA=str(run/"local-data"),APPDATA=str(run/"roaming-data"),
                      HF_HOME=str(run/"hf-cache"),TORCH_HOME=str(run/"torch-cache"),
                      HF_HUB_OFFLINE="1",TRANSFORMERS_OFFLINE="1",QT_QPA_PLATFORM="offscreen")
    from PySide6.QtCore import QCoreApplication,QEvent,QItemSelectionModel,QSettings,Qt
    from PySide6.QtGui import QFont,QFontDatabase
    from PySide6.QtTest import QSignalSpy,QTest
    from PySide6.QtWidgets import QApplication,QMessageBox
    import ai_tools
    import engine
    import subtitle_tools
    from desktop import IndependentWindow
    from subtitle_editor import SubtitleEditorDialog

    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat,QSettings.UserScope,str(run/"qt-settings"))
    app=QApplication.instance() or QApplication([])
    app.setApplicationName("VideoEditorIndependentSubtitleQA")
    app.setOrganizationName("VideoEditorIndependentSubtitleQA")
    app.setStyle("Fusion")
    for name in ("segoeui.ttf","segoeuib.ttf","segoeuii.ttf","seguisb.ttf","seguisym.ttf","seguiemj.ttf"):
        path=Path(os.environ.get("WINDIR","C:/Windows"))/"Fonts"/name
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont("Segoe UI",10))
    report=dict(phase="subtitle-modal",created_at=datetime.now(timezone.utc).isoformat(),run_dir=str(run),offline=True,checks=[],artifacts={})
    errors=[]
    notifications=[]
    old_hook=sys.excepthook
    sys.excepthook=lambda kind,value,trace:errors.append("".join(traceback.format_exception(kind,value,trace)))
    window=None
    dialog=None
    original=[dict(start=.1,end=1.2,text="Xin chào các bạn.",voice="vi-VN-HoaiMyNeural"),
              dict(start=1.3,end=2.4,text="Đây là câu thứ hai.",voice="vi-VN-NamMinhNeural"),
              dict(start=2.5,end=3.8,text="Hẹn gặp lại ngày mai.")]
    translated=[dict(row,text=text) for row,text in zip(original,("Hello everyone.","This is the second sentence.","See you tomorrow."))]
    source=run/"Video thử phụ đề có dấu.mp4"
    orig_path=run/"Phụ đề gốc.srt"
    trans_path=run/"Phụ đề dịch.srt"

    def persist():
        report["passed"]=sum(row["status"]=="passed" for row in report["checks"])
        report["failed"]=sum(row["status"]=="failed" for row in report["checks"])
        report["status"]="failed" if report["failed"] else "passed"
        data=json.dumps(report,ensure_ascii=False,indent=2)
        (ROOT/"qa"/"subtitle-editor-verification-report.json").write_text(data,encoding="utf-8")
        (run/"subtitle-editor-verification-report.json").write_text(data,encoding="utf-8")

    def settle():
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        app.processEvents()
        QTest.qWait(20)

    def wait_for(predicate,title,timeout=15):
        limit=time.monotonic()+timeout
        while not predicate():
            require(time.monotonic()<limit,"Timed out: "+title)
            app.processEvents()
            QTest.qWait(15)
        settle()
        require(not errors,"Qt slot errors: "+"\n".join(errors))

    def check(name,fn):
        started=time.monotonic()
        row=dict(name=name)
        report["checks"].append(row)
        try:
            details=fn()
            require(not errors,"Qt slot errors: "+"\n".join(errors))
            row.update(status="passed",details=details or {})
        except Exception as exc:
            row.update(status="failed",error=str(exc),traceback=traceback.format_exc())
        row["seconds"]=round(time.monotonic()-started,3)
        print(json.dumps({key:row[key] for key in ("name","status","seconds")},ensure_ascii=False),flush=True)
        persist()

    def select_rows(indices):
        dialog.table.clearSelection()
        for index in indices:
            dialog.table.selectionModel().select(dialog.table.model().index(index,0),
                QItemSelectionModel.SelectionFlag.Select|QItemSelectionModel.SelectionFlag.Rows)

    def fixture_and_modal():
        nonlocal window,dialog
        result=subprocess.run([engine.tool_path("ffmpeg"),"-hide_banner","-loglevel","error","-nostdin","-n",
            "-f","lavfi","-i","color=c=0x101010:size=320x180:rate=15:duration=4",
            "-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p",str(source)],
            stdin=subprocess.DEVNULL,capture_output=True,timeout=30,creationflags=engine._creation_flags())
        require(result.returncode==0,result.stderr.decode("utf-8","replace"))
        subtitle_tools.save_subtitles(original,orig_path)
        subtitle_tools.save_subtitles(translated,trans_path)
        window=IndependentWindow(testing=True)
        window.info=lambda title,text:notifications.append(dict(title=title,text=text))
        window.show()
        window.ingest([str(source)])
        wait_for(lambda: len(window.library)==1 and not window.workers,"source import")
        media=engine.probe(str(source))
        dialog=SubtitleEditorDialog(window,media,window.settings(),dict(original_subtitle=str(orig_path),translated_subtitle=str(trans_path)))
        dialog.show()
        settle()
        require(dialog.rows==original and dialog.table.rowCount()==3,"Original subtitle cues did not load into timed table")
        require(dialog.table.cellWidget(0,3).currentText()==original[0]["voice"],"Per-cue voice failed to load")
        screenshot=run/"subtitle-editor-modal.png"
        dialog.grab().save(str(screenshot))
        report["artifacts"].update(source=str(source),original=str(orig_path),translated=str(trans_path),modal_screenshot=str(screenshot))
        return dict(cues=3,voice_sidecar_loaded=True,screenshot=str(screenshot))

    def bilingual_roles():
        dialog.roles.setCurrentIndex(dialog.roles.findData("translated"))
        require(dialog.rows==translated,"Translated role does not display its own document")
        dialog.roles.setCurrentIndex(dialog.roles.findData("bilingual"))
        expected=subtitle_tools.bilingual_rows(original,translated)
        require(dialog.rows==expected,"Bilingual role discarded or reordered source/translated text")
        require(dialog.preview._subtitles==expected,"Bilingual cues did not update the preview")
        dialog.roles.setCurrentIndex(dialog.roles.findData("original"))
        # A derived bilingual view must include text edited since the prior visit.
        dialog.table.item(0,2).setText("Xin chào đã cập nhật.")
        updated=copy.deepcopy(dialog.rows)
        dialog.roles.setCurrentIndex(dialog.roles.findData("bilingual"))
        revisited=copy.deepcopy(dialog.rows)
        dialog.roles.setCurrentIndex(dialog.roles.findData("original"))
        dialog.undo()
        require(dialog.rows==original,"Undo test source edit did not restore the original role")
        require(revisited==subtitle_tools.bilingual_rows(updated,translated),
                "Revisiting bilingual shows stale text after an original-cue edit")
        return dict(original=3,translated=3,bilingual=3,first_bilingual=expected[0]["text"])

    def editing_operations():
        dialog.table.item(0,2).setText("Xin chào những người bạn.")
        dialog.table.item(0,0).setText("0.200")
        dialog.table.cellWidget(0,3).setCurrentText("vi-VN-NamMinhNeural")
        require(dialog.rows[0]["start"]==.2 and dialog.rows[0]["voice"]=="vi-VN-NamMinhNeural","Table timing/voice edits did not update the cue")
        edited=copy.deepcopy(dialog.rows)
        select_rows([0])
        dialog.split_cue()
        require(len(dialog.rows)==4 and dialog.rows[0]["end"]==dialog.rows[1]["start"],"Split did not create adjacent timed cues")
        require(dialog.rows[0]["voice"]==dialog.rows[1]["voice"]=="vi-VN-NamMinhNeural","Split lost voice metadata")
        split=copy.deepcopy(dialog.rows)
        select_rows([0,1])
        dialog.merge_cues()
        require(len(dialog.rows)==3 and dialog.rows[0]["start"]==.2 and dialog.rows[0]["end"]==1.2,"Merge changed the combined cue timing")
        dialog.undo()
        require(dialog.rows==split,"Undo merge did not restore split cues")
        dialog.undo()
        require(dialog.rows==edited,"Undo split did not restore edited original")
        dialog.find.setText("người bạn")
        dialog.replace.setText("khán giả")
        dialog.whole_word.setChecked(True)
        dialog.replace_cues()
        require(dialog.rows[0]["text"]=="Xin chào những khán giả.","Unicode whole-word replacement did not update text")
        dialog.undo()
        require(dialog.rows==edited,"Undo replace lost timing or voice edits")
        select_rows([1])
        dialog.shift_value.setValue(.1)
        dialog.shift_cues()
        require(abs(dialog.rows[1]["start"]-1.4)<.001 and dialog.rows[0]["start"]==.2,"Selected timing shift changed unintended cue")
        dialog.undo()
        require(dialog.rows==edited,"Undo timing shift failed")
        require(subtitle_tools.load_subtitles(orig_path)==original,"Editing mutated the imported original file")
        return dict(text=edited[0]["text"],start=edited[0]["start"],voice=edited[0]["voice"],split_merge_undo_replace_shift=True)

    def save_and_invalid_timing():
        spy=QSignalSpy(dialog.saved)
        expected=copy.deepcopy(dialog.rows)
        srt=run/"Đã sửa và chọn giọng.srt"
        vtt=run/"Đã sửa và chọn giọng.vtt"
        dialog.save_to(srt)
        dialog.save_to(vtt)
        require(subtitle_tools.load_subtitles(srt)==expected and subtitle_tools.load_subtitles(vtt)==expected,"Saved SRT/VTT failed text/timing/voice round-trip")
        require(spy.count()==2,"Saving did not notify the host of both files")
        require(Path(str(srt)+".metadata.json").is_file() and Path(str(vtt)+".metadata.json").is_file(),"Saved per-cue voices have no metadata sidecar")
        dialog.table.item(0,0).setText("99.000")
        require(dialog.rows==expected,"Invalid intermediate timing should not enter accepted document")
        invalid=run/"Không được lưu.srt"
        rejected=False
        try:
            dialog.save_to(invalid)
        except ValueError:
            rejected=True
        require(rejected and not invalid.exists(),"Save accepted invalid start >= end timing")
        dialog.table.item(0,0).setText(f'{expected[0]["start"]:.3f}')
        settle()
        report["artifacts"].update(edited_srt=str(srt),edited_vtt=str(vtt))
        return dict(srt=str(srt),vtt=str(vtt),cue_voice_metadata=True,invalid_timing_rejected=True)

    def preview_seek():
        dialog.seek_to(0)
        settle()
        before=dialog.preview.grab().toImage()
        dialog.seek_cue(1)
        settle()
        expected=dialog.rows[1]["start"]
        require(abs(dialog.preview.position-expected)<.002,"Cue seek did not move preview to cue start")
        require(dialog.preview._subtitles==dialog.rows,"Preview shows stale subtitle rows after edits")
        active=next(row for row in dialog.preview._subtitles if row["start"]<=dialog.preview.position<row["end"])
        require(active["text"]==dialog.rows[1]["text"],"Seeking displays incorrect active cue")
        after=dialog.preview.grab().toImage()
        require(before!=after,"Preview pixels did not change from blank timeline position to an active cue")
        screenshot=run/"subtitle-editor-active-cue.png"
        dialog.grab().save(str(screenshot))
        report["artifacts"]["active_cue_screenshot"]=str(screenshot)
        return dict(seconds=expected,active_text=active["text"],preview_pixels_changed=True)

    def worker_cancel_and_close():
        started=threading.Event()
        stopped=threading.Event()
        callbacks=[]
        def local_cancellable(progress,cancel):
            started.set()
            while not cancel.wait(.02):
                pass
            stopped.set()
            raise ai_tools.CancelledError("QA operation cancelled")
        dialog.start_task("Local QA cancellable operation",local_cancellable,lambda result:callbacks.append(result))
        wait_for(lambda: started.is_set() and dialog.worker is not None,"modal worker starts")
        with patch.object(QMessageBox,"question",return_value=QMessageBox.StandardButton.Discard):
            dialog.close()
            wait_for(lambda: dialog.worker is None and not dialog.isVisible(),"modal worker cancelled before close")
        require(stopped.is_set() and not callbacks,"Modal close did not cancel its worker or applied its result")
        require(not window.workers,"Host retained running workers after modal close")
        return dict(cancelled=True,callback_suppressed=True,clean_close=True,network_calls=0)

    try:
        check("Subtitle modal loads Unicode source/translated cues and voice metadata",fixture_and_modal)
        if dialog is not None:
            check("Original/translated/bilingual role documents and preview",bilingual_roles)
            check("Text/timing/voice edits, split/merge/undo/replace and selected shift",editing_operations)
            check("Save SRT/VTT with voice sidecars; invalid timing rejects save",save_and_invalid_timing)
            check("Seeking a cue updates preview time, active text and pixels",preview_seek)
            check("Modal cancellation cleans worker and safely closes",worker_cancel_and_close)
    finally:
        if dialog is not None and dialog.isVisible():
            with patch.object(QMessageBox,"question",return_value=QMessageBox.StandardButton.Discard):
                dialog.close()
            wait_for(lambda: dialog.worker is None,"modal shutdown")
        if window is not None:
            wait_for(lambda:not window.workers,"host shutdown")
            window.dirty=False
            window.close()
            settle()
        sys.excepthook=old_hook
        report["notifications"]=notifications
        report["qt_exceptions"]=errors
        persist()
    print(json.dumps(dict(report=str(ROOT/"qa"/"subtitle-editor-verification-report.json"),passed=report["passed"],failed=report["failed"]),ensure_ascii=False))
    return 1 if report["failed"] else 0


if __name__=="__main__":
    raise SystemExit(main())
