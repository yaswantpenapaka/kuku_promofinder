"""Validated Gemini integration and deterministic offline promo generation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

import httpx
from google import genai
from google.genai import errors, types

from parser import Episode, ScriptAnalytics, Scene, ScriptEvent, format_time, parse_time


HOOK_CUES = (
    "abandon", "accuse", "arrest", "betray", "cannot marry", "cheat", "humiliate",
    "kneel", "plain", "reject", "slap", "steal", "threat", "turn around",
    "unworthy", "you are marrying", "you should turn around",
)
CLIFF_CUES = (
    "duke", "kill", "kneel", "marry", "proposal", "propose", "reveal", "secret",
    "sword", "threat", "who are you", "will you", "i am", "i will", "you are",
    "you can't", "you cannot",
)
HIGH_DRAMA_TONES = {
    "bitter", "callous", "cruel", "desperate", "devastated", "fierce", "furious",
    "gloating", "imposing", "mocking", "outraged", "scornful", "smug", "threatening",
}


class PromoValidationError(ValueError):
    """Raised when an LLM promo response does not map to the parsed screenplay."""


def source_digest(source: bytes) -> str:
    return hashlib.sha256(source).hexdigest()


class PromoEngine:
    def __init__(
        self,
        api_key: str = "",
        model_name: str = "gemini-2.0-flash",
        cache_path: str | Path | None = None,
        prompt_path: str | Path | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.model_name = model_name.strip() or "gemini-2.0-flash"
        self.cache_path = Path(cache_path) if cache_path else Path(__file__).with_name("cached_promos.json")
        self.prompt_path = Path(prompt_path) if prompt_path else Path(__file__).with_name("prompt.md")

    def generate_promos(
        self,
        analytics: ScriptAnalytics,
        source_hash: str,
    ) -> tuple[dict, str, str | None]:
        """Return response, generation mode, and any visible fallback explanation."""
        cached = self._load_cache(source_hash, analytics)
        if not self.api_key:
            if cached:
                return cached, "Verified offline cache", None
            return self._offline_response(analytics), "Offline analysis", None

        try:
            response = self._request_gemini(analytics)
            self.validate_response(response, analytics)
            return response, f"Gemini ({self.model_name})", None
        except (
            errors.APIError,
            httpx.HTTPError,
            PromoValidationError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            if cached:
                return cached, "Verified offline cache", f"Gemini response unavailable or invalid; using the verified cache. Details: {exc}"
            return self._offline_response(analytics), "Offline analysis", f"Gemini response unavailable or invalid; using validated offline cuts. Details: {exc}"

    def _request_gemini(self, analytics: ScriptAnalytics) -> dict:
        client = genai.Client(api_key=self.api_key)
        contents = self._user_prompt(analytics)
        result = client.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=self._system_prompt(),
                response_mime_type="application/json",
                temperature=0.25,
            ),
        )
        if not result.text:
            raise PromoValidationError("Gemini returned an empty response.")
        return json.loads(result.text)

    def _user_prompt(self, analytics: ScriptAnalytics) -> str:
        template = self._prompt_template()
        roster = analytics.character_roster
        beats = analytics.beat_registry.get("beats", [])
        endings = analytics.episode_endings.get("episodes", [])
        values = {
            "{show_title}": analytics.show_title,
            "{total_episodes}": str(len(analytics.episodes)),
            "{top_emotions}": json.dumps(analytics.theme_frequencies or analytics.tone_frequencies, ensure_ascii=False),
            "{top_cast_summary}": json.dumps(analytics.cast[:15], ensure_ascii=False),
            "{character_roster_json}": json.dumps(roster, ensure_ascii=False),
            "{beat_registry_json}": json.dumps(beats[:40], ensure_ascii=False),
            "{episode_endings_json}": json.dumps(endings, ensure_ascii=False),
        }
        for marker, value in values.items():
            template = template.replace(marker, value)
        return (
            template
            + "\n\nTimestamps must be copied from exact source scene/event intervals. "
            "Do not invent script text. The application rejects any segment outside its episode's scene bounds "
            "or any promo whose recomputed duration is not between 20 and 60 seconds."
        )

    def _system_prompt(self) -> str:
        content = self.prompt_path.read_text(encoding="utf-8")
        match = re.search(
            r"##\s*1\.\s*System Prompt\s+```text\s*(.*?)```",
            content,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not match:
            raise ValueError(f"Could not find the system prompt in {self.prompt_path}.")
        return match.group(1).strip()

    def _prompt_template(self) -> str:
        content = self.prompt_path.read_text(encoding="utf-8")
        match = re.search(
            r"#{2,3}\s*2\.\s*Dynamic User Prompt Template\s+```text\s*(.*?)```",
            content,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not match:
            raise ValueError(f"Could not find the dynamic user prompt template in {self.prompt_path}.")
        return match.group(1).strip()

    def _load_cache(self, source_hash: str, analytics: ScriptAnalytics) -> dict | None:
        if not self.cache_path.is_file():
            return None
        try:
            cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if cache.get("source_digest") != source_hash or not isinstance(cache.get("response"), dict):
            return None
        try:
            self.validate_response(cache["response"], analytics)
        except (PromoValidationError, KeyError, TypeError, ValueError):
            return None
        return cache["response"]

    @classmethod
    def validate_response(cls, response: dict, analytics: ScriptAnalytics) -> None:
        if not isinstance(response, dict) or not isinstance(response.get("promos"), list):
            raise PromoValidationError("Promo output must contain a promos array.")
        genre = response.get("genre_analysis")
        if not isinstance(genre, dict) or any(
            not isinstance(genre.get(field), str) or not genre[field].strip()
            for field in ("primary_genre", "marketing_hook_summary")
        ):
            raise PromoValidationError("Genre analysis is missing its required text fields.")
        if not isinstance(genre.get("sub_genres"), list) or not all(
            isinstance(value, str) and value.strip() for value in genre["sub_genres"]
        ):
            raise PromoValidationError("Genre analysis must include a list of sub-genres.")
        promos = response["promos"]
        if len(promos) != 10:
            raise PromoValidationError(f"Expected 10 promos, received {len(promos)}.")
        episodes_by_number = {episode.number: episode for episode in analytics.episodes}
        ranks: set[int] = set()
        for promo in promos:
            try:
                rank = int(promo["rank"])
                segments = promo["segments"]
            except (KeyError, TypeError, ValueError) as exc:
                raise PromoValidationError("A promo is missing its rank or segment list.") from exc
            for field in (
                "promo_title", "promo_type", "target_audience_appeal",
                "why_it_works", "posting_priority_rationale",
            ):
                if not isinstance(promo.get(field), str) or not promo[field].strip():
                    raise PromoValidationError(f"Promo {rank} is missing required text: {field}.")
            for field, timestamp_key in (("hook", "timestamp_start"), ("cliffhanger", "timestamp_end")):
                details = promo.get(field)
                if (
                    not isinstance(details, dict)
                    or not isinstance(details.get("description"), str)
                    or not details["description"].strip()
                ):
                    raise PromoValidationError(f"Promo {rank} is missing its {field} description.")
                try:
                    parse_time(str(details[timestamp_key]))
                except (KeyError, ValueError) as exc:
                    raise PromoValidationError(f"Promo {rank} has an invalid {field} timestamp.") from exc
            if rank in ranks or not 1 <= rank <= 10:
                raise PromoValidationError(f"Promo rank {rank} is invalid or duplicated.")
            ranks.add(rank)
            if not isinstance(segments, list) or not 1 <= len(segments) <= 3:
                raise PromoValidationError(f"Promo {rank} must contain one to three segments.")
            if len(segments) == 1 and promo.get("promo_type") != "Single-Scene Punch":
                raise PromoValidationError(f"Promo {rank} has a single segment but the wrong promo type.")
            if len(segments) > 1 and promo.get("promo_type") != "Multi-Segment Arc":
                raise PromoValidationError(f"Promo {rank} has multiple segments but the wrong promo type.")
            duration = 0.0
            actual_starts: list[float] = []
            actual_ends: list[float] = []
            for segment_index, segment in enumerate(segments, start=1):
                try:
                    episode_number = int(segment["episode"])
                    start = parse_time(str(segment["start_time"]))
                    end = parse_time(str(segment["end_time"]))
                except (KeyError, TypeError, ValueError) as exc:
                    raise PromoValidationError(f"Promo {rank} contains an invalid timestamp.") from exc
                if int(segment.get("order", segment_index)) != segment_index:
                    raise PromoValidationError(f"Promo {rank} segment order is not sequential.")
                if not isinstance(segment.get("role"), str) or not segment["role"].strip():
                    raise PromoValidationError(f"Promo {rank} segment {segment_index} is missing its role.")
                if end <= start:
                    raise PromoValidationError(f"Promo {rank} contains an empty or reversed segment.")
                actual_starts.append(start)
                actual_ends.append(end)
                episode = episodes_by_number.get(episode_number)
                if episode is None or not any(
                    start >= scene.start - 0.01 and end <= scene.end + 0.01 for scene in episode.scenes
                ):
                    raise PromoValidationError(
                        f"Promo {rank} segment {episode_number} [{segment['start_time']}-{segment['end_time']}] "
                        "does not fit within a single parsed scene."
                    )
                actual = end - start
                segment_duration = float(segment.get("duration_sec", actual))
                if not math.isfinite(segment_duration) or abs(segment_duration - actual) > 0.1:
                    raise PromoValidationError(f"Promo {rank} segment duration does not match its timestamps.")
                duration += actual
            if abs(parse_time(str(promo["hook"]["timestamp_start"])) - actual_starts[0]) > 0.1:
                raise PromoValidationError(f"Promo {rank} hook timestamp does not match its first segment.")
            if abs(parse_time(str(promo["cliffhanger"]["timestamp_end"])) - actual_ends[-1]) > 0.1:
                raise PromoValidationError(f"Promo {rank} cliffhanger timestamp does not match its last segment.")
            if not 20.0 <= duration <= 60.0:
                raise PromoValidationError(
                    f"Promo {rank} totals {duration:.2f} seconds; each promo must be 20–60 seconds."
                )
            if len(segments) == 1 and not 25.0 <= duration <= 55.0:
                raise PromoValidationError(
                    f"Single-scene promo {rank} totals {duration:.2f} seconds; it must be 25–55 seconds."
                )
            total_duration = float(promo.get("total_duration_sec", duration))
            if not math.isfinite(total_duration) or abs(total_duration - duration) > 0.1:
                raise PromoValidationError(f"Promo {rank} total duration does not match its segments.")
        if ranks != set(range(1, 11)):
            raise PromoValidationError("Promo ranks must be numbered 1 through 10.")

    @classmethod
    def _offline_response(cls, analytics: ScriptAnalytics) -> dict:
        """Create ten timestamp-grounded cuts without inventing script text."""
        candidates = cls._single_scene_candidates(analytics.episodes)
        candidates.extend(cls._multi_segment_candidates(analytics.episodes))
        chosen = cls._rank_distinct(candidates)
        if len(chosen) < 10:
            chosen.extend(cls._scene_fallbacks(analytics.episodes, chosen))
        chosen = cls._rank_distinct(chosen)[:10]
        if len(chosen) < 10:
            raise PromoValidationError(
                f"Only {len(chosen)} valid 20–60 second cuts could be found; at least 10 are required."
            )
        promos: list[dict] = []
        for index, candidate in enumerate(chosen, start=1):
            segment_specs = []
            for order, segment in enumerate(candidate["segments"], start=1):
                segment_specs.append({
                    "order": order,
                    "episode": segment["episode"],
                    "start_time": format_time(segment["start"]),
                    "end_time": format_time(segment["end"]),
                    "duration_sec": round(segment["end"] - segment["start"], 2),
                    "role": segment["role"],
                })
            first = candidate["segments"][0]
            last = candidate["segments"][-1]
            promos.append({
                "rank": index,
                "promo_title": candidate["title"],
                "promo_type": "Single-Scene Punch" if len(segment_specs) == 1 else "Multi-Segment Arc",
                "target_audience_appeal": candidate["appeal"],
                "total_duration_sec": round(sum(item["duration_sec"] for item in segment_specs), 2),
                "segments": segment_specs,
                "hook": {
                    "timestamp_start": format_time(first["start"]),
                    "description": candidate["hook"],
                },
                "cliffhanger": {
                    "timestamp_end": format_time(last["end"]),
                    "description": candidate["cliffhanger"],
                },
                "why_it_works": candidate["why"],
                "posting_priority_rationale": (
                    "Prioritized by source-scene conflict intensity, immediate hook cues, and a high-stakes ending. "
                    "Offline ranking is heuristic and should be reviewed by an editor."
                ),
            })
        analysis = {
            "primary_genre": cls._infer_genre(analytics),
            "sub_genres": ["Revenge Drama", "Aristocratic Romance"],
            "marketing_hook_summary": (
                "A betrayed heroine confronts public humiliation and fights for control as powerful rivals "
                "and unexpected allies reshape her future."
            ),
        }
        return {"genre_analysis": analysis, "promos": promos}

    @classmethod
    def _single_scene_candidates(cls, episodes: list[Episode]) -> list[dict]:
        candidates: list[dict] = []
        for episode in episodes:
            for scene in episode.scenes:
                hooks = [event for event in scene.events if cls._is_hook(event)]
                endings = [event for event in scene.events if cls._is_cliffhanger(event)]
                for hook_event in hooks:
                    for ending_event in endings:
                        duration = ending_event.end - hook_event.start
                        if ending_event.end <= hook_event.start or not 25.0 <= duration <= 55.0:
                            continue
                        if not cls._is_hook_early(scene, hook_event):
                            continue
                        if ending_event.start < hook_event.start:
                            continue
                        candidates.append(cls._candidate(
                            [
                                {
                                    "episode": episode.number,
                                    "scene": scene.number,
                                    "start": hook_event.start,
                                    "end": ending_event.end,
                                    "role": f"Hook and escalation: {cls._brief(hook_event.text)}",
                                }
                            ],
                            hook_event,
                            ending_event,
                            scene.conflict_intensity + cls._event_score(hook_event) + cls._event_score(ending_event),
                            "A continuous escalation builds from an immediate conflict cue to a timed dramatic peak.",
                        ))
        return candidates

    @classmethod
    def _multi_segment_candidates(cls, episodes: list[Episode]) -> list[dict]:
        hooks: list[tuple[Episode, Scene, ScriptEvent]] = []
        cliffhangers: list[tuple[Episode, Scene, ScriptEvent]] = []
        for episode in episodes:
            for scene in episode.scenes:
                hooks.extend((episode, scene, event) for event in scene.events if cls._is_hook(event))
                cliffhangers.extend(
                    (episode, scene, event) for event in scene.events if cls._is_cliffhanger(event)
                )
        hooks.sort(key=lambda item: cls._event_score(item[2]) + item[1].conflict_intensity, reverse=True)
        cliffhangers.sort(key=lambda item: cls._event_score(item[2]) + item[1].conflict_intensity, reverse=True)
        candidates: list[dict] = []
        for hook_episode, hook_scene, hook_event in hooks[:120]:
            if not cls._is_hook_early(hook_scene, hook_event):
                continue
            first_end = min(hook_scene.end, hook_event.start + 14.0)
            first_duration = first_end - hook_event.start
            if first_duration < 8.0:
                continue
            for end_episode, end_scene, end_event in cliffhangers[:120]:
                final_end = end_event.end
                second_start = max(end_scene.start, final_end - 18.0)
                second_duration = final_end - second_start
                if not 15.0 <= second_duration <= 30.0:
                    continue
                if hook_episode.number == end_episode.number and hook_scene.number == end_scene.number:
                    if second_start < first_end and hook_event.start < final_end:
                        continue
                duration = first_duration + second_duration
                if not 20.0 <= duration <= 60.0:
                    continue
                segments = [
                    {
                        "episode": hook_episode.number,
                        "scene": hook_scene.number,
                        "start": hook_event.start,
                        "end": first_end,
                        "role": f"Hook: {cls._brief(hook_event.text)}",
                    },
                    {
                        "episode": end_episode.number,
                        "scene": end_scene.number,
                        "start": second_start,
                        "end": final_end,
                        "role": f"Cliffhanger: {cls._brief(end_event.text)}",
                    },
                ]
                score = (
                    hook_scene.conflict_intensity + end_scene.conflict_intensity
                    + cls._event_score(hook_event) + cls._event_score(end_event)
                )
                candidates.append(cls._candidate(
                    segments,
                    hook_event,
                    end_event,
                    score,
                    "The first beat establishes an immediate injustice; the second resolves into a separate high-stakes reveal.",
                ))
        return candidates

    @classmethod
    def _scene_fallbacks(cls, episodes: list[Episode], existing: list[dict]) -> list[dict]:
        already = {
            tuple((seg["episode"], seg["scene"], round(seg["start"], 1), round(seg["end"], 1)) for seg in item["segments"])
            for item in existing
        }
        candidates: list[dict] = []
        for episode in episodes:
            for scene in episode.scenes:
                duration = min(40.0, scene.end - scene.start)
                if duration < 25.0:
                    continue
                start = scene.start
                end = start + duration
                closing = next(
                    (event for event in reversed(scene.events) if event.end <= end and event.text),
                    scene.events[-1] if scene.events else None,
                )
                opening = next((event for event in scene.events if event.start >= start), None)
                if closing is None or opening is None:
                    continue
                candidate = cls._candidate(
                    [{
                        "episode": episode.number,
                        "scene": scene.number,
                        "start": start,
                        "end": end,
                        "role": "Continuous scene excerpt; editorial review recommended",
                    }],
                    opening,
                    closing,
                    scene.conflict_intensity,
                    "A continuous scene excerpt offers a self-contained test cut; confirm hook and ending in editorial review.",
                )
                key = tuple((seg["episode"], seg["scene"], round(seg["start"], 1), round(seg["end"], 1)) for seg in candidate["segments"])
                if key not in already:
                    candidates.append(candidate)
                    already.add(key)
        return candidates

    @classmethod
    def _candidate(
        cls,
        segments: list[dict],
        hook_event: ScriptEvent,
        ending_event: ScriptEvent,
        score: float,
        why: str,
    ) -> dict:
        hook_text = cls._brief(hook_event.text)
        ending_text = cls._brief(ending_event.text)
        return {
            "segments": segments,
            "score": score,
            "hook": f"Opens on: {hook_text}" if hook_text else "Opens on a tense screenplay beat.",
            "cliffhanger": f"Ends on: {ending_text}" if ending_text else "Ends on an unresolved dramatic beat.",
            "title": f"{'Power reversal' if len(segments) > 1 else 'Single-scene cut'}: {hook_text[:58] or 'A dramatic turning point'}",
            "appeal": "Viewers drawn to betrayal, status conflict, and a heroine facing public pressure.",
            "why": why,
        }

    @staticmethod
    def _brief(text: str, limit: int = 120) -> str:
        compact = re.sub(r"\s+", " ", text).strip().strip('"')
        return compact[:limit] + ("…" if len(compact) > limit else "")

    @staticmethod
    def _event_score(event: ScriptEvent) -> float:
        content = event.text.lower()
        score = sum(3 for cue in HOOK_CUES + CLIFF_CUES if cue in content)
        score += sum(1.5 for tone in event.tones if tone.lower() in HIGH_DRAMA_TONES)
        return score

    @classmethod
    def _is_hook(cls, event: ScriptEvent) -> bool:
        content = event.text.lower()
        return (
            any(cue in content for cue in HOOK_CUES)
            or any(tone.lower() in {"mocking", "gloating", "furious", "outraged", "desperate"} for tone in event.tones)
        )

    @classmethod
    def _is_cliffhanger(cls, event: ScriptEvent) -> bool:
        content = event.text.lower()
        return (
            any(cue in content for cue in CLIFF_CUES)
            or any(tone.lower() in {"shocked", "desperate", "imposing", "threatening"} for tone in event.tones)
            or (event.kind == "DIALOGUE" and "?" in content)
        )

    @staticmethod
    def _is_hook_early(scene: Scene, event: ScriptEvent) -> bool:
        return event.start - scene.start <= 3.0

    @classmethod
    def _rank_distinct(cls, candidates: list[dict]) -> list[dict]:
        unique: dict[tuple, dict] = {}
        for candidate in candidates:
            key = tuple(
                (
                    segment["episode"],
                    segment["scene"],
                    round(segment["start"], 1),
                    round(segment["end"], 1),
                )
                for segment in candidate["segments"]
            )
            if key not in unique or candidate["score"] > unique[key]["score"]:
                unique[key] = candidate
        ranked = sorted(unique.values(), key=lambda item: item["score"], reverse=True)
        selected: list[dict] = []
        episode_counts: dict[int, int] = {}
        hook_counts: dict[tuple, int] = {}
        ending_counts: dict[tuple, int] = {}
        for candidate in ranked:
            episode_key = candidate["segments"][0]["episode"]
            first = candidate["segments"][0]
            last = candidate["segments"][-1]
            hook_key = (first["episode"], first["scene"], round(first["start"], 1))
            ending_key = (last["episode"], last["scene"], round(last["end"], 1))
            if (
                episode_counts.get(episode_key, 0) >= 2
                or hook_counts.get(hook_key, 0) >= 1
                or ending_counts.get(ending_key, 0) >= 1
            ):
                continue
            selected.append(candidate)
            episode_counts[episode_key] = episode_counts.get(episode_key, 0) + 1
            hook_counts[hook_key] = hook_counts.get(hook_key, 0) + 1
            ending_counts[ending_key] = ending_counts.get(ending_key, 0) + 1
            if len(selected) == 10:
                break
        if len(selected) < 10:
            selected_keys = {
                tuple((seg["episode"], seg["scene"], round(seg["start"], 1), round(seg["end"], 1)) for seg in item["segments"])
                for item in selected
            }
            for candidate in ranked:
                key = tuple((seg["episode"], seg["scene"], round(seg["start"], 1), round(seg["end"], 1)) for seg in candidate["segments"])
                if key not in selected_keys:
                    selected.append(candidate)
                    selected_keys.add(key)
                    if len(selected) == 10:
                        break
        return selected

    @staticmethod
    def _infer_genre(analytics: ScriptAnalytics) -> str:
        content = " ".join(
            event.text.lower()
            for episode in analytics.episodes
            for scene in episode.scenes
            for event in scene.events
        )
        if "duke" in content and ("marry" in content or "wedding" in content):
            return "Aristocratic Romance / Revenge Drama"
        if "murder" in content or "kill" in content:
            return "Revenge Drama"
        return "Romantic Melodrama"
