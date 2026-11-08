# Kuku Microdrama Promo Finder

A Streamlit prototype that parses timestamped screenplay episodes, computes cast
and conflict analytics, and ranks ten 20–60 second vertical-video promo cuts.
It can use Gemini when configured, or run with the validated local heuristics
and a screenplay-specific cache.

## Run locally

1. Use Python 3.10 or later.
2. From this folder, install dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

3. Add a Gemini key to `.env` if desired. Set `GEMINI_MODEL` to the model name
   available to your API key (default: `gemini-3.8-flash`). The key is read from
   `.env` and is never shown in the dashboard. Leave it blank for offline mode.
4. Start the dashboard:

   ```powershell
   streamlit run app.py
   ```

The app uses the sibling `I Married a Broken Soldier. He was a Duke-script.zip`
by default, or accepts an uploaded `.zip` of episode `.txt` files. Analysis
artifacts are written to `outputs/`, which is ignored by Git.

## Deploy on Streamlit Community Cloud

Live app: https://kukupromofinder-8ay8irvrkpuqshzaqfactt.streamlit.app/

1. Push this repository to GitHub without adding `.env` or screenplay files.
2. In Streamlit Community Cloud, create an app from this repository and select
   `app.py` as the entry point.
3. Add `GEMINI_API_KEY` and optionally `GEMINI_MODEL` in the app's Secrets
   settings. Never commit API keys to GitHub.
4. Open the deployed app and upload a screenplay ZIP to analyze it.

## Validation and exports

Every candidate's timestamps must fit within a parsed scene, and segment
durations are recomputed from those timestamps. Promos outside 20–60 seconds
are rejected. Screenplay excerpts are populated from source events rather than
LLM-generated text. The dashboard provides `promos_output.json` and
`promos_output.csv` downloads, plus the cast, beat, and episode-ending
registries in `outputs/`.
