"""Film translation/editing templates and local pronunciation dictionaries.

Built-in templates are ordinary original prompts; they do not claim to contain
the other application's proprietary scripts. Custom templates/dictionaries are
saved in this application's own JSON data folder. This module never uses the
network or reads another application's settings.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Iterable
import uuid

from providers import app_data_root

_CATEGORIES = [
    ("ai_tong_hop", "★ AI Tổng hợp thông minh",
     "Nhận biết thể loại và quan hệ nhân vật từ lời thoại. Dịch tự nhiên, đúng nghĩa, thống nhất tên riêng và cách xưng hô. "
     "Giữ sắc thái cảm xúc, không tự thêm tình tiết hoặc lời giải thích."),
    ("co_trang", "Phim Cổ Trang",
     "Dùng văn phong cổ trang rõ nghĩa. Giữ danh xưng, phẩm vị, thân phận và lễ nghi. Chọn cách xưng hô theo quan hệ nhân vật; "
     "không tự nâng cấp địa vị và không lạm dụng từ Hán Việt khó hiểu."),
    ("kiem_hiep", "Phim Kiếm Hiệp / Tiên Hiệp",
     "Giữ thuật ngữ võ học, tu luyện, môn phái, cảnh giới và pháp bảo nhất quán. Tên Trung Quốc dùng Hán Việt khi xác định được. "
     "Lời thoại có sắc thái giang hồ hoặc tiên hiệp nhưng dễ nghe; không bịa tên chiêu thức."),
    ("xuyen_khong", "Phim Xuyên Không",
     "Phân biệt lời thoại hiện đại của người xuyên không với lời thoại thời đại họ đến. Giữ các liên hệ thời gian, ký ức và thân phận "
     "đúng bản gốc; chuyển trò đùa và thành ngữ tự nhiên, không tự giải thích cốt truyện."),
    ("hien_dai", "Phim Hiện Đại",
     "Dùng tiếng nói hiện đại, tự nhiên, ngắn gọn. Xưng hô phù hợp tuổi tác, quan hệ gia đình, tình cảm hoặc công việc. "
     "Giữ sắc thái thân mật, mỉa mai và xung đột của nhân vật."),
    ("tom_tat", "Tóm Tắt / Review Phim",
     "Dùng giọng kể mạch lạc, rõ hành động và quan hệ nhân quả. Giữ nguyên mọi sự kiện và nội dung đã có trong từng câu; "
     "không thêm nhận xét, quảng cáo, lời kêu gọi hoặc sự kiện ngoài bản gốc."),
    ("hanh_dong", "Phim Hành Động",
     "Lời thoại dứt khoát, ngắn gọn, phù hợp nhịp hành động. Giữ chính xác mệnh lệnh, địa điểm, tín hiệu, số liệu và thuật ngữ "
     "quân sự/cảnh sát; thể hiện căng thẳng nhưng không làm tăng mức bạo lực của lời gốc."),
    ("hai_huoc", "Phim Hài Hước",
     "Giữ dụng ý hài, chơi chữ và nhịp chốt câu khi có thể. Ưu tiên câu dễ hiểu trong văn hóa ngôn ngữ đích, nhưng không thay đổi "
     "tình huống, tự tạo trò đùa hoặc thêm lời thoại."),
    ("kinh_di", "Phim Kinh Dị",
     "Giữ không khí hồi hộp, lo sợ, bí ẩn và nhịp nói của bản gốc. Dịch rõ các cảnh báo và manh mối; không tiết lộ, "
     "giải thích hoặc sáng tạo tình tiết mà bản gốc chưa nêu."),
    ("han_quoc", "Phim Hàn Quốc",
     "Giữ tên nhân vật Hàn Quốc nhất quán. Chuyển kính ngữ và quan hệ tuổi tác/gia đình thành cách xưng hô phù hợp ngôn ngữ đích. "
     "Giữ khác biệt thân mật và trang trọng; không đoán quan hệ khi chưa đủ ngữ cảnh."),
    ("anime_nhat", "Anime / Phim Nhật",
     "Giữ tên nhân vật, tổ chức và tên kỹ năng nhất quán. Chuyển cách gọi và kính ngữ Nhật theo ngữ cảnh, giữ sắc thái anime "
     "hoặc đời thường đúng bản gốc; không thêm câu cảm thán hay giải thích ngoài lời thoại."),
    ("au_my", "Phim Âu Mỹ",
     "Dịch thành ngữ và lời nói tự nhiên theo ngữ cảnh. Giữ mức độ trang trọng, tiếng lóng, châm biếm và cảm xúc tương ứng bản gốc. "
     "Không dịch máy móc thành ngữ và không tự kiểm duyệt hoặc tăng mức thô tục của lời gốc."),
]
_VERSION = 1
_MAX_PROMPT = 24000
_MAX_ITEMS = 200
_MAX_RULES = 1000
_MAX_FILE = 8 * 1024 * 1024
_KINDS = {"translate", "rewrite"}
_ROOT_LOCKS: dict[str, threading.RLock] = {}
_ROOT_LOCKS_GUARD = threading.Lock()


def _kind(value: str) -> str:
    value = str(value or "translate").strip().lower()
    aliases = {"translation": "translate", "edit": "rewrite", "editing": "rewrite"}
    value = aliases.get(value, value)
    if value not in _KINDS:
        raise ValueError("Loại kịch bản cần là translate hoặc rewrite.")
    return value


def builtin_templates(kind: str = "translate") -> list[dict]:
    kind = _kind(kind)
    prefix = ("Dịch phụ đề theo hướng dẫn phong cách sau. " if kind == "translate" else
              "Sửa diễn đạt, chính tả và xưng hô của phụ đề theo phong cách sau. Giữ ý nghĩa và diễn biến gốc. ")
    return [dict(id=identity, name=name, prompt=prefix + prompt +
                 " Giữ nguyên ID và số dòng; không thay đổi mốc thời gian, gộp hoặc tách câu.",
                 builtin=True, kind=kind) for identity, name, prompt in _CATEGORIES]


class ScenarioStore:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(root).expanduser().absolute() if root is not None else app_data_root() / "scenarios"
        self.path = self.root / "scenarios.json"
        with _ROOT_LOCKS_GUARD:
            self._lock = _ROOT_LOCKS.setdefault(str(self.path.resolve()).casefold(), threading.RLock())

    def _read(self) -> dict:
        if not self.path.exists():
            return dict(version=_VERSION, templates=[], pronunciation=[])
        try:
            if self.path.stat().st_size > _MAX_FILE:
                raise ValueError("oversized file")
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(payload, dict) or payload.get("version") != _VERSION
                    or not isinstance(payload.get("templates"), list)
                    or not isinstance(payload.get("pronunciation"), list)):
                raise ValueError("invalid structure")
            return payload
        except (OSError, ValueError, TypeError, UnicodeError):
            raise ValueError("Không đọc được kho kịch bản. Hãy giữ lại file scenarios.json và phục hồi từ bản sao.") from None

    def _write(self, payload: dict) -> None:
        text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
        if len(text.encode("utf-8")) > _MAX_FILE:
            raise ValueError("Kho kịch bản vượt quá 8 MB. Hãy rút gọn hoặc bỏ bớt kịch bản.")
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".scenarios-", suffix=".tmp", dir=self.root)
        staged = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as destination:
                destination.write(text)
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(staged, self.path)
        finally:
            staged.unlink(missing_ok=True)

    def list_templates(self, kind: str = "translate") -> list[dict]:
        kind = _kind(kind)
        with self._lock:
            custom = [copy.deepcopy(item) for item in self._read()["templates"]
                      if isinstance(item, dict) and item.get("kind") == kind]
        return builtin_templates(kind) + custom

    def get_template(self, template_id: str, kind: str = "translate") -> dict:
        for template in self.list_templates(kind):
            if template["id"] == str(template_id):
                return template
        raise KeyError("Kịch bản được chọn không còn tồn tại.")

    def save_template(self, name: str, prompt: str, template_id: str | None = None,
                      kind: str = "translate") -> dict:
        kind = _kind(kind)
        name, prompt = str(name).strip(), str(prompt).strip()
        if not name or len(name) > 160:
            raise ValueError("Tên kịch bản cần có từ 1 đến 160 ký tự.")
        if not prompt or len(prompt) > _MAX_PROMPT:
            raise ValueError("Nội dung kịch bản cần có từ 1 đến 24.000 ký tự.")
        builtin_ids = {item["id"] for item in builtin_templates(kind)}
        # Editing a built-in always creates a personal copy, preserving originals.
        identity = str(template_id) if template_id and template_id not in builtin_ids else "user_" + uuid.uuid4().hex
        result = dict(id=identity, name=name, prompt=prompt, builtin=False, kind=kind)
        with self._lock:
            payload = self._read()
            found = next((index for index, item in enumerate(payload["templates"])
                          if item.get("id") == identity and item.get("kind") == kind), None)
            if template_id and template_id not in builtin_ids and found is None:
                raise KeyError("Kịch bản cần sửa không còn tồn tại.")
            if found is None:
                if len(payload["templates"]) >= _MAX_ITEMS:
                    raise ValueError("Kho đã có 200 kịch bản cá nhân. Xóa bớt trước khi thêm.")
                payload["templates"].append(result)
            else:
                payload["templates"][found] = result
            self._write(payload)
        return copy.deepcopy(result)

    def delete_template(self, template_id: str, kind: str = "translate") -> None:
        kind = _kind(kind)
        if str(template_id) in {item["id"] for item in builtin_templates(kind)}:
            raise ValueError("Kịch bản mặc định được giữ lại. Bạn có thể tạo và xóa bản sao cá nhân.")
        with self._lock:
            payload = self._read()
            filtered = [item for item in payload["templates"]
                        if not (item.get("id") == str(template_id) and item.get("kind") == kind)]
            if len(filtered) == len(payload["templates"]):
                raise KeyError("Kịch bản cần xóa không còn tồn tại.")
            payload["templates"] = filtered
            self._write(payload)

    def list_pronunciation(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._read()["pronunciation"])

    def get_pronunciation(self, dictionary_id: str) -> dict:
        for item in self.list_pronunciation():
            if item.get("id") == str(dictionary_id):
                return item
        raise KeyError("Bảng cách đọc được chọn không còn tồn tại.")

    @staticmethod
    def _rules(rules: Iterable[dict] | dict[str, str]) -> list[dict]:
        if isinstance(rules, dict):
            rules = [dict(source=source, target=target) for source, target in rules.items()]
        rules = list(rules)
        if len(rules) > _MAX_RULES:
            raise ValueError("Mỗi bảng cách đọc hỗ trợ tối đa 1.000 quy tắc.")
        cleaned, signatures = [], set()
        for rule in rules:
            if not isinstance(rule, dict):
                raise ValueError("Mỗi quy tắc cần có source và target.")
            source, target = str(rule.get("source", "")).strip(), str(rule.get("target", ""))
            if not source or len(source) > 256 or len(target) > 512:
                raise ValueError("Từ gốc cần 1–256 ký tự; cách đọc không quá 512 ký tự.")
            case_sensitive, whole_word = bool(rule.get("case_sensitive", False)), bool(rule.get("whole_word", True))
            signature = (source if case_sensitive else source.casefold(), case_sensitive, whole_word)
            if signature in signatures:
                raise ValueError("Có quy tắc cách đọc trùng từ gốc; hãy giữ một quy tắc cho mỗi từ.")
            signatures.add(signature)
            cleaned.append(dict(source=source, target=target, case_sensitive=case_sensitive, whole_word=whole_word))
        return cleaned

    def save_pronunciation(self, name: str, rules: Iterable[dict] | dict[str, str],
                           dictionary_id: str | None = None) -> dict:
        name = str(name).strip()
        if not name or len(name) > 160:
            raise ValueError("Tên bảng cách đọc cần có từ 1 đến 160 ký tự.")
        identity = str(dictionary_id) if dictionary_id else "pronunciation_" + uuid.uuid4().hex
        result = dict(id=identity, name=name, rules=self._rules(rules))
        with self._lock:
            payload = self._read()
            found = next((index for index, item in enumerate(payload["pronunciation"])
                          if item.get("id") == identity), None)
            if dictionary_id and found is None:
                raise KeyError("Bảng cách đọc cần sửa không còn tồn tại.")
            if found is None:
                if len(payload["pronunciation"]) >= _MAX_ITEMS:
                    raise ValueError("Kho đã có 200 bảng cách đọc. Xóa bớt trước khi thêm.")
                payload["pronunciation"].append(result)
            else:
                payload["pronunciation"][found] = result
            self._write(payload)
        return copy.deepcopy(result)

    def delete_pronunciation(self, dictionary_id: str) -> None:
        with self._lock:
            payload = self._read()
            filtered = [item for item in payload["pronunciation"] if item.get("id") != str(dictionary_id)]
            if len(filtered) == len(payload["pronunciation"]):
                raise KeyError("Bảng cách đọc cần xóa không còn tồn tại.")
            payload["pronunciation"] = filtered
            self._write(payload)

    def apply_pronunciation(self, text: str, dictionary_id: str) -> str:
        """Literal Unicode replacements in one pass, longest source wins.

        Replacement text is never reprocessed, so A→B and B→C does not turn A
        into C. This function is intended for speech text only, preserving the
        displayed subtitles. Word boundaries use Unicode letters/digits.
        """
        rules = self._rules(self.get_pronunciation(dictionary_id)["rules"])
        ordered = sorted(enumerate(rules), key=lambda pair: (-len(pair[1]["source"]), pair[0]))
        branches, replacements = [], {}
        for position, (_original_index, rule) in enumerate(ordered):
            group = f"r{position}"
            pattern = re.escape(rule["source"])
            if not rule["case_sensitive"]:
                pattern = "(?i:" + pattern + ")"
            if rule["whole_word"]:
                pattern = r"(?<!\w)" + pattern + r"(?!\w)"
            branches.append("(?P<" + group + ">" + pattern + ")")
            replacements[group] = rule["target"]
        if not branches:
            return str(text)
        regex = re.compile("|".join(branches))
        return regex.sub(lambda match: replacements[match.lastgroup], str(text))

    def apply_pronunciation_rows(self, rows: Iterable[dict], dictionary_id: str) -> list[dict]:
        """Copy speech cues with corrected pronunciation; timing stays intact."""
        result = copy.deepcopy(list(rows))
        for row in result:
            row["text"] = self.apply_pronunciation(row["text"], dictionary_id)
        return result
