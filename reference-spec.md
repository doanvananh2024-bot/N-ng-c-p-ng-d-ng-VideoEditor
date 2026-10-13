# Independent Video Editor reference specification

Inspected read-only installation: `C:\Users\doanv\AppData\Local\Programs\VideoEditor`.
This document describes observable layout and user-facing behavior. It contains no copied application code or assets. Authentication, licensing, accounts, bundled models, icons, fonts and server integrations belong to the reference installation and are not part of the independent implementation.

## Main screen

- Native maximized desktop window, nominal design size 1600 × 920. Default title shown by reference: Video Editor.
- 8 px outer padding and 8 px separation between top bar, content and bottom bar.
- Top bar spans width. Left actions in order: settings, translation scenarios, cut/join tools, downloads, terms. Right area: version indicator, VI/EN compact switch, account/status badge, account button. Independent edition should use its own local status/version and identity.
- Main area: a large media grid on the left and a fixed 300 px sidebar on the right, separated by 8 px. Both are rounded dark panels.
- Sidebar has a 56 px tab strip: Pipeline, Thành phần, Tinh chỉnh video. The content below scrolls and leaves approximately 12 px bottom clearance.
- Pipeline has four segmented subtabs with approximately 10 px margin: Phụ đề, Dịch thuật, Lồng tiếng, Hệ thống.
- Empty media grid shows a centered muted folder symbol with a short invitation to drop video or use Thêm video.
- Grid layout defaults to two columns. User can select 1/2/3/4 columns. Page-size choices: 10/20/30/50/100, default 20. Previous/next and page counter are shown only when multiple pages exist.
- Grid has 10 px internal padding and about 12 px cell separation. Preview region is landscape by default; card height scales with width, with roughly 96 px reserved for card chrome and controls. Videos retain their real aspect within the preview.
- Bottom bar: Thêm video, separator, Trích phụ đề, Dịch, Lồng tiếng, Xuất (selected count), Dừng; progress summary stretches toward the right; separator, page navigation, Mỗi trang, Layout. Progress is per stage, with counts for source subtitles, translation, voice and export, plus error count.

## Theme and control geometry

- Reference style is near-black/dark-gray with bright cyan primary emphasis, green success, blue information, red danger, warm warning, white main text and several muted gray levels. Exact dynamic palette values were not available in the readable UI definitions; the root agent should sample its reference screenshot before claiming exact colors.
- Explicit visible preview/workspace gray is approximately #2A2A2A; idle folder symbol gray approximately #3B3B3B. Section backgrounds are slightly lighter than their parents; dark section parent described around #1A1A1A.
- Fine 1 px gray borders; focused/current video card uses a 2 px accent border; video cards have 4 px corner radius. Larger containers use softer, larger radii.
- Buttons have small rounded corners in actual definitions. Typical height tiers are about 30/38/44 px. Medium horizontal padding about 18 px. Primary buttons are filled cyan, secondary outlined accent, ordinary chrome/close buttons transparent with gray outline, destructive actions red.
- Typography: compact readable UI text, approximately 11 px status/caption, 12–14 px ordinary labels/body, slightly larger bold section/card titles. Use an independently available system font, e.g. Segoe UI.
- Numeric controls combine caption, value, horizontal slider and optional unit. Combo boxes use dark fills, quiet border and compact chevron. Switches are compact rounded tracks. Hover has a subtle lighter gray fill.
- Modal dimming overlay is black at 70% opacity. Dialog is centered, with thin border and rounded corners, about 16 px outer padding and 20 px body padding. Title row, divider, segmented tabs, scrollable content, then cyan save and outlined close/cancel buttons. Use independently authored dialog sizes/content.

## Video card

- Header includes selection checkbox, elided filename with full-name tooltip, optional completion badges for original subtitles, translated subtitles and generated voice, stage-status line and active-stage percent/progress bar, and a remove button.
- Clicking card makes it the current card; selection checkbox separately controls which cards are processed. Right-click opens grouped actions.
- Preview supports source video playback, pause/play, scrub, fullscreen, active-card space shortcut. Thumbnail/poster is displayed before playback. Aspect selections: source, 9:16, 16:9, 1:1, 4:5, 3:4, 4:3. It supports editable overlays for subtitles, text, images, blur regions and source-video positioning.
- Card footer contains transport controls and ratio combo, plus small Apply to all action for layer settings. Applying settings should keep each video's media and subtitle content independent.
- Context menu: open source folder, open output folder, subtitle editor, play output, import original/translated SRT, copy original/translated subtitles, select all/none/errors, clear selected outputs, remove selected, remove all.
- Drag/drop accepts common video formats: MP4, MKV, AVI, MOV, WEBM; file picker also includes FLV and TS. Drop feedback overlays the grid in translucent accent color.

## Pipeline controls

### Subtitles

- Header toggle enables subtitle extraction.
- Extraction method selector distinguishing voice transcription and on-screen text/OCR; source-language selector.
- Parallel extraction slider 1–12 only for methods supporting it.
- Subtitle lead slider 0–0.5 seconds in 0.05-second steps.
- Subtitle typography belongs in per-video layer properties, not a duplicate global style form.

### Translation

- Header toggle. Controls are disabled when source extraction is disabled; a short hint states why.
- Target language, scenario/prompt preset, provider/engine and model selectors.
- Optional frame/image context checkbox.
- Request batch size 5–500 in steps of 5; parallel requests 1–5.

### Voice

- Header toggle; voice-picker row displaying selected voice and engine.
- TTS worker slider with engine-dependent upper limit; explanation for serial-only engines.
- Separate original-video section: source-vocal separation mode, basic/detailed separation level, workers 1–12, original-video speed 0.5–2.0 × in steps of 0.05.
- Separate generated-voice section: timing-fit mode off / fit individually / uniform speed. Uniform voice speed 0.5–2.0 ×; maximum fitted speed 1.0–2.0 ×; lead offset −1000 to 3000 ms; pronunciation-replacement table selector.

### System/export

- Parallel video jobs, voice jobs and renders: each 1–8.
- Container MP4/MOV; resolution source/1080p/720p/480p; speed fast/balanced/high quality; device automatic/NVIDIA/Intel/AMD/CPU; codec automatic/H.264/H.265; FPS automatic/24/30/60.
- Quality presets standard/balanced/maximum/custom. Advanced panel collapsed by default: rate control CRF/VBR/CBR, quality 14–32 when CRF, max bitrate 0–50 Mbps when VBR/CBR (0 means automatic), B-frames switch.
- Output folder is in general settings. Main export button requires selected cards, output folder and any needed engine to be configured. Stop is enabled while jobs run.

## Layer properties and tuning

- Properties area offers Layers / Details views; empty state explains to add video or select a layer.
- Layer groups: original video, text, media, blur, filter, audio. Add actions for text/media/blur/filter/audio. Layer deletion action in details.
- Core details: canvas ratio; normalized transform position, uniform/non-uniform scale, rotation, flip horizontal/vertical; opacity and blend mode; original audio volume; background fill black/blur/color; timing/trim.
- Text: editable content, font, font size/color, multiple outline/shadow/glow presets, optional background color/opacity/corner radius, subtitle position, list of subtitle cues, start/end display time.
- Blur: drag/resize region in preview; intensity; rectangle/ellipse; corner radius; edge feather.
- Media: select image/GIF, size, opacity, loop, and 3 × 3 anchor choices.
- Audio: source-file picker, volume, looping, fade in/out, enabled state.
- Filters include vignette, vintage, grain, light leak, glow, CRT, scanline/noise texture, saturation, brightness/contrast, sharpen, white balance and lift/gamma/gain. Reference itself states some color filters appear only in exported video.
- Tuning tab: auto zoom on/off, zoom 1.05–2.0 ×, hold 0.1–3 s, period 0.5–10 s; horizontal mirror; crop/padding and border 0–60; rotation none/right 90°/left 90°. Reference has checksum/metadata modification options; independent implementation should label their exact supported behavior truthfully.

## Secondary dialogs

- Settings: API keys / accounts / general settings. General controls: VI/EN, error-reporting switch, output directory choose/open, model-data directory/storage footprint, temporary cache size/clear. Key form accepts multiple user-provided API keys, one per line. Services listed include Gemini, Groq, DeepSeek, Grok, FPT, ElevenLabs, Vbee and other TTS engines. Account forms are optional separate integrations and should never imply reference account credentials are reusable.
- Scenario editor: five tabs for supplied translation templates, personal translation templates, supplied subtitle-repair templates, personal repair templates and pronunciation tables. Personal templates have name/content, add/edit/remove/save. Pronunciation pairs map a term to its spoken form without changing displayed subtitles.
- Cut/join tools: split, join, MP3 extraction. Split selects source, duration display, number of equal parts, output directory, fast split switch, status, open folder and import results. Join has ordered file list with add/remove/reorder/clear, output path, progress/cancel, open folder/import output. MP3 selects video, output MP3 path and bitrate/quality.
- Voice picker: provider/engine list, language filter, search, voice cards grouped by category/gender, preview, selection and confirm. Optional per-voice download/remove and catalog refresh. Distinguish missing provider/model from a ready voice.
- Downloads: model/component cards with readiness state, size/path, install/remove/open-folder actions; Whisper model selector and CPU/GPU-dependent variants. Independent edition should not show fictional installed status.
- Subtitle editor: large preview plus editable timed cue table, original/translated display, search and replace with case/whole-word options, multi-select, add/remove/split/merge, undo, per-cue voice assignment, timing-fit controls, re-generate voice, AI fix action, save/cancel. SRT import/export are core independent functionality.

## Implementation boundaries

All independent code, styling, icons, prompts and assets must be authored for the new application. The reference's QML, images, fonts, compiled modules and executable must not be bundled into it. A faithful layout can be recreated from the above observations. Offline export/edit/SRT/cut/join can be concrete from FFmpeg and an independently installed Qt runtime. Online transcription/translation/TTS require separately configured provider credentials or independently supported local models; unavailable features should say so. Reference authentication, paid access and updates must not be modified or impersonated.
