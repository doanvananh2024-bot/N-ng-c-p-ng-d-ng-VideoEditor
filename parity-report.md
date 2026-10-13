# Observable parity review — 12 October 2026

Read-only reference: installed VideoEditor UI definitions and the prior independently written reference specification. Compared against the independent application before this upgrade. No reference code, fonts, icons, authentication or accounts are copied.

## Ranked gaps to address in this upgrade

| Priority | Reference behavior | Independent baseline | Concrete fidelity fix |
|---|---|---|---|
| 1 | Per-card canvas ratio; editable text/image/blur boxes directly in the video preview | Landscape poster/native player, edit actions open another page | Add ratio selector and independently authored interactive canvas; preserve per-video settings and reflect changes in actual exported dimensions/layer position |
| 2 | Paging and layout live in footer; page size defaults to 20, choices 10/20/30/50/100; navigation appears only for multiple pages | Layout above grid; paging inside grid; default 10, options include 6 and omit 30 | Move controls to footer, use reference defaults/choices, hide single-page navigation |
| 3 | Slider/value rows and switches make sidebar compact | Tall spin boxes consume vertical space | Slider plus precise numeric entry, bind both directions to the processing configuration |
| 4 | Footer export says Xuất (selected count); state reflects selection, output path and current job | Chạy toàn bộ with a job-only disable condition | Show selected count; connect enabled state and stop state to current inputs/job |
| 5 | Voice picker has engine/language filter, search, categories/gender, preview and confirm | Raw voice ID combo | Offline catalog and filterable grouped picker; explicit preview status, persist confirmed voice |
| 6 | Header has subtitle/translation/voice badge pills and visible stage label/percent | Asset availability is a small plain dimensions suffix | Independently drawn badges and per-card active stage status/progress |
| 7 | Apply-to-all is an obvious card action and preserves media/subtitles/voice for each video | Action exists in tuning/context menu | Add compact card action; test per-video resources are unchanged while layer settings copy |
| 8 | Short dark chrome: 8 px margins/gaps, 300 px sidebar, 4 px card corners, 2 px current-card border | 10 px shell margins, 9 px content gap, 8 px corners, selected border 1 px | Match measured geometry and distinguish active card from processing selection |

## Small language/layout differences

Use “Kịch bản”, “Cắt ghép”, “Dịch thuật” and “Tinh chỉnh video” for the corresponding actions/tabs. Keep the two-column grid default. Source, 9:16, 16:9, 1:1, 4:5, 3:4 and 4:3 are the reference ratio choices. Source video, thumbnail and each subtitle must retain their identity as focus changes between cards.

## Observable differences that remain beyond a layout match

The reference lists more online speech/translation providers, paid TTS services and proprietary account integrations. They cannot be claimed equivalent without independently implemented and validated provider support. Its native render module, effects/model packs and server features are not part of this independent build. Licensing, authentication and software updates are separate products. Exact dynamic theme palette values are not in the read-only UI definitions; matching observed geometry and sampled colors does not establish 100% functional parity.

## Upgrade verification plan

Use generated Unicode-path videos, no paid AI calls, no model downloads, own isolated local data and QSettings. Verify footer/default/paging behavior, per-card ratio and real output dimensions, apply-all preserving each video's assets, bidirectional slider values, interactive layer move/resize and per-video persistence, actual rendered layer placement, offline voice search/language selection/confirm, and clean application close. Save machine-readable results and representative screenshots in `qa`.

## Verified upgrade outcomes

`qa/upgrade-verification-report.json` records nine passing integration checks. Footer defaults/choices and paging now match the observed reference; checkbox selection survives page changes. Per-card ratios remain independent and a selected 1:1 canvas produced a real 1080 × 1080 video. Precise values, sliders and renderer settings synchronize. Apply-all retains each video's subtitle and narration resources. Mouse dragging/resizing moved an image to (60, 40), resized it to 50 × 30 and moved a blur mask to (240, 90); these values survived card changes, and exported pixels confirm the image moved. Offline voice language/gender/search and confirmation pass. An active voice-preview worker was cancelled before closing the dialog, without a network request.

A targeted subtitle-modal suite now records six passing checks for independent source/translation documents, editing, per-cue voices, split/merge/undo/search-replace, SRT/VTT sidecars, invalid-timing rejection, preview seek and active-worker cancellation. A discovered stale bilingual view was repaired: the regression now verifies that revisiting the bilingual role includes newly edited original text. Final results are saved in `qa/subtitle-editor-verification-report.json`.

The final chrome uses three equal-width sidebar tabs with independently drawn icons, footer controls, muted disabled export state, card ratio/apply-all actions, completion badges and component Layers/Details views. The preview supports direct manipulation and display-resolution Gaussian blur; real exports continue to render at output resolution. The timed subtitle dialog is accessible from the card's subtitle action.

## Final evidence and honest limits

- Upgrade desktop suite: **9/9 passed**, `qa/upgrade-verification-report.json`; includes actual interactive drag/resize and two real FFmpeg exports.
- Subtitle modal suite: **6/6 passed**, `qa/subtitle-editor-verification-report.json`; includes the bilingual freshness regression, SRT/VTT voice sidecars and worker-close cancellation.
- Existing media/runtime suite: **8/8 passed**, `qa/verification-report.json`; includes Unicode paths, advanced layers, H.265/MOV, export cancellation/resume, ordered joining, split/MP3 and overwrite protection.
- Final rebuilt EXE **1.1.0**: **6/6 passed**, `qa/packaged-verification-report.json`; all eleven runtime/UI modules imported from the frozen bundle, real advanced H.265/MOV and MP3 jobs passed, multiprocessing RapidOCR recognized `HELLO WORLD` without downloads, and the executable captured a readable empty workspace on Qt's offscreen virtual display. A separate native Windows run of the same executable captured the full workspace at **1920 × 1001** with isolated data and exited successfully; its screenshot path is recorded in that report. Tested executable SHA-256: `9b35f64652acbdc638b82a06f2293169dec69db8c92d31e180987af991e981ee`.

This is independently authored functional and layout matching, not proof of 100% identity. The original licensing/accounts/update service, proprietary renderer and downloadable packs have not been copied or reproduced. The independent voice picker currently supports Edge TTS, while the reference catalogs several paid TTS providers. Online transcription/translation/voice services require their own independently supported engines and credentials. A successful offline integration suite does not establish identical output/performance for every codec, GPU, source format, subtitle language or provider. Bilingual composition requires aligned source/translated cue timelines. Some color-filter/zoom/fade behavior is inspected through the explicit rendered preview rather than by claiming full live-preview equivalence.

## Version 1.2.0 — completed source upgrade, 12 October 2026

The installed reference is VideoEditor 1.0.36, independently identified from its Desktop shortcut and uninstall metadata. The independent 1.2.0 continues the previously interrupted source upgrade and adds:

- Real per-layer rotation, horizontal/vertical flip, enabled state, independently editable duplication, text outlines/backgrounds and nine image anchors.
- Timed blur, corner/feather controls, zoom hold and advanced CRF/VBR/CBR/preset/B-frame configuration.
- FPT and ElevenLabs provider integration using the independent application's own encrypted keys and catalogs, including per-cue provider routing. Provider transport is verified with mocked HTTP and real synthetic audio; no paid account request has been made.
- Consistent multi-speaker timing fit that considers the next cue across voices, retains trailing silence and warns when synthesis requires unusually fast speech.
- GIF preview and export agree on frame timing, rotation/flip and looping. One-shot GIFs retain the final frame for its duration and disappear after one cycle. Static raster sizing, short anchor aliases and default offsets now match.
- Empty sidebar states, immediate detail loading after adding a layer, removal cleanup, compact combo rows and codec item availability. Paid voice filtering/refresh/preview cancellation/cache failures are handled visibly.

Source evidence on the final runtime: core media suite **8/8**, previous layout/interaction suite **9/9**, subtitle modal **6/6**, advanced real-export suite **6/6**, renderer regressions **4/4**, TTS regressions **12/12** and isolated sidebar state checks **5/5**. Reports and actual generated media/screenshots are in `qa`. The old Apply-all resource regression was detected during verification and repaired before release.

The advanced GUI screenshot now shows the populated card workspace and selected-layer details. This is a verified closer match, not 100% equivalence. Vbee and other private speech services, equivalent offline TTS voices/model packs, proprietary account/update services, several source transform/blend/background modules and the full live filter preview are still absent or differ. Each provider needs the user's own credentials and service limits. No exact-match percentage is asserted.

The final frozen 1.2.0 executable passed **6/6** packaged checks, including actual H.265/MOV with rotated/flipped anchored image pixels and B-frames disabled, MP3, RapidOCR and an application screenshot. All **18** owned module bytecodes match final source. Together with source/sidebar checks the release has **56 passing checks**. Executable SHA-256: `53f4486739b318ff03118697f6e2c5488bc99e23519864009ed95ddb371236ae`.
