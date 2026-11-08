import unittest

from parser import Episode, Scene, ScreenplayParser, format_time, parse_time
from promo_engine import PromoEngine, PromoValidationError


class ParserTests(unittest.TestCase):
    def test_time_round_trip(self):
        self.assertEqual(parse_time("02:04.20"), 124.2)
        self.assertEqual(format_time(124.2), "02:04.20")
        with self.assertRaises(ValueError):
            parse_time("2:64.20")

    def test_uppercase_headers_and_per_episode_screentime(self):
        text = """EPISODE NUMBER: Episode 1
EPISODE LOGLINE: A test.
SCENE 01
SCENE HEADING: INT. HALL - DAY
SCENE START: 00:00.00
SCENE END: 00:30.00
**CAMERA:**
Start: (00:00.00) | End: (00:05.00)
Juliana enters.
**LADY JULIANA (Furious, Defiant):**
Start: (00:02.00) | End: (00:07.00)
\"I will not kneel.\"
**ACTION:**
Start: (00:06.00) | End: (00:10.00)
Juliana turns toward the door.
"""
        parser = ScreenplayParser()
        episode_one = parser.parse_text(text, "episode-001.txt")
        episode_two = parser.parse_text(text.replace("Episode 1", "Episode 2"), "episode-002.txt")
        self.assertEqual(episode_one.scenes[0].heading, "INT. HALL - DAY")
        self.assertEqual(len(episode_one.scenes[0].events), 3)

        analytics = parser.compute_analytics([episode_one, episode_two], "Test")
        juliana = next(item for item in analytics.cast if item["character"] == "LADY JULIANA")
        self.assertEqual(juliana["spoken_lines"], 2)
        self.assertEqual(juliana["screentime_sec"], 16.0)
        self.assertEqual(juliana["appearances"], [1, 2])
        self.assertEqual(juliana["first_appearance"]["first_line"], "I will not kneel.")
        self.assertEqual(analytics.tone_frequencies["Furious"], 2)


class PromoValidationTests(unittest.TestCase):
    def setUp(self):
        scene = Scene(number=1, heading="INT. HALL", start=0.0, end=60.0)
        self.analytics = ScreenplayParser().compute_analytics(
            [Episode(number=1, filename="episode-001.txt", title="", logline="", continuity_notes="", scenes=[scene])],
            "Test",
        )

    def _response(self, duration=25.0):
        return {
            "genre_analysis": {
                "primary_genre": "Drama",
                "sub_genres": ["Romance", "Revenge"],
                "marketing_hook_summary": "A test summary.",
            },
            "promos": [
                {
                    "rank": rank,
                    "promo_title": f"Promo {rank}",
                    "promo_type": "Single-Scene Punch",
                    "target_audience_appeal": "Drama viewers.",
                    "total_duration_sec": duration,
                    "segments": [{
                        "order": 1,
                        "episode": 1,
                        "start_time": "00:00.00",
                        "end_time": format_time(duration),
                        "duration_sec": duration,
                        "role": "Hook and cliffhanger.",
                    }],
                    "hook": {"timestamp_start": "00:00.00", "description": "A disruption."},
                    "cliffhanger": {"timestamp_end": format_time(duration), "description": "A reveal."},
                    "why_it_works": "It opens strongly.",
                    "posting_priority_rationale": "Ranked for a demo.",
                }
                for rank in range(1, 11)
            ],
        }

    def test_accepts_valid_duration_and_rejects_out_of_range(self):
        PromoEngine.validate_response(self._response(), self.analytics)
        with self.assertRaises(PromoValidationError):
            PromoEngine.validate_response(self._response(19.9), self.analytics)


if __name__ == "__main__":
    unittest.main()
