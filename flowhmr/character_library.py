"""Portable character files and preview preferences, independent of the desktop port."""
from contextlib import nullcontext
from hashlib import sha256
from io import BytesIO
import json
from math import isfinite
from pathlib import Path
import re
from shutil import rmtree
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import Lock
from time import sleep
from uuid import uuid4

from .character_files import MAX_MODEL_BYTES, MAX_TEXTURE_BYTES, MODEL_EXTENSIONS, texture_parts

_ID = re.compile(r"[0-9a-f]{64}")
_RETIRED = re.compile(r"\.removed-[0-9a-f]{32}")
_MAX_BUNDLE_BYTES = 1 << 30
THUMBNAIL_NAME = "thumbnail.png"
THUMBNAIL_BYTES = 512 << 10
_THUMBNAIL_SIDE = 512
_PRESET_THUMBNAIL_SIDE = 192
_IK_SLOTS = ("leftFoot", "rightFoot", "leftHand", "rightHand")
_REQUIRED_JOINTS = (0, 1, 2, 3, 4, 5, 7, 8, 15, 16, 17, 18, 19, 20, 21)
_BUILTIN_PRESET_KINDS = {"pmx": "pmx", "vrm": "vrm", "mixamo": "fbx", "metahuman": "fbx"}


class RetargetPresetError(ValueError):
    """Invalid or incompatible reusable preset data."""


def _valid_id(value):
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def _pose(value):
    def triple(vector, limit):
        if not isinstance(vector, list) or len(vector) != 3:
            return [0, 0, 0]
        return [max(-limit, min(limit, x)) if type(x) in (int, float)
                and (type(x) is int or isfinite(x)) else 0 for x in vector]

    value = value if isinstance(value, dict) else {}
    rotations = value.get("rotations")
    rotations = rotations if isinstance(rotations, dict) else {}
    normalized = {}
    for key in map(str, range(37)):
        angles = triple(rotations.get(key), 180)
        if any(angles):
            normalized[key] = angles
    pose = {"rotations": normalized, "mirror": value.get("mirror") is True,
            "ikMode": "ik" if value.get("ikMode") == "ik" else "fk",
            "rootRotation": triple(value.get("rootRotation"), 180),
            "rootTranslation": triple(value.get("rootTranslation"), 5)}
    for key in ("pmxBaselineVersion", "vrmBaselineVersion"):
        baseline = value.get(key)
        if type(baseline) is int and baseline == 1:
            pose[key] = baseline
    def offsets(data, name, limit):
        values = data.get(name)
        values = values if isinstance(values, dict) else {}
        return {key: val for key in map(str, range(52)) if any(val := triple(values.get(key), limit))}

    extra = value.get("boneOffsets")
    if isinstance(extra, dict):
        extra = {"rotations": offsets(extra, "rotations", 180), "translations": offsets(extra, "translations", 5)}
        if any(extra.values()):
            pose["boneOffsets"] = extra
    profile = value.get("retarget")
    if isinstance(profile, dict) and type(profile.get("version")) is int and profile["version"] == 1:
        mapping = profile.get("map")
        if isinstance(mapping, list) and len(mapping) == 52:
            def index(item):
                return item if type(item) is int and 0 <= item < 65536 else -1

            ik = profile.get("ik")
            ik = ik if isinstance(ik, dict) else {}
            pose["retarget"] = {"version": 1, "map": [index(x) for x in mapping], "root": index(profile.get("root")),
                                "ik": {slot: index(ik.get(slot)) for slot in _IK_SLOTS},
                                "rotations": offsets(profile, "rotations", 180), "translations": offsets(profile, "translations", 5)}
    return pose


def _retarget_preset(value):
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        raise RetargetPresetError("Invalid retarget preset")
    identifier, name, kind = (value.get(key) for key in ("id", "name", "kind"))
    builtin = value.get("builtin")
    source = value.get("thumbnail_source")
    if (not _valid_id(identifier) or not isinstance(name, str) or kind not in ("pmx", "vrm", "fbx")
            or (builtin is not None and
                (not isinstance(builtin, str) or _BUILTIN_PRESET_KINDS.get(builtin) != kind))
            or (source is not None and not _valid_id(source))):
        raise RetargetPresetError("Invalid retarget preset")
    name = " ".join(name.split())[:80]
    if not name or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in name):
        raise RetargetPresetError("Invalid retarget preset name")

    def path(item):
        if item is None:
            return None
        if (not isinstance(item, str) or not item.startswith("/") or not 2 <= len(item) <= 384
                or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in item)):
            raise RetargetPresetError("Invalid retarget preset bone path")
        return item

    links, ik = value.get("links"), value.get("ik")
    if not isinstance(links, list) or len(links) != 52 or not isinstance(ik, dict):
        raise RetargetPresetError("Invalid retarget preset mapping")
    links = [path(item) for item in links]
    root, ik = path(value.get("root")), {key: path(ik.get(key)) for key in _IK_SLOTS}
    if (any(links[j] is None for j in _REQUIRED_JOINTS)
            or len(set(filter(None, links))) != len(list(filter(None, links)))):
        raise RetargetPresetError("Missing or duplicate preset bones")
    controllers = list(filter(None, ik.values()))
    if (len(controllers) != len(set(controllers))
            or any(item in links or item == root for item in controllers)
            or (root is not None and root in links and root != links[0])):
        raise RetargetPresetError("Conflicting retarget preset assignments")
    offsets = _pose(value.get("offsets"))
    offsets.pop("retarget", None)
    result = {"version": 1, "id": identifier, "name": name, "kind": kind, "builtin": builtin,
              "links": links, "root": root, "ik": ik, "offsets": offsets}
    if source is not None:
        result["thumbnail_source"] = source
    return result


def _builtin_override(profile, value):
    if not isinstance(profile, str) or profile not in _BUILTIN_PRESET_KINDS:
        raise RetargetPresetError("Unknown built-in retarget profile")
    if (not isinstance(value, dict) or value.get("id", profile) != profile
            or type(value.get("version")) is not int or value["version"] != 1
            or not isinstance(value.get("offsets"), dict)):
        raise RetargetPresetError("Invalid built-in retarget override")
    offsets = _pose(value["offsets"])
    offsets.pop("retarget", None)
    result = {"id": profile, "version": 1, "offsets": offsets}
    if value.get("binding") is not None:
        binding = _retarget_preset(value["binding"])
        if binding["kind"] != _BUILTIN_PRESET_KINDS[profile] or binding["builtin"] is not None:
            raise RetargetPresetError("Built-in override binding does not match the profile")
        binding.pop("thumbnail_source", None)
        result["binding"] = binding
    return result


class CharacterLibrary:
    def __init__(self, root):
        self.root = Path(root)
        self._lock = Lock()
        self._checked_retired = False

    def _entry(self, identifier):
        if not _valid_id(identifier):
            return None
        directory = self.root / identifier
        canonical = self.root.resolve() / identifier
        # A manually moved or replaced entry must never grant access outside the library.
        if not directory.is_dir() or directory.resolve() != canonical:
            return None
        manifest_path = directory / "character.json"
        if manifest_path.resolve() != canonical / "character.json":
            return None
        record = _read_json(manifest_path)
        filename = record.get("filename")
        if (not isinstance(filename, str) or texture_parts(filename) != (filename,)
                or Path(filename).suffix.lower() not in MODEL_EXTENSIONS):
            return None
        file = directory / "files" / filename
        if not file.is_file() or file.resolve() != canonical / "files" / filename:
            return None
        name = record.get("name")
        return {"id": identifier, "filename": filename, "name": name if isinstance(name, str) and name else filename,
                "kind": file.suffix[1:].lower(), "has_thumbnail": self._thumbnail(file) is not None}, file

    @staticmethod
    def _thumbnail(model):
        path = model.parent.parent / THUMBNAIL_NAME
        return CharacterLibrary._valid_thumbnail(path)

    @staticmethod
    def _valid_thumbnail(path):
        try:
            if (not path.is_symlink() and path.is_file() and path.resolve() == path.parent.resolve() / path.name
                    and 0 < path.stat().st_size <= THUMBNAIL_BYTES):
                return path
        except OSError:
            pass
        return None

    @staticmethod
    def _read_thumbnail_data(path):
        with path.open("rb") as stream:
            data = stream.read(THUMBNAIL_BYTES + 1)
        if len(data) > THUMBNAIL_BYTES or not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise FileNotFoundError("Invalid saved thumbnail")
        return data

    def read_thumbnail(self, identifier):
        with self._lock:
            entry = self._entry(identifier)
            if entry is None or (path := self._thumbnail(entry[1])) is None:
                raise FileNotFoundError("Character thumbnail unavailable")
            return self._read_thumbnail_data(path)

    @staticmethod
    def _clean_thumbnail(data, side=_THUMBNAIL_SIDE):
        from PIL import Image

        if not 0 < len(data) <= THUMBNAIL_BYTES:
            raise ValueError("Character thumbnail exceeds its limit")
        try:
            with Image.open(BytesIO(data), formats=("PNG",)) as picture:
                if max(picture.size) > _THUMBNAIL_SIDE or getattr(picture, "n_frames", 1) != 1:
                    raise ValueError("Invalid character thumbnail size or frame count")
                picture.verify()
            with Image.open(BytesIO(data), formats=("PNG",)) as picture:
                # Re-encode validated pixels to drop metadata and trailing bytes.
                pixel_data = picture.convert("RGBA")
                image = Image.frombytes("RGBA", pixel_data.size, pixel_data.tobytes())
                if max(image.size) > side:
                    image.thumbnail((side, side), Image.Resampling.LANCZOS)
                clean = BytesIO()
                image.save(clean, format="PNG")
        except (Image.DecompressionBombError, OSError, SyntaxError) as cause:
            raise ValueError("Invalid character thumbnail PNG") from cause
        if clean.tell() > THUMBNAIL_BYTES:
            raise ValueError("Character thumbnail exceeds its encoded limit")
        return clean.getvalue()

    @staticmethod
    def _write_thumbnail_data(path, data):
        with NamedTemporaryFile(prefix=".thumb-", suffix=".part", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(data)
                stream.close()
                temporary.replace(path)
            finally:
                stream.close()
                temporary.unlink(missing_ok=True)

    def write_thumbnail(self, identifier, data):
        clean = self._clean_thumbnail(data)
        with self._lock:
            entry = self._entry(identifier)
            if entry is None:
                raise FileNotFoundError("Character is not in the library")
            self._write_thumbnail_data(entry[1].parent.parent / THUMBNAIL_NAME, clean)
            # The first preview may be generated after the preset is saved.
            # Preserve a small copy even if its source character is deleted later.
            for preset in _read_json(self._preferences).get("retarget_presets", {}).values():
                if preset.get("thumbnail_source") == identifier:
                    self._copy_preset_thumbnail(preset, replace=False)

    def open(self, identifier, selections):
        with self._lock:
            entry = self._entry(identifier)
            if entry is None:
                raise FileNotFoundError("Character is not in the library")
            return selections.register(str(entry[1].resolve()))

    @staticmethod
    def _bundle_digest(model, textures, entry=None):
        if texture_parts(model.name) != (model.name,):
            raise ValueError("Invalid character filename")
        digest = sha256()
        total = 0
        for relative, source, limit in [
            (model.name, model, MAX_MODEL_BYTES),
            *((path, file, MAX_TEXTURE_BYTES) for path, file in sorted(textures)),
        ]:
            parts = texture_parts(relative)
            candidate = model.parent.joinpath(*parts)
            if (not parts or source.resolve(strict=True) != source
                    or candidate.resolve(strict=True) != source or not source.is_relative_to(model.parent)
                    or not source.is_file() or not 0 < source.stat().st_size <= limit):
                raise ValueError("Character file changed while importing")
            if entry is not None:
                dest = entry.joinpath("files", *parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
            file_digest, size = sha256(), 0
            with source.open("rb") as src, (dest.open("xb") if entry is not None else nullcontext()) as out:
                while block := src.read(1 << 20):
                    size += len(block)
                    total += len(block)
                    if size > limit or total > _MAX_BUNDLE_BYTES:
                        raise ValueError("Character bundle is too large")
                    if out is not None:
                        out.write(block)
                    file_digest.update(block)
            if size == 0:
                raise ValueError("Character file changed while importing")
            digest.update(relative.encode("utf-8") + b"\0" + file_digest.digest())
        return digest.hexdigest()

    def lookup(self, selections, ticket):
        # Avoid a second read on the first import; only existing entries can
        # match. The filename is part of the bundle digest.
        model, textures = selections.bundle(ticket)
        with self._lock:
            if not self.root.is_dir() or not any(
                entry[0]["filename"] == model.name
                for folder in self.root.iterdir() if (entry := self._entry(folder.name)) is not None
            ):
                return None
        identifier = self._bundle_digest(model, textures)
        with self._lock:
            entry = self._entry(identifier)
            return entry[0] if entry is not None else None

    def save(self, selections, ticket, name):
        model, textures = selections.bundle(ticket)
        label = " ".join(name.split())[:200] if isinstance(name, str) else ""
        record = {"filename": model.name, "name": label or model.stem}
        self.root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".import-", dir=self.root) as scratch:
            entry = Path(scratch) / "entry"
            identifier = self._bundle_digest(model, textures, entry)
            (entry / "character.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            with self._lock:
                existing = self._entry(identifier)
                if existing is not None:
                    return existing[0]
                entry.rename(self.root / identifier)
        return {"id": identifier, **record, "kind": model.suffix[1:].lower(), "has_thumbnail": False}

    @property
    def _preferences(self):
        return self.root / ".preview.json"

    def _preset_thumbnail_file(self, identifier, *, create=False):
        if not _valid_id(identifier):
            raise FileNotFoundError("Unknown retarget preset")
        directory = self.root / ".retarget-thumbnails"
        if directory.resolve() != self.root.resolve() / directory.name:
            raise OSError("Invalid retarget thumbnail directory")
        if create:
            directory.mkdir(exist_ok=True)
        return directory / f"{identifier}.png"

    def _preset_thumbnail(self, identifier):
        try:
            return self._valid_thumbnail(self._preset_thumbnail_file(identifier))
        except OSError:
            return None

    def _catalog_preset(self, preset):
        return {**preset, "has_thumbnail": self._preset_thumbnail(preset["id"]) is not None}

    def _copy_preset_thumbnail(self, preset, *, replace):
        identifier = preset["id"]
        try:
            if not replace and self._preset_thumbnail(identifier):
                return
            source = self._entry(preset.get("thumbnail_source"))
            path = self._thumbnail(source[1]) if source else None
            if path:
                data = self._clean_thumbnail(self._read_thumbnail_data(path), _PRESET_THUMBNAIL_SIDE)
                self._write_thumbnail_data(self._preset_thumbnail_file(identifier, create=True), data)
        except (OSError, ValueError):
            # The optional image should not prevent saving an actor or preset.
            return

    def read_retarget_thumbnail(self, identifier):
        with self._lock:
            presets = _read_json(self._preferences).get("retarget_presets", {})
            if not _valid_id(identifier) or identifier not in presets or not (path := self._preset_thumbnail(identifier)):
                raise FileNotFoundError("Retarget thumbnail unavailable")
            return self._read_thumbnail_data(path)

    def write_retarget_thumbnail(self, identifier, data):
        clean = self._clean_thumbnail(data, _PRESET_THUMBNAIL_SIDE)
        with self._lock:
            presets = _read_json(self._preferences).get("retarget_presets", {})
            if not _valid_id(identifier) or identifier not in presets:
                raise FileNotFoundError("Unknown retarget preset")
            self._write_thumbnail_data(self._preset_thumbnail_file(identifier, create=True), clean)

    @staticmethod
    def _purge_retired(root, directory):
        # Only unlink an app-owned staging directory inside this library.
        try:
            if (directory.parent != root or not _RETIRED.fullmatch(directory.name)
                    or not directory.is_dir() or directory.resolve(strict=True) != directory):
                return False
            rmtree(directory)
            return True
        except OSError:
            return False

    def remove(self, identifier, selections):
        with self._lock:
            entry = self._entry(identifier)
            if entry is None:
                raise FileNotFoundError("Character is not in the library")
            root = self.root.resolve(strict=True)
            directory = root / identifier
            if directory.resolve(strict=True) != directory:
                raise ValueError("Character directory is outside the library")
            state = _read_json(self._preferences)
            for record in state.get("actors", {}).values():
                if record.get("id") == identifier:
                    record["id"] = None
                record.get("poses", {}).pop(identifier, None)
            defaults = state.get("defaults", {})
            for slot in [slot for slot, value in defaults.items() if value.get("id") == identifier]:
                del defaults[slot]
            state.get("presets", {}).pop(identifier, None)

            # Move this bundle out of the catalog first. Roll back the move if
            # saving the updated assignments fails, before removing any files.
            retired = root / f".removed-{uuid4().hex}"
            if retired.resolve() != retired or retired.exists():
                raise ValueError("Invalid character removal directory")
            directory.rename(retired)
            try:
                self._write(state)
            except BaseException:
                retired.rename(directory)
                raise
            selections.revoke_directory(directory)
            return self._purge_retired(root, retired)

    def snapshot(self, actor_ids):
        models = []
        with self._lock:
            if self.root.is_dir():
                root = self.root.resolve(strict=True)
                for directory in root.iterdir():
                    if _RETIRED.fullmatch(directory.name):
                        if not self._checked_retired:
                            self._purge_retired(root, directory)
                        continue
                    entry = self._entry(directory.name)
                    if entry is not None:
                        models.append(entry[0])
                self._checked_retired = True
            state = _read_json(self._preferences)
            presets = {key: self._catalog_preset(value)
                       for key, value in state.get("retarget_presets", {}).items()}
        models.sort(key=lambda item: (item["name"].casefold(), item["filename"].casefold(), item["id"]))
        actors = state.get("actors", {})
        return {"models": models, "defaults": state.get("defaults", {}), "presets": state.get("presets", {}),
                "retarget_presets": presets, "builtin_retarget_presets": state.get("builtin_retarget_presets", {}),
                "actors": {identifier: actors[identifier] for identifier in actor_ids if identifier in actors}}

    def _save_retarget_preset(self, state, preset, source=None):
        source = source or preset.get("thumbnail_source")
        if source is not None:
            character = self._entry(source)
            if character is not None and character[0]["kind"] == preset["kind"]:
                preset["thumbnail_source"] = source
            else:
                preset.pop("thumbnail_source", None)
        presets = state.setdefault("retarget_presets", {})
        previous = presets.get(preset["id"], {})
        if "thumbnail_source" not in preset and "thumbnail_source" in previous:
            preset["thumbnail_source"] = previous["thumbnail_source"]
        presets[preset["id"]] = preset

    def set_retarget_preset(self, value):
        preset = _retarget_preset(value)
        with self._lock:
            state = _read_json(self._preferences)
            self._save_retarget_preset(state, preset)
            self._write(state)
            self._copy_preset_thumbnail(preset, replace=True)
            return self._catalog_preset(preset)

    def remove_retarget_preset(self, identifier):
        if not _valid_id(identifier):
            raise FileNotFoundError("Unknown retarget preset")
        with self._lock:
            state = _read_json(self._preferences)
            presets = state.get("retarget_presets", {})
            if identifier not in presets:
                raise FileNotFoundError("Unknown retarget preset")
            del presets[identifier]
            self._write(state)
            try:
                self._preset_thumbnail_file(identifier).unlink(missing_ok=True)
            except OSError:
                pass

    def set_builtin_retarget_preset(self, profile, value):
        override = _builtin_override(profile, value)
        with self._lock:
            state = _read_json(self._preferences)
            state.setdefault("builtin_retarget_presets", {})[profile] = override
            self._write(state)
        return override

    def reset_builtin_retarget_preset(self, profile):
        if not isinstance(profile, str) or profile not in _BUILTIN_PRESET_KINDS:
            raise RetargetPresetError("Unknown built-in retarget profile")
        with self._lock:
            state = _read_json(self._preferences)
            overrides = state.get("builtin_retarget_presets", {})
            if profile in overrides:
                del overrides[profile]
                self._write(state)

    def set_actor(self, actor, slot, identifier, offsets, *, select, retarget_preset=None, remember=False):
        with self._lock:
            entry = self._entry(identifier) if identifier is not None else None
            if identifier is not None and entry is None:
                raise FileNotFoundError("Character is not in the library")
            preset = None
            state = _read_json(self._preferences)
            defaults = state.setdefault("defaults", {})
            actors = state.setdefault("actors", {})
            record = actors.setdefault(actor, {"id": identifier, "poses": {}})
            if not select and record["id"] != identifier:
                return
            record["id"] = identifier
            offsets = _pose(offsets)
            if identifier is not None:
                record["poses"][identifier] = offsets
                if remember or not select or "retarget" in offsets:
                    state.setdefault("presets", {})[identifier] = offsets
            if select or defaults.get(slot, {}).get("actor") == actor:
                defaults[slot] = {"actor": actor, "id": identifier, "offsets": offsets}
            if retarget_preset is not None:
                preset = _retarget_preset(retarget_preset)
                if not select or not entry or preset["kind"] != entry[0]["kind"]:
                    raise RetargetPresetError("Retarget preset does not match the selected character")
                self._save_retarget_preset(state, preset, source=identifier)
            self._write(state)
            result = {}
            if preset is not None:
                self._copy_preset_thumbnail(preset, replace=True)
                result["retarget_preset"] = self._catalog_preset(preset)
            return result

    def update_pose(self, actor, slot, identifier, transform):
        """Atomically patch the selected character without overwriting another selection."""
        with self._lock:
            state = _read_json(self._preferences)
            record = state.get('actors', {}).get(actor)
            if record is None:
                default = state.get('defaults', {}).get(slot, {})
                if default.get('id') == identifier and identifier is not None:
                    record = {'id': identifier, 'poses': {identifier: default.get('offsets', {})}}
                    state.setdefault('actors', {})[actor] = record
            if record is None or record.get('id') != identifier:
                raise ValueError('Selected character changed; query the actor before editing again')
            if self._entry(identifier) is None:
                raise FileNotFoundError('Character is not in the library')
            offsets = _pose(transform(record.get('poses', {}).get(identifier, {})))
            record.setdefault('poses', {})[identifier] = offsets
            state.setdefault('presets', {})[identifier] = offsets
            if state.get('defaults', {}).get(slot, {}).get('actor') == actor:
                state['defaults'][slot] = {'actor': actor, 'id': identifier, 'offsets': offsets}
            self._write(state)
            return offsets

    def forget_actors(self, ids):
        if not ids:
            return
        with self._lock:
            state = _read_json(self._preferences)
            actors = state.get("actors", {})
            removed = any(identifier in actors for identifier in ids)
            if removed:
                for identifier in ids:
                    actors.pop(identifier, None)
                self._write(state)

    def _write(self, state):
        self.root.mkdir(parents=True, exist_ok=True)
        stream = NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.root, prefix=".preview-",
                                    suffix=".tmp", delete=False)
        path = Path(stream.name)
        try:
            with stream:
                json.dump(state, stream, ensure_ascii=False, separators=(",", ":"))
            for delay in (0.01, 0.025, 0.05, 0.1, 0.2, None):
                try:
                    path.replace(self._preferences)
                    break
                except PermissionError as error:
                    # Windows readers can momentarily block an atomic replace.
                    if delay is None or getattr(error, "winerror", None) not in (5, 32, 33):
                        raise
                    sleep(delay)
        finally:
            path.unlink(missing_ok=True)
