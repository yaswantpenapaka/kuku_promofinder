"""Screenplay ingestion, parsing, and analytics for the Kuku promo prototype."""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Iterable


TIMESTAMP_RE = re.compile(r"^\d{1,2}:\d{2}\.\d{1,3}$")
TIMESTAMP_PAIR_RE = re.compile(
    r"Start:\s*\((\d{1,2}:\d{2}\.\d{1,3})\)\s*\|\s*"
    r"End:\s*\((\d{1,2}:\d{2}\.\d{1,3})\)"
)
SCENE_RE = re.compile(r"(?im)^SCENE\s+(\d+)\s*$")
EVENT_RE = re.compile(r"(?m)^\*\*(.+?):\*\*\s*$")
EMOTION_IGNORE = {"O.S.", "OS", "V.O.", "VO", "OFF-SCREEN", "CONTINUES"}
CONFLICT_TERMS = {
    "abandon", "accuse", "arrest", "attack", "bastard", "betray", "blackmail",
    "blood", "bride", "burn", "cannot marry", "cheat", "duel", "force",
    "humiliate", "kneel", "kill", "marry", "murder", "plain", "reject",
    "seize", "slap", "steal", "threat", "turn around", "weapon",
}
AGGRESSIVE_TERMS = {
    "abandon", "accuse", "arrest", "attack", "betray", "blackmail", "blood",
    "cannot marry", "cruel", "force", "humiliate", "kill", "murder", "reject",
    "seize", "slap", "threat",
}


def parse_time(timestamp: str) -> float:
    """Convert MM:SS.ss to seconds."""
    if not TIMESTAMP_RE.fullmatch(timestamp.strip()):
        raise ValueError(f"Invalid screenplay timestamp: {timestamp!r}")
    minutes, seconds = timestamp.strip().split(":")
    parsed_seconds = float(seconds)
    if parsed_seconds >= 60:
        raise ValueError(f"Invalid screenplay timestamp: {timestamp!r}")
    return int(minutes) * 60 + parsed_seconds


def format_time(seconds: float) -> str:
    """Format non-negative seconds as MM:SS.ss."""
    if seconds < 0:
        raise ValueError("A screenplay timestamp cannot be negative.")
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    return f"{minutes:02d}:{remainder:05.2f}"


def _union_duration(intervals: Iterable[tuple[float, float]]) -> float:
    ordered = sorted((start, end) for start, end in intervals if end > start)
    if not ordered:
        return 0.0
    total = 0.0
    current_start, current_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            total += current_end - current_start
            current_start, current_end = start, end
    return total + current_end - current_start


@dataclass
class ScriptEvent:
    kind: str
    start: float
    end: float
    text: str
    speaker: str | None = None
    tones: list[str] = field(default_factory=list)


@dataclass
class Scene:
    number: int
    heading: str
    start: float
    end: float
    events: list[ScriptEvent] = field(default_factory=list)
    conflict_intensity: float = 0.0


@dataclass
class Episode:
    number: int
    filename: str
    title: str
    logline: str
    continuity_notes: str
    scenes: list[Scene] = field(default_factory=list)

    @property
    def runtime(self) -> float:
        return max((scene.end for scene in self.scenes), default=0.0)


@dataclass
class ScriptAnalytics:
    show_title: str
    episodes: list[Episode]
    total_runtime_sec: float
    cast: list[dict]
    tone_frequencies: dict[str, int]
    theme_frequencies: dict[str, int]
    scene_conflicts: list[dict]
    character_roster: dict
    beat_registry: dict
    episode_endings: dict

    def registries(self) -> dict[str, dict]:
        return {
            "character_roster.json": self.character_roster,
            "beat_registry.json": self.beat_registry,
            "episode_endings.json": self.episode_endings,
        }


class ScreenplayParser:
    """Parse screenplay text from episode files, directories, or ZIP archives."""

    MAX_EPISODE_BYTES = 5 * 1024 * 1024
    MAX_ARCHIVE_BYTES = 100 * 1024 * 1024

    @staticmethod
    def extract_zip(zip_path: str | Path, target_dir: str | Path) -> list[Path]:
        """Safely extract episode text files into target_dir."""
        target = Path(target_dir).resolve()
        target.mkdir(parents=True, exist_ok=True)
        extracted: list[Path] = []
        total_size = 0
        with zipfile.ZipFile(zip_path) as archive:
            for entry in archive.infolist():
                if entry.is_dir() or not entry.filename.lower().endswith(".txt"):
                    continue
                relative = Path(entry.filename)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError(f"Unsafe path in screenplay archive: {entry.filename}")
                if entry.file_size > ScreenplayParser.MAX_EPISODE_BYTES:
                    raise ValueError(f"Episode is too large to process: {entry.filename}")
                total_size += entry.file_size
                if total_size > ScreenplayParser.MAX_ARCHIVE_BYTES:
                    raise ValueError("Screenplay archive exceeds the 100 MB processing limit.")
                destination = (target / relative).resolve()
                if not destination.is_relative_to(target):
                    raise ValueError(f"Unsafe path in screenplay archive: {entry.filename}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(entry))
                extracted.append(destination)
        return extracted

    def parse_archive(self, source: bytes | bytearray | BinaryIO | str | Path) -> list[Episode]:
        """Parse .txt files directly from a ZIP without extracting untrusted paths."""
        archive_source: bytes | BinaryIO | str | Path
        archive_source = bytes(source) if isinstance(source, bytearray) else source
        episodes: list[Episode] = []
        total_size = 0
        with zipfile.ZipFile(io.BytesIO(archive_source) if isinstance(archive_source, bytes) else archive_source) as archive:
            for entry in archive.infolist():
                if entry.is_dir() or not entry.filename.lower().endswith(".txt"):
                    continue
                if Path(entry.filename).is_absolute() or ".." in Path(entry.filename).parts:
                    raise ValueError(f"Unsafe path in screenplay archive: {entry.filename}")
                if entry.file_size > self.MAX_EPISODE_BYTES:
                    raise ValueError(f"Episode is too large to process: {entry.filename}")
                total_size += entry.file_size
                if total_size > self.MAX_ARCHIVE_BYTES:
                    raise ValueError("Screenplay archive exceeds the 100 MB processing limit.")
                text = archive.read(entry).decode("utf-8-sig", errors="replace")
                episodes.append(self.parse_text(text, entry.filename))
        return sorted(episodes, key=lambda episode: (episode.number, episode.filename))

    def parse_directory(self, directory: str | Path) -> list[Episode]:
        """Parse all screenplay text files below a local directory."""
        paths = sorted(Path(directory).rglob("*.txt"))
        episodes: list[Episode] = []
        total_size = 0
        for path in paths:
            size = path.stat().st_size
            if size > self.MAX_EPISODE_BYTES:
                raise ValueError(f"Episode is too large to process: {path.name}")
            total_size += size
            if total_size > self.MAX_ARCHIVE_BYTES:
                raise ValueError("Screenplay directory exceeds the 100 MB processing limit.")
            episodes.append(self.parse_text(path.read_text(encoding="utf-8-sig", errors="replace"), path.name))
        return sorted(episodes, key=lambda episode: (episode.number, episode.filename))

    def parse_episode(self, file_path: str | Path) -> Episode:
        path = Path(file_path)
        if path.stat().st_size > self.MAX_EPISODE_BYTES:
            raise ValueError(f"Episode is too large to process: {path.name}")
        return self.parse_text(path.read_text(encoding="utf-8-sig", errors="replace"), path.name)

    def parse_text(self, text: str, filename: str = "episode-001.txt") -> Episode:
        episode_match = re.search(r"(?im)^EPISODE NUMBER:\s*(?:Episode\s*)?(\d+)", text)
        filename_match = re.search(r"(?i)episode[- _]?(\d+)", filename)
        number = int(episode_match.group(1)) if episode_match else int(filename_match.group(1)) if filename_match else 0
        title = re.sub(r"(?i)^episode[- _]?\d+[- _]*", "", Path(filename).stem).strip(" _-")
        logline = self._metadata(text, "EPISODE LOGLINE")
        continuity = self._metadata(text, "CONTINUITY NOTES")
        scenes: list[Scene] = []
        scene_matches = list(SCENE_RE.finditer(text))
        for index, match in enumerate(scene_matches):
            block_end = scene_matches[index + 1].start() if index + 1 < len(scene_matches) else len(text)
            block = text[match.end():block_end]
            start_match = re.search(r"(?im)^Scene start:\s*(\d{1,2}:\d{2}\.\d{1,3})\s*$", block)
            end_match = re.search(r"(?im)^Scene end:\s*(\d{1,2}:\d{2}\.\d{1,3})\s*$", block)
            if not start_match or not end_match:
                continue
            scene_start, scene_end = parse_time(start_match.group(1)), parse_time(end_match.group(1))
            if scene_end < scene_start:
                raise ValueError(f"Scene {match.group(1)} in {filename} ends before it starts.")
            heading_match = re.search(r"(?im)^Scene heading:\s*(.+?)\s*$", block)
            heading = heading_match.group(1).strip() if heading_match else ""
            events: list[ScriptEvent] = []
            event_matches = list(EVENT_RE.finditer(block))
            for event_index, event_match in enumerate(event_matches):
                event_end = event_matches[event_index + 1].start() if event_index + 1 < len(event_matches) else len(block)
                event_block = block[event_match.end():event_end]
                times = TIMESTAMP_PAIR_RE.search(event_block)
                if not times:
                    continue
                event_start, event_finish = parse_time(times.group(1)), parse_time(times.group(2))
                if event_finish < event_start:
                    continue
                label = event_match.group(1).strip()
                text_after_time = event_block[times.end():].strip()
                event_text = text_after_time.strip(" \t\r\n-")
                kind, speaker, tones = self._parse_label(label)
                events.append(ScriptEvent(kind, event_start, event_finish, event_text, speaker, tones))
            scene = Scene(int(match.group(1)), heading, scene_start, scene_end, events)
            scene.conflict_intensity = self._scene_conflict(scene)
            scenes.append(scene)
        return Episode(number, filename, title, logline, continuity, scenes)

    @staticmethod
    def _metadata(text: str, label: str) -> str:
        match = re.search(rf"(?im)^{re.escape(label)}:\s*(.+?)\s*$", text)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _parse_label(label: str) -> tuple[str, str | None, list[str]]:
        cleaned = label.strip().rstrip(":")
        normalized = re.sub(r"\s+", " ", cleaned).strip()
        if normalized.upper() in {"CAMERA", "ACTION", "SOUND FX"}:
            return normalized.upper(), None, []
        parts = re.findall(r"\(([^()]*)\)", normalized)
        tones = [
            item.strip()
            for part in parts
            for item in re.split(r"[,/]", part)
            if item.strip() and item.strip().upper() not in EMOTION_IGNORE
        ]
        speaker = re.sub(r"\s*\([^)]*\)", "", normalized).strip()
        speaker = re.sub(r"\s+", " ", speaker).strip()
        return "DIALOGUE", speaker or None, tones

    @classmethod
    def _scene_conflict(cls, scene: Scene) -> float:
        if not scene.events:
            return 0.0
        score = 0
        content = " ".join(event.text.lower() for event in scene.events)
        for term in CONFLICT_TERMS:
            score += min(content.count(term), 3)
        score += sum(
            1
            for event in scene.events
            for tone in event.tones
            if tone.lower() in {"furious", "mocking", "smug", "desperate", "threatening", "cruel", "gloating"}
        )
        return round(min(100.0, score * 7.5 + min(len(scene.events), 8) * 1.5), 1)

    def compute_analytics(self, episodes: list[Episode], show_title: str = "") -> ScriptAnalytics:
        if not episodes:
            raise ValueError("No screenplay episodes were found in the selected input.")
        if not show_title:
            show_title = self._show_title(episodes)

        tone_counts: Counter[str] = Counter()
        theme_counts: Counter[str] = Counter()
        intervals: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
        dialogue_counts: Counter[str] = Counter()
        first_seen: dict[str, tuple[int, float, str, str]] = {}
        line_by_character: dict[str, str] = {}
        scene_conflicts: list[dict] = []
        beat_scenes: list[dict] = []
        character_names = {
            event.speaker
            for episode in episodes
            for scene in episode.scenes
            for event in scene.events
            if event.kind == "DIALOGUE" and event.speaker
        }
        name_aliases = self._character_aliases(character_names)

        for episode in episodes:
            for scene in episode.scenes:
                participants: set[str] = set()
                for event in scene.events:
                    tone_counts.update(event.tones)
                    for character, aliases in name_aliases.items():
                        if (
                            event.speaker
                            and self._canonical_name(event.speaker, name_aliases) == character
                        ) or any(
                            re.search(rf"(?i)(?<!\w){re.escape(alias)}(?!\w)", event.text)
                            for alias in aliases
                        ):
                            participants.add(character)
                            if event.kind in {"DIALOGUE", "ACTION"}:
                                intervals[character].append((episode.number, event.start, event.end))
                            if character not in first_seen or event.start < first_seen[character][1] and episode.number <= first_seen[character][0]:
                                description = event.text or scene.heading
                                first_seen[character] = (
                                    episode.number, event.start, scene.heading, description
                                )
                    if event.kind == "DIALOGUE" and event.speaker:
                        speaker = self._canonical_name(event.speaker, name_aliases)
                        dialogue_counts[speaker] += 1
                        line_by_character.setdefault(speaker, self._first_line(event.text))
                        if speaker not in first_seen:
                            first_seen[speaker] = (episode.number, event.start, scene.heading, event.text)
                    if event.kind == "DIALOGUE":
                        text = event.text.lower()
                        for theme, cues in {
                            "Betrayal": ("betray", "abandon", "stole", "cheat", "marrying me"),
                            "Arrogance": ("plain", "unworthy", "kneel", "know your place", "beneath"),
                            "Defiance": ("never", "won't", "refuse", "stand", "you knew"),
                            "Rage": ("furious", "kill", "blood", "traitor", "bastard"),
                            "Heartbreak": ("tears", "heartbroken", "why", "no...", "can't believe"),
                        }.items():
                            if any(cue in text for cue in cues):
                                theme_counts[theme] += 1
                scene_record = {
                    "episode": episode.number,
                    "scene": scene.number,
                    "heading": scene.heading,
                    "start_time": format_time(scene.start),
                    "end_time": format_time(scene.end),
                    "conflict_intensity": scene.conflict_intensity,
                    "involved_characters": sorted(participants),
                }
                scene_conflicts.append(scene_record)
                scene_text = " ".join(event.text.lower() for event in scene.events)
                if scene.conflict_intensity >= 15 or any(term in scene_text for term in CONFLICT_TERMS):
                    beat_scenes.append({
                        **scene_record,
                        "high_stakes_events": [
                            {
                                "start_time": format_time(event.start),
                                "end_time": format_time(event.end),
                                "kind": event.kind,
                                "speaker": event.speaker,
                                "text": event.text,
                            }
                            for event in scene.events
                            if any(term in event.text.lower() for term in CONFLICT_TERMS)
                            or event.tones
                        ],
                    })

        cast: list[dict] = []
        for character in sorted(set(name_aliases) | set(dialogue_counts)):
            intervals_by_episode: dict[int, list[tuple[float, float]]] = defaultdict(list)
            for episode_number, start, end in intervals[character]:
                intervals_by_episode[episode_number].append((start, end))
            screentime = sum(_union_duration(items) for items in intervals_by_episode.values())
            introduction = first_seen.get(character)
            cast.append({
                "character": character,
                "spoken_lines": dialogue_counts[character],
                "screentime_sec": round(screentime, 2),
                "appearances": sorted(intervals_by_episode),
                "first_appearance": (
                    {
                        "episode": introduction[0],
                        "timestamp": format_time(introduction[1]),
                        "scene_description": introduction[2] + (
                            f" — {introduction[3][:220]}" if introduction[3] else ""
                        ),
                        "first_line": line_by_character.get(character, ""),
                    }
                    if introduction else None
                ),
            })
        cast.sort(key=lambda item: (-item["screentime_sec"], -item["spoken_lines"], item["character"]))
        endings = self._episode_endings(episodes)
        return ScriptAnalytics(
            show_title=show_title,
            episodes=episodes,
            total_runtime_sec=round(sum(episode.runtime for episode in episodes), 2),
            cast=cast,
            tone_frequencies=dict(tone_counts.most_common()),
            theme_frequencies=dict(theme_counts.most_common()),
            scene_conflicts=sorted(
                scene_conflicts,
                key=lambda item: (-item["conflict_intensity"], item["episode"], item["scene"]),
            ),
            character_roster={
                "show_title": show_title,
                "characters": cast,
            },
            beat_registry={
                "show_title": show_title,
                "beats": sorted(
                    beat_scenes,
                    key=lambda item: (-item["conflict_intensity"], item["episode"], item["scene"]),
                ),
            },
            episode_endings=endings,
        )

    @staticmethod
    def _character_aliases(names: set[str | None]) -> dict[str, set[str]]:
        usable_names = {re.sub(r"\s+", " ", name).strip() for name in names if name}
        aliases: dict[str, set[str]] = {}
        for name in usable_names:
            canonical = "LADY JULIANA" if name.upper() in {"JULIANA", "LADY JULIANA"} and any(
                candidate.upper() == "LADY JULIANA" for candidate in usable_names
            ) else name
            aliases.setdefault(canonical, set()).add(name)
        for name in list(aliases):
            aliases[name].add(name)
            for title in ("LADY ", "LORD ", "DUCHESS ", "DUKE "):
                if name.upper().startswith(title) and len(name) > len(title):
                    aliases[name].add(name[len(title):])
        return aliases

    @staticmethod
    def _canonical_name(name: str, aliases: dict[str, set[str]]) -> str:
        for canonical, variations in aliases.items():
            if name in variations:
                return canonical
        return name

    @staticmethod
    def _first_line(text: str) -> str:
        match = re.search(r'"([^"]+)"', text)
        return match.group(1) if match else text.strip()

    @staticmethod
    def _show_title(episodes: list[Episode]) -> str:
        parents = [Path(episode.filename).parent.name for episode in episodes]
        common = Counter(name for name in parents if name not in {"", "."}).most_common(1)
        return common[0][0] if common else "Microdrama Series"

    @staticmethod
    def _episode_endings(episodes: list[Episode]) -> dict:
        records = []
        for episode in episodes:
            ending = episode.runtime
            window_start = max(0.0, ending - 20.0)
            final_events = [
                {
                    "start_time": format_time(event.start),
                    "end_time": format_time(event.end),
                    "kind": event.kind,
                    "speaker": event.speaker,
                    "text": event.text,
                    "tones": event.tones,
                }
                for scene in episode.scenes
                for event in scene.events
                if event.end >= window_start
            ]
            records.append({
                "episode": episode.number,
                "runtime_sec": round(ending, 2),
                "window_start": format_time(window_start),
                "window_end": format_time(ending),
                "cliffhanger_lines": [
                    item for item in final_events
                    if item["kind"] == "DIALOGUE" and item["text"]
                ],
                "audio_swells": [
                    item for item in final_events
                    if item["kind"] == "SOUND FX" and item["text"]
                ],
                "final_events": final_events,
            })
        return {"episodes": records}


def write_registries(analytics: ScriptAnalytics, output_dir: str | Path) -> dict[str, Path]:
    """Persist the three screening registries and return their file paths."""
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for filename, registry in analytics.registries().items():
        path = destination / filename
        path.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")
        written[filename] = path
    return written
