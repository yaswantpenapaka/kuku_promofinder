# Architecture, Tech Stack & Agent Rules: Kuku Promo Prototype

## 1. System Architecture

```
                    ┌──────────────────────────────────────────────┐
                    │               Streamlit UI                   │
                    │ (app.py: Dashboard, Visual Cards, Exporters) │
                    └──────────────────────┬───────────────────────┘
                                           │
                        ┌──────────────────┴──────────────────┐
                        ▼                                     ▼
          ┌───────────────────────────┐         ┌───────────────────────────┐
          │         parser.py         │         │      promo_engine.py      │
          │ - Zip extraction          │         │ - Prompt payload composer │
          │ - Script regex tokenizer  │         │ - Gemini API caller       │
          │ - Screentime & Emotions   │         │ - Strict JSON validation  │
          │ - Registries generator    │         │ - Offline cache fallback  │
          └─────────────┬─────────────┘         └─────────────┬─────────────┘
                        │                                     │
                        └──────────────────┬──────────────────┘
                                           ▼
          ┌─────────────────────────────────────────────────────────┐
          │                    Generated Artifacts                  │
          │ - character_roster.json                                 │
          │ - beat_registry.json                                    │
          │ - episode_endings.json                                  │
          │ - promos_output.json & .csv                             │
          └─────────────────────────────────────────────────────────┘
```

---

## 2. Technology Stack & Dependencies

- **Language:** Python 3.10+ (tested on Python 3.13)
- **Web Dashboard:** `streamlit` (v1.40+)
- **LLM Integration:** `google-genai` (v1.0+ or `google.generativeai`)
- **Data Manipulation & Visualization:** `pandas`, `altair` / `plotly`
- **Report & Document Generation:** `reportlab` (for PDF generation of the 2-page doc)
- **Standard Library:** `zipfile`, `re`, `json`, `os`, `pathlib`, `typing`

---

## 3. Core Implementation Modules

### Module 1: `parser.py`
- **Class `ScreenplayParser`:**
  - `extract_zip(zip_path, target_dir)`: Unzips archive safely.
  - `parse_episode(file_path) -> EpisodeData`:
    - Regex pattern for Scene Headers: `SCENE (\d+)`, `Scene start: (\d+:\d+\.\d+)`, `Scene end: (\d+:\d+\.\d+)`
    - Regex pattern for Events: `\*\*(CAMERA|ACTION|SOUND FX|.+?)\*\*`
    - Extracts Character name and tone: `\*\*([A-Z\s]+)(?:\s*\((.*?)\))?\:\*\*`
    - Parses timestamp tuples: `Start: \((\d+:\d+\.\d+)\)\s*\|\s*End: \((\d+:\d+\.\d+)\)`
    - Converts `MM:SS.ss` to float seconds (`parse_time(ts) -> float`) and back (`format_time(sec) -> str`).
  - `compute_analytics() -> ScriptAnalytics`:
    - Aggregates cast dialogue duration, line counts, first appearance timestamps.
    - Aggregates emotional tone frequencies.
    - Emits:
      - `character_roster.json`
      - `beat_registry.json` (filtered for high-intensity keywords: slap, marry, plainness, bastard, arrest, duke, kneel, betray)
      - `episode_endings.json` (last 20 seconds of every episode)

### Module 2: `promo_engine.py`
- **Class `PromoEngine`:**
  - Initializes Gemini client using `GEMINI_API_KEY` from the local `.env` file; never renders the key in the dashboard.
  - Method `generate_promos(analytics, character_roster, beat_registry, episode_endings) -> PromoResponse`:
    - Constructs the prompt using the templated instructions in `prompt.md`.
    - Implements strict Pydantic/TypedDict JSON parsing.
    - Enforces duration boundaries: If any promo duration $< 20.0$ or $> 60.0$ seconds, automatically recalculates or clips timestamps to legitimate screenplay scene beats.
    - Uses the Gemini Interactions API with a JSON response schema.
    - **Fallback Offline Cache:** If no API key is supplied or network fails, automatically loads pre-extracted, verified golden promos from `cached_promos.json` to ensure the prototype never breaks during review or live demo.

### Module 3: `app.py`
- **Streamlit Application Layout:**
  - **Sidebar:** File uploader (`.zip`), API Key input, Analysis Trigger button, Raw Export buttons.
  - **Main Screen:**
    - Show Title, Metrics Row (Total Episodes, Total Characters, Estimated Runtime, Primary Genre).
    - Tabs:
      1. `🏆 Top 10 Promo Cuts`: Expandable cards with rank, type, duration badge, hook, cliffhanger, rationale, and color-coded screenplay text.
      2. `👥 Cast & Screentime`: Screentime leaderboards, dialogue counters, first appearance cues.
      3. `📊 Emotional & Dramatic Profile`: Visual bar chart of emotion tags, conflict distribution.
      4. `⚠️ Edge Cases & Failure Modes`: Professional critique on screenplay-only promo extraction challenges.

---

## 4. Coding & Agent Rules

1. **Deterministic Time Math:** Never let an LLM invent or guess floating timestamps without validating them against the parsed script timestamps.
2. **Resilience & Fallback:** The prototype must run even with 0 API credits by maintaining a fallback cache of pre-analyzed outputs.
3. **Clean Code Structure:** Keep functions modular, type-annotated, and well-documented. Do not write one single giant unmaintainable file.
4. **No Hallucinated Script Text:** Dialogue presented in promo cards must be exact verbatim excerpts from the parsed screenplay text.
5. **Human in the Loop:** Include an "Edit Cutpoints" or "Copy Promo Clip Specs" feature on each promo card so an editor can easily tweak in/out points.
