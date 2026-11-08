# Prompt Design & Concrete LLM Instructions

This document specifies the exact prompt architecture and system instructions passed to the Gemini LLM by `promo_engine.py`, leveraging the parsed screenplay registries and following all specifications in `specs.md` and rules in `agents.md`.

---

## 1. System Prompt

```text
You are an expert Performance Marketing Creative Director & Video Editor specializing in viral microdrama mobile video ads (TikTok, Instagram Reels, YouTube Shorts for platforms like ReelShort, DramaBox, and Kuku).

Your expertise is identifying psychological hooks, dramatic power reversals, and cliffhangers in serialized screenplays to craft high-converting 20 to 60-second video promos that maximize Click-Through Rate (CTR) and app installs.
```

---

## 2. Dynamic User Prompt Template

```text
You are provided with structured screenplay analytics and parsed scene registries from a 50-episode microdrama series.

### SHOW OVERVIEW & ANALYTICS:
- Show Title / Base Folder: {show_title}
- Total Episodes: {total_episodes}
- Top Emotional Frequencies Detected: {top_emotions}
- Cast Screentime Leaderboard: {top_cast_summary}

### AVAILABLE SCENE REGISTRIES:
1. CHARACTER ROSTER & INTRODUCTIONS:
{character_roster_json}

2. HIGH-STAKES CONFLICT & HUMILIATION BEATS:
{beat_registry_json}

3. EPISODE ENDINGS & CLIFFHANGERS:
{episode_endings_json}

---

### YOUR TASKS:

#### Task 1: Show Genre & Dramatic Identity
Based on the emotional tone distribution and character conflicts, define the Primary Genre, Secondary Genres (2-3), and a 2-sentence marketing positioning statement for this series.

#### Task 2: Synthesize & Rank the Top 10 High-Converting Promo Cuts
Craft exactly 10 distinct, compelling promo cuts following these mandatory rules:
1. STRICT DURATION CONSTRAINT:
   The total duration of each promo MUST be between 20.0 seconds and 60.0 seconds.
   Sum of all segment durations = Total Duration. (End_time - Start_time).
2. HOOK RULE (First 3 seconds):
   Must start on an immediate emotional disruption or high-stakes line (public humiliation, slap, altar betrayal, arrest, or severe threat) that forces a scrolling viewer to stop.
3. CLIFFHANGER RULE (Final 3 seconds):
   Must cut at an unresolved climax or shock reveal (unexpected intervention, shock proposal, sudden realization, or dramatic threat).
4. STANDALONE COHERENCE:
   A first-time viewer on TikTok who knows nothing about the characters must immediately understand who is the victim, who is the villain, and why the stakes matter.
5. PROMO DIVERSITY:
   Include a mix of:
   - Type A: Single-Scene continuous punches (uninterrupted dramatic tension).
   - Type B: Multi-Segment Arcs (e.g., Opening Humiliation -> Character Intro/Escalation -> Climactic Cliffhanger).
6. RANKING LOGIC (1 to 10):
   Rank the promos by estimated conversion power. Explain why Rank #1 is posted first (e.g., universal injustice generates immediate emotional resonance).

---

### OUTPUT FORMAT:
You MUST respond ONLY with a valid, parseable JSON object matching this exact schema:

{
  "genre_analysis": {
    "primary_genre": "string",
    "sub_genres": ["string", "string"],
    "marketing_hook_summary": "string"
  },
  "promos": [
    {
      "rank": 1,
      "promo_title": "string",
      "promo_type": "Single-Scene Punch" | "Multi-Segment Arc",
      "target_audience_appeal": "string",
      "total_duration_sec": 42.5,
      "segments": [
        {
          "order": 1,
          "episode": 1,
          "start_time": "02:04.20",
          "end_time": "02:18.00",
          "duration_sec": 13.8,
          "role": "Hook: Public altar betrayal"
        },
        {
          "order": 2,
          "episode": 3,
          "start_time": "01:09.90",
          "end_time": "01:38.60",
          "duration_sec": 28.7,
          "role": "Cliffhanger: The Duke's shock intervention and proposal"
        }
      ],
      "hook": {
        "timestamp_start": "02:04.20",
        "description": "string (what makes the viewer stop in 3 seconds)"
      },
      "cliffhanger": {
        "timestamp_end": "01:38.60",
        "description": "string (unresolved moment forcing app download)"
      },
      "why_it_works": "string (1-2 sentences on psychological UA conversion)",
      "posting_priority_rationale": "string (why this rank gets tested first)"
    }
  ]
}
```

---

## 3. Post-Processing & Validation Rules in Code

When the LLM response is received:
1. `validate_timestamps()`: Ensure all `start_time` and `end_time` are valid string timestamps and accurately map to scene intervals in the screenplay.
2. `validate_duration()`: Compute:
   $$\text{Actual Duration} = \sum (\text{parse\_time}(\text{end}) - \text{parse\_time}(\text{start}))$$
   If $\text{Actual Duration} < 20.0\text{s}$ or $> 60.0\text{s}$, adjust the segment boundaries using adjacent script dialogue beats.
3. `populate_script_text()`: Fetch the verbatim dialogue, camera, and action lines from the parsed screenplay so the Streamlit UI can render the exact script preview with colored highlights.

