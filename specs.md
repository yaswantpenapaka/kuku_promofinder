# Functional & Technical Specifications: Kuku Microdrama Promo Finder

## 1. Executive Summary & Objective
Build a production-grade Streamlit web prototype that ingests a zip file containing 50 microdrama screenplay episodes (US English format, e.g., *"I Married a Broken Soldier. He was a Duke"*), statistically parses the narrative and temporal data, extracts key character dynamics, and uses Gemini LLM (with a robust offline/cached fallback) to generate and rank the **Top 10 High-Converting Promo Cuts (20–60 seconds)** for vertical video platforms (TikTok, Instagram Reels, YouTube Shorts).

---

## 2. Input Specifications

### 2.1 File Ingestion
- **Input Formats:** 
  - Direct file upload via Streamlit (`.zip` containing episode `.txt` files).
  - Default fallback to local workspace folder or zip file (`I Married a Broken Soldier. He was a Duke-script.zip` or existing directory).
- **Episode Format:**
  Standard screenplay plain text files named `episode-XXX-Episode YYYYYYY.txt`.
  Each file contains:
  - Header: `EPISODE NUMBER`, `EPISODE LOGLINE`, `CONTINUITY NOTES`
  - Scene Blocks: `SCENE XX`, `Scene heading`, `Scene start: MM:SS.ss`, `Scene end: MM:SS.ss`
  - Event Blocks: `**CAMERA:**`, `**ACTION:**`, `**SOUND FX:**`, `**SPEAKER (Tone/Emotion):**`
  - Granular timestamps: `Start: (MM:SS.ss) | End: (MM:SS.ss)`
  - Dialogue strings in quotes.

---

## 3. Strict Constraints & Business Rules

1. **Duration Constraint (Non-Negotiable):**
   - Each promo cut must strictly satisfy:
     $$20.0\text{ seconds} \le \text{Total Promo Duration} \le 60.0\text{ seconds}$$
   - For single-scene cuts: $\text{Duration} = \text{End Timestamp} - \text{Start Timestamp}$.
   - For multi-segment cuts: $\text{Total Duration} = \sum_{i=1}^{k} (\text{End}_i - \text{Start}_i)$, where $k \in \{2, 3\}$.
   - Any promo candidate failing this range must be rejected or programmatically clamped by the validation layer.

2. **Promo Composition Rules:**
   - **The Opening Hook (First 3 seconds):** Must start with an immediate disruption (public humiliation, slap, betrothal rejection, arrest, or shocking accusation).
   - **The Body / Character Bridge:** Seamlessly brings in key character dynamics (protagonist under attack, antagonist smugness, or heroic intervention).
   - **The Cliffhanger (Final 3 seconds):** Must end abruptly on an unresolved peak of dramatic tension (unanswered shock proposal, impending violence, secret identity disclosure, cut to black with sound swell).
   - **Standalone Comprehensibility:** A scrolling viewer with zero knowledge of the show must grasp the stakes instantly.

3. **Promo Types Supported:**
   - **Type A (Single-Beat Punch):** A continuous 25–55s uninterrupted scene containing hook, conflict, and cliffhanger.
   - **Type B (Multi-Segment Arc):** 2 stitched segments (e.g., Segment 1: Humiliation / Betrayal in Ep 1 or 2 [10–20s] + Segment 2: Character Intro / Retribution Cliffhanger in Ep 3 [15–30s]).

---

## 4. Analytical & Data Requirements

The parser must compute and extract:
1. **Cast Screentime & Statistics:**
   - Total spoken lines per character across all 50 episodes.
   - Total active timestamp duration on screen (dialogue + explicit action blocks).
   - Character introduction registry: Episode number, exact timestamp, introductory scene description, and first line of dialogue.
   - Cast ordered from highest to lowest screentime.
2. **Emotional Profile & Drama Analytics:**
   - Frequency count of emotional tone tags in parentheses (e.g., `Cold`, `Callous`, `Furious`, `Mocking`, `Smug`, `Desperate`, `Imposing`).
   - Conflict intensity score per scene based on action verbs and aggressive dialogue cues.
3. **Screenplay Intermediate Registries:**
   - `character_roster.json`: Character appearances, intro timestamps, first lines.
   - `beat_registry.json`: Highlight conflict/humiliation scenes with involved characters and timestamps.
   - `episode_endings.json`: Final 15–30 seconds of all 50 episodes with cliffhanger lines and audio swells.

---

## 5. UI & Presentation Specifications (Streamlit)

The user interface must be clean, responsive, and avoid raw JSON outputs.

1. **Header & Overview:**
   - Series Title, Total Episodes Analyzed, Total Run Time.
   - **Genre & Tone Badges:** AI-predicted Genre (e.g., *Aristocratic Romance / Revenge Drama / Forced Marriage*) derived from emotional analytics.
2. **Section 1: Top 10 Promo Cuts (Leaderboard):**
   - Ranks 1 through 10 with impact badges (`Hero Launch Priority`, `Viral Outrage`, `Power Reversal`).
   - Duration badge (e.g., `⏱️ 42.5s`).
   - Segment Breakdown list: `Segment 1: Ep X [02:04.20 - 02:18.00] (13.8s) -> Segment 2: Ep Y [01:09.90 - 01:38.60] (28.7s)`.
   - The Hook Summary (First 3s).
   - The Cliffhanger Summary (Unresolved moment).
   - One-Line Marketing Rationale ("Why it works on TikTok/Reels").
   - **Interactive Screenplay Viewer:** Expandable drawer showing the exact screenplay excerpt with color-coded highlights for Hook (Red), Body (Yellow), and Cliffhanger (Green).
3. **Section 2: Cast Screentime Leaderboard:**
   - Interactive table / progress bars sorting the cast by screentime and dialogue count.
   - Character First Appearance tag and signature opening line.
4. **Section 3: Script Analytics & Emotional Distribution:**
   - Visual bar chart showing top emotional frequencies (Betrayal, Arrogance, Defiance, Rage, Heartbreak).
5. **Section 4: Edge Cases & Quality Limitations:**
   - Clear disclosure of failure modes (e.g., scenes requiring external lore, rapid cuts with audio overlap, false-positive rage in minor scenes).
6. **Data Export:**
   - One-click download buttons for `promos_output.json` and `promos_output.csv` matching the assignment's raw output requirements.

