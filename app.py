"""Streamlit dashboard for finding microdrama promo cuts."""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import os
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from parser import Episode, ScriptAnalytics, ScreenplayParser, format_time, parse_time, write_registries
from promo_engine import PromoEngine, PromoValidationError, source_digest


APP_DIR = Path(__file__).resolve().parent
DEFAULT_ZIP = APP_DIR.parent / "I Married a Broken Soldier. He was a Duke-script.zip"
DEFAULT_DIRECTORY = APP_DIR.parent / "I Married a Broken Soldier. He was a Duke"
OUTPUT_DIR = APP_DIR / "outputs"

load_dotenv(APP_DIR / ".env", override=True)

st.set_page_config(
    page_title="Kuku Promo Finder",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .promo-meta { color: #64748b; font-size: .9rem; }
    .script-line {
        color: #172033 !important;
        padding: .55rem .75rem;
        margin: .3rem 0;
        border-radius: .4rem;
        white-space: pre-wrap;
        line-height: 1.55;
    }
    .script-line b { color: #111827 !important; }
    .hook-line { background: #fff1f2; border-left: 4px solid #be123c; }
    .body-line { background: #fffbeb; border-left: 4px solid #b45309; }
    .cliff-line { background: #f0fdf4; border-left: 4px solid #15803d; }
    </style>
    """,
    unsafe_allow_html=True,
)


def _read_input(upload: object | None) -> tuple[list[Episode], bytes, str]:
    parser = ScreenplayParser()
    if upload is not None:
        archive_bytes = upload.getvalue()
        episodes = parser.parse_archive(archive_bytes)
        return episodes, archive_bytes, getattr(upload, "name", "uploaded-script.zip")
    if DEFAULT_ZIP.is_file():
        archive_bytes = DEFAULT_ZIP.read_bytes()
        episodes = parser.parse_archive(archive_bytes)
        return episodes, archive_bytes, DEFAULT_ZIP.name
    if DEFAULT_DIRECTORY.is_dir():
        episodes = parser.parse_directory(DEFAULT_DIRECTORY)
        digest = hashlib.sha256()
        for path in sorted(DEFAULT_DIRECTORY.rglob("*.txt")):
            digest.update(path.relative_to(DEFAULT_DIRECTORY).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
        return episodes, digest.digest(), DEFAULT_DIRECTORY.name
    raise FileNotFoundError("Upload a screenplay ZIP to start analysis.")


def _source_excerpt(
    promo: dict,
    analytics: ScriptAnalytics,
) -> str:
    episode_map = {episode.number: episode for episode in analytics.episodes}
    lines: list[str] = []
    segments = promo["segments"]
    for index, segment in enumerate(segments):
        episode = episode_map.get(int(segment["episode"]))
        if episode is None:
            continue
        start = parse_time(segment["start_time"])
        end = parse_time(segment["end_time"])
        scene = next(
            (item for item in episode.scenes if start >= item.start - .01 and end <= item.end + .01),
            None,
        )
        if scene is None:
            continue
        lines.append(
            f"SEGMENT {index + 1} — EPISODE {episode.number} / SCENE {scene.number} "
            f"[{segment['start_time']}–{segment['end_time']}]"
        )
        for event in scene.events:
            if event.end < start or event.start > end:
                continue
            if index == 0 and event.start <= start + 3:
                kind = "hook-line"
                label = "HOOK"
            elif index == len(segments) - 1 and event.end >= end - 3:
                kind = "cliff-line"
                label = "CLIFFHANGER"
            else:
                kind = "body-line"
                label = "BODY"
            speaker = f"{event.speaker}: " if event.speaker else f"{event.kind}: "
            tones = f" ({', '.join(event.tones)})" if event.tones else ""
            text = html.escape(f"{format_time(event.start)}–{format_time(event.end)} {speaker}{event.text}{tones}")
            lines.append(f'<div class="script-line {kind}"><b>{label}</b> {text}</div>')
    return "\n".join(lines)


def _clip_specs(promo: dict) -> str:
    lines = [
        f"{promo['promo_title']} ({promo['total_duration_sec']:.1f}s)",
        f"Type: {promo['promo_type']}",
    ]
    for segment in promo["segments"]:
        lines.append(
            f"Segment {segment['order']}: Ep {segment['episode']} "
            f"[{segment['start_time']} - {segment['end_time']}] "
            f"({segment['duration_sec']:.1f}s) — {segment['role']}"
        )
    return "\n".join(lines)


def _gemini_settings() -> tuple[str, str]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip().strip('"').strip("'")
    model_name = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
    try:
        api_key = str(st.secrets.get("GEMINI_API_KEY", api_key)).strip().strip('"').strip("'")
        model_name = str(st.secrets.get("GEMINI_MODEL", model_name)).strip()
    except st.errors.StreamlitSecretNotFoundError:
        pass
    return api_key, model_name or "gemini-3.8-flash"


def _export_csv(response: dict) -> bytes:
    buffer = io.StringIO(newline="")
    fields = [
        "rank", "promo_title", "promo_type", "target_audience_appeal",
        "total_duration_sec", "hook_timestamp", "hook", "cliffhanger_timestamp",
        "cliffhanger", "why_it_works", "posting_priority_rationale", "segments",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for promo in response["promos"]:
        writer.writerow({
            "rank": promo["rank"],
            "promo_title": promo["promo_title"],
            "promo_type": promo["promo_type"],
            "target_audience_appeal": promo["target_audience_appeal"],
            "total_duration_sec": promo["total_duration_sec"],
            "hook_timestamp": promo["hook"]["timestamp_start"],
            "hook": promo["hook"]["description"],
            "cliffhanger_timestamp": promo["cliffhanger"]["timestamp_end"],
            "cliffhanger": promo["cliffhanger"]["description"],
            "why_it_works": promo["why_it_works"],
            "posting_priority_rationale": promo["posting_priority_rationale"],
            "segments": json.dumps(promo["segments"], ensure_ascii=False),
        })
    return buffer.getvalue().encode("utf-8-sig")


def _render_promos(response: dict, analytics: ScriptAnalytics) -> None:
    badge_names = ["Hero Launch Priority", "Viral Outrage", "Power Reversal"]
    for promo in response["promos"]:
        badge = badge_names[(int(promo["rank"]) - 1) % len(badge_names)]
        title = (
            f"#{promo['rank']}  {promo['promo_title']}  ·  "
            f"⏱️ {float(promo['total_duration_sec']):.1f}s"
        )
        with st.expander(title, expanded=int(promo["rank"]) == 1):
            st.markdown(f"**{badge}** · {promo['promo_type']}")
            st.markdown(f"**Hook (first 3 seconds):** {promo['hook']['description']}")
            st.markdown(f"**Cliffhanger:** {promo['cliffhanger']['description']}")
            st.markdown(f"**Why it works:** {promo['why_it_works']}")
            st.caption(f"Posting priority: {promo['posting_priority_rationale']}")
            st.markdown("**Segment breakdown**")
            for segment in promo["segments"]:
                st.markdown(
                    f"- Segment {segment['order']}: Ep {segment['episode']} "
                    f"[{segment['start_time']} – {segment['end_time']}] "
                    f"({float(segment['duration_sec']):.1f}s) → {segment['role']}"
                )
            st.markdown("**Screenplay excerpt** · red = hook, yellow = body, green = cliffhanger")
            st.markdown(_source_excerpt(promo, analytics), unsafe_allow_html=True)
            st.text_area(
                "Copy promo clip specs",
                value=_clip_specs(promo),
                key=f"clip-spec-{promo['rank']}",
                height=116,
            )


def _render_cast(analytics: ScriptAnalytics) -> None:
    if not analytics.cast:
        st.info("No named speaking characters were detected.")
        return
    frame = pd.DataFrame(analytics.cast)
    max_time = max(float(frame["screentime_sec"].max()), 1.0)
    max_lines = max(int(frame["spoken_lines"].max()), 1)
    frame["first_episode"] = frame["first_appearance"].map(
        lambda item: f"Ep {item['episode']} · {item['timestamp']}" if item else "—"
    )
    frame["signature_line"] = frame["first_appearance"].map(
        lambda item: item["first_line"] if item else ""
    )
    st.dataframe(
        frame[["character", "screentime_sec", "spoken_lines", "first_episode", "signature_line"]],
        width="stretch",
        hide_index=True,
        column_config={
            "character": "Character",
            "screentime_sec": st.column_config.ProgressColumn(
                "Active screen time (sec)",
                min_value=0,
                max_value=max_time,
                format="%.1f",
            ),
            "spoken_lines": st.column_config.ProgressColumn(
                "Spoken lines",
                min_value=0,
                max_value=max_lines,
                format="%d",
            ),
            "first_episode": "First appearance",
            "signature_line": "First dialogue",
        },
    )
    st.caption(f"Longest active character duration: {max_time:.0f} seconds.")


def _render_analytics(analytics: ScriptAnalytics) -> None:
    left, right = st.columns(2)
    with left:
        st.subheader("Emotional profile")
        themes = analytics.theme_frequencies
        if themes:
            frame = pd.DataFrame(
                [{"Theme": theme, "Frequency": count} for theme, count in themes.items()]
            ).sort_values("Frequency", ascending=False)
            st.bar_chart(frame, x="Theme", y="Frequency", color="#a855f7")
        elif analytics.tone_frequencies:
            frame = pd.DataFrame(
                [{"Tone": tone, "Frequency": count} for tone, count in list(analytics.tone_frequencies.items())[:12]]
            )
            st.bar_chart(frame, x="Tone", y="Frequency", color="#a855f7")
        else:
            st.info("No parenthetical tone tags were detected.")
    with right:
        st.subheader("Scene conflict intensity")
        scenes = analytics.scene_conflicts[:15]
        if scenes:
            frame = pd.DataFrame([
                {
                    "Scene": f"Ep {item['episode']} · Sc {item['scene']}",
                    "Intensity": item["conflict_intensity"],
                }
                for item in scenes
            ])
            st.bar_chart(frame, x="Scene", y="Intensity", color="#f97316")
        else:
            st.info("No scene conflict data is available.")
    with st.expander("Most frequent tone tags"):
        if analytics.tone_frequencies:
            st.write(", ".join(
                f"{tone} ({count})" for tone, count in list(analytics.tone_frequencies.items())[:20]
            ))
        else:
            st.write("No tone tags found.")


def main() -> None:
    st.title("🎬 Kuku Microdrama Promo Finder")
    st.caption("Turn screenplay episodes into timestamp-verified vertical-video promo concepts.")

    with st.sidebar:
        st.header("Analyze a screenplay")
        upload = st.file_uploader("Upload episode ZIP", type=["zip"])
        api_key, model_name = _gemini_settings()
        st.caption(
            "Gemini API key is configured."
            if api_key
            else "No Gemini API key configured; offline mode will be used."
        )
        model_name = st.text_input(
            "Gemini model",
            value=model_name,
            help="Model names depend on availability for your Gemini API key.",
        )
        st.caption("Or use the default local screenplay archive when no upload is selected.")
        analyze = st.button("Analyze scripts", type="primary", width="stretch")

    if analyze:
        try:
            with st.spinner("Parsing episodes, computing analytics, and validating promo cuts…"):
                episodes, source_bytes, source_name = _read_input(upload)
                parser = ScreenplayParser()
                show_title = Path(source_name).stem.replace("-script", "").strip()
                analytics = parser.compute_analytics(episodes, show_title)
                engine = PromoEngine(api_key=api_key, model_name=model_name)
                source_hash = source_digest(source_bytes)
                response, mode, warning = engine.generate_promos(analytics, source_hash)
                engine.validate_response(response, analytics)
                OUTPUT_DIR.mkdir(exist_ok=True)
                write_registries(analytics, OUTPUT_DIR)
                (OUTPUT_DIR / "promos_output.json").write_text(
                    json.dumps(response, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                st.session_state["analysis"] = {
                    "analytics": analytics,
                    "response": response,
                    "mode": mode,
                    "warning": warning,
                    "source_name": source_name,
                    "source_hash": source_hash,
                }
        except (OSError, ValueError, zipfile.BadZipFile, PromoValidationError) as exc:
            st.error(f"Analysis failed: {exc}")

    state = st.session_state.get("analysis")
    if state is None:
        st.info("Choose an episode ZIP or analyze the local sample screenplay to begin.")
        return

    analytics: ScriptAnalytics = state["analytics"]
    response: dict = state["response"]
    genre = response.get("genre_analysis", {})
    st.caption(f"Source: {state['source_name']} · Promo generation: {state['mode']}")
    if state["warning"]:
        st.warning(state["warning"])
    st.header(analytics.show_title)
    st.markdown(
        f"`{genre.get('primary_genre', 'Genre pending')}` "
        + " ".join(f"`{item}`" for item in genre.get("sub_genres", []))
    )
    metric_columns = st.columns(4)
    metric_columns[0].metric("Episodes analyzed", len(analytics.episodes))
    metric_columns[1].metric("Characters detected", len(analytics.cast))
    runtime_hours = int(analytics.total_runtime_sec // 3600)
    runtime_minutes = int((analytics.total_runtime_sec % 3600) // 60)
    metric_columns[2].metric("Total runtime", f"{runtime_hours}h {runtime_minutes}m")
    metric_columns[3].metric("Promo cuts", len(response["promos"]))
    if genre.get("marketing_hook_summary"):
        st.write(genre["marketing_hook_summary"])

    with st.sidebar:
        st.divider()
        st.subheader("Export")
        st.download_button(
            "Download promos_output.json",
            data=json.dumps(response, indent=2, ensure_ascii=False).encode("utf-8"),
            file_name="promos_output.json",
            mime="application/json",
            width="stretch",
        )
        st.download_button(
            "Download promos_output.csv",
            data=_export_csv(response),
            file_name="promos_output.csv",
            mime="text/csv",
            width="stretch",
        )

    promos_tab, cast_tab, analytics_tab, limits_tab = st.tabs([
        "🏆 Top 10 Promo Cuts",
        "👥 Cast & Screentime",
        "📊 Emotional & Dramatic Profile",
        "⚠️ Edge Cases & Limitations",
    ])
    with promos_tab:
        _render_promos(response, analytics)
    with cast_tab:
        _render_cast(analytics)
    with analytics_tab:
        _render_analytics(analytics)
    with limits_tab:
        st.subheader("Quality limitations to review")
        st.markdown(
            """
            - Timestamped screenplay text cannot verify actual picture edits, actor delivery, or final audio mix.
            - Rapid cuts, overlapping dialogue, and sound effects may not translate cleanly into a vertical promo.
            - Scene-level conflict scores are keyword heuristics; dramatic context can produce false positives or negatives.
            - Some character relationships require series lore; standalone comprehension and hook/cliffhanger choices need editorial review.
            - Offline ranking is deterministic and screenplay-grounded, not a measured conversion prediction.
            """
        )
        st.caption(
            "Every displayed excerpt is drawn from source events. Promo timestamps are checked against parsed scene bounds "
            "and the 20–60 second duration rule before display or export."
        )


if __name__ == "__main__":
    main()
