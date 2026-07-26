#!/usr/bin/env python3
"""
TikTok Viral Video Analyzer
AI-powered competitor analysis system

Author: HiroshigeG
Date: 29 Jan 2026
"""

import os
import json
import base64
import argparse
import time
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

APIFY_API_KEY = os.getenv('APIFY_API_KEY')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# Visual separators for better UX
SEPARATOR_HEAVY = "=" * 60
SEPARATOR_LIGHT = "-" * 60

class TikTokAnalyzer:
    """Main analyzer class"""

    def __init__(self):
        self.apify_key = APIFY_API_KEY
        self.gemini_key = GEMINI_API_KEY
        self.videos_folder = Path("./downloaded_videos")
        self.videos_folder.mkdir(exist_ok=True)

    def scrape_videos(self, hashtag: Optional[str] = None,
                     profile: Optional[str] = None,
                     count: int = 5,
                     min_views: int = 0) -> List[Dict]:
        """
        Scrape TikTok videos using Apify

        Args:
            hashtag: TikTok hashtag to scrape (without #)
            profile: TikTok profile username (with @)
            count: Number of videos to scrape
            min_views: Minimum views filter

        Returns:
            List of video metadata dicts
        """
        print(f"🔍 Scraping {count} videos...")

        # Apify TikTok Scraper actor ID (same as JS version)
        actor_id = "clockworks~tiktok-scraper"

        # Build input payload
        run_input = {
            "resultsPerPage": count,
            "shouldDownloadVideos": True,
            "shouldDownloadCovers": False,
            "shouldDownloadSubtitles": False,
        }

        if hashtag:
            run_input["hashtags"] = [hashtag]
        elif profile:
            run_input["profiles"] = [profile]
        else:
            raise ValueError("Must provide either hashtag or profile")

        # Call Apify API
        url = f"https://api.apify.com/v2/acts/{actor_id}/runs?token={self.apify_key}"

        response = requests.post(url, json=run_input)
        response.raise_for_status()

        run_data = response.json()
        run_id = run_data['data']['id']

        # Wait for run to complete
        print("⏳ Waiting for scraping to complete...")
        while True:
            status_url = f"https://api.apify.com/v2/acts/{actor_id}/runs/{run_id}?token={self.apify_key}"
            status_response = requests.get(status_url)
            status_data = status_response.json()

            status = status_data['data']['status']

            if status == 'SUCCEEDED':
                print("✅ Scraping completed!")
                break
            elif status in ['FAILED', 'ABORTED', 'TIMED-OUT']:
                raise Exception(f"Scraping failed with status: {status}")

            time.sleep(5)

        # Get results from dataset (metadata)
        dataset_id = status_data['data']['defaultDatasetId']
        key_value_store_id = status_data['data']['defaultKeyValueStoreId']

        results_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={self.apify_key}"
        results = requests.get(results_url).json()

        # Build metadata map
        video_metadata = {}
        for item in results:
            video_id = item.get('id') or item.get('awemeId')
            if video_id:
                video_metadata[video_id] = {
                    'id': video_id,
                    'webVideoUrl': item.get('webVideoUrl') or item.get('shareUrl'),
                    'videoMeta': {
                        'playCount': item.get('playCount', 0),
                        'diggCount': item.get('diggCount', 0),
                        'commentCount': item.get('commentCount', 0),
                        'shareCount': item.get('shareCount', 0),
                    },
                    'text': item.get('text') or item.get('desc', ''),
                    'authorMeta': item.get('authorMeta', {})
                }

        # Get video files from Key-Value Store
        store_url = f"https://api.apify.com/v2/key-value-stores/{key_value_store_id}/keys?token={self.apify_key}"
        store_keys = requests.get(store_url).json()

        # Filter for .mp4 files
        video_keys = [k for k in store_keys.get('data', {}).get('items', []) if k['key'].endswith('.mp4')]

        print(f"✅ Found {len(video_keys)} videos in Key-Value Store")

        # Download videos from store and attach metadata
        final_results = []
        for key_info in video_keys[:count]:
            try:
                # Extract video ID from key name (format: video-ID.mp4 or similar)
                key_name = key_info['key']
                key_parts = key_name.replace('.mp4', '').split('-')
                video_id = key_parts[-1] if key_parts else 'unknown'

                print(f"📥 Downloading {key_name} from Key-Value Store...")

                # Download video from Key-Value Store
                video_url = f"https://api.apify.com/v2/key-value-stores/{key_value_store_id}/records/{key_name}?token={self.apify_key}"
                video_response = requests.get(video_url)
                video_response.raise_for_status()

                # Save video file
                video_path = self.videos_folder / key_name
                with open(video_path, 'wb') as f:
                    f.write(video_response.content)

                file_size_mb = video_path.stat().st_size / (1024 * 1024)
                print(f"✅ Downloaded {key_name} ({file_size_mb:.2f} MB)")

                # Get metadata for this video
                metadata = video_metadata.get(video_id, {
                    'id': video_id,
                    'webVideoUrl': '',
                    'videoMeta': {'playCount': 0, 'diggCount': 0, 'commentCount': 0, 'shareCount': 0}
                })

                # Add video path to metadata
                metadata['videoUrl'] = str(video_path)
                metadata['local_filename'] = key_name

                # Filter by min views
                if min_views > 0 and metadata.get('videoMeta', {}).get('playCount', 0) < min_views:
                    print(f"⏭️  Skipping {key_name} - below minimum views threshold")
                    continue

                final_results.append(metadata)

            except Exception as e:
                print(f"⚠️  Error downloading {key_name}: {e}")
                continue

        print(f"✅ Successfully downloaded {len(final_results)} videos")

        return final_results

    def download_video(self, video_url: str, video_id: str) -> Path:
        """Download video file"""
        video_path = self.videos_folder / f"{video_id}.mp4"

        if video_path.exists():
            print(f"⏭️  Video {video_id} already downloaded, skipping...")
            return video_path

        print(f"⬇️  Downloading video {video_id}...")
        response = requests.get(video_url)
        response.raise_for_status()

        with open(video_path, 'wb') as f:
            f.write(response.content)

        # Get file size
        file_size_mb = video_path.stat().st_size / (1024 * 1024)
        print(f"✅ Downloaded to {video_path} ({file_size_mb:.2f} MB)")
        return video_path

    def video_to_base64(self, video_path: Path) -> str:
        """Convert video to base64 for Gemini API"""
        with open(video_path, 'rb') as f:
            return base64.b64encode(f.read()).decode('utf-8')

    def analyze_video_with_gemini(self, video_path: Path, video_metadata: Dict) -> Dict:
        """
        Analyze video using Gemini 2.5 Pro Vision API

        Args:
            video_path: Path to downloaded video
            video_metadata: Metadata from Apify scraper

        Returns:
            Structured analysis dict
        """
        print(f"🧠 Analyzing video with Gemini AI...")

        # Convert video to base64
        video_base64 = self.video_to_base64(video_path)

        # Prepare Gemini API request
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent?key={self.gemini_key}"

        # Detailed analysis prompt - SIMPLIFIED to avoid JSON errors
        prompt = """
        Analyze this viral TikTok/Instagram video in extreme detail.

        CRITICAL FORMATTING:
        - Return ONLY valid JSON (no markdown, no code blocks)
        - Keep all text on single lines (no line breaks in strings)
        - Use simple descriptions (avoid complex nested quotes)

        Provide this EXACT structure:

        {
          "VISUAL_HOOK": {
            "description": "exact description of first 3 seconds",
            "hook_type": "Curiosity|Emotional|Shock|Transformation",
            "effectiveness": "why it's effective"
          },
          "NARRATIVE_STRUCTURE": {
            "format": "problem-solution|transformation|tutorial|comedy|etc",
            "pacing": "description of cut frequency",
            "emotional_arc": "emotion journey from start to end"
          },
          "AUDIO_STRATEGY": {
            "music_sound": "music/sound description or 'None'",
            "is_trending_audio": true|false,
            "voiceover": {
              "present": true|false,
              "tone": "description of tone"
            },
            "sound_effects_present": true|false
          },
          "TEXT_OVERLAY": {
            "style": "font, color, placement description",
            "frequency": "how often text appears",
            "purpose": "purpose of the text"
          },
          "WHY_IT_WORKS": {
            "reasons_for_virality": [
              "reason 1",
              "reason 2",
              "reason 3"
            ],
            "psychological_triggers": [
              "trigger 1",
              "trigger 2"
            ],
            "shareability": "what makes it shareable"
          },
          "REPLICATION_BLUEPRINT": {
            "opening_shot_recommendation": "specific recommendation",
            "music_selection_strategy": "strategy description",
            "text_overlay_strategy": "strategy description",
            "pacing_guidelines": "guidelines description",
            "hook_formula": "formula description"
          }
        }

        IMPORTANT:
        - Keep all descriptions concise and on single lines
        - Do NOT add line breaks within string values
        - Use EXACTLY these field names (no variations)
        - Return ONLY the JSON object, no other text

        NOTE: HOW_TO_REPLICATE will be auto-generated after analysis based on your insights.
        """

        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": "video/mp4",
                            "data": video_base64
                        }
                    }
                ]
            }],
            "generationConfig": {
                "temperature": 0.4,
                "topK": 32,
                "topP": 1,
                "maxOutputTokens": 4096,
            }
        }

        response = requests.post(url, json=payload)
        response.raise_for_status()

        gemini_response = response.json()

        # Extract text from response
        analysis_text = gemini_response['candidates'][0]['content']['parts'][0]['text']

        # Parse JSON from response
        try:
            # Clean markdown if present
            if '```json' in analysis_text:
                analysis_text = analysis_text.split('```json')[1].split('```')[0].strip()
            elif '```' in analysis_text:
                analysis_text = analysis_text.split('```')[1].split('```')[0].strip()

            # Try to parse JSON
            analysis_json = json.loads(analysis_text)

        except json.JSONDecodeError as e:
            # If JSON parsing fails, try to fix common issues
            print(f"⚠️  JSON parsing error: {e}")
            print("🔧 Attempting to fix JSON...")

            try:
                # Remove any text before first { and after last }
                start = analysis_text.find('{')
                end = analysis_text.rfind('}') + 1
                if start != -1 and end > start:
                    analysis_text = analysis_text[start:end]
                    analysis_json = json.loads(analysis_text)
                    print("✅ JSON fixed successfully!")
                else:
                    raise ValueError("Could not find valid JSON structure")

            except (json.JSONDecodeError, ValueError):
                # Last resort: return raw text
                print("❌ Could not parse JSON, saving raw analysis")
                analysis_json = {"raw_analysis": analysis_text}

        # Add metadata
        analysis_json['video_id'] = video_metadata.get('id')
        analysis_json['url'] = video_metadata.get('webVideoUrl')
        analysis_json['metrics'] = {
            'views': video_metadata.get('videoMeta', {}).get('playCount', 0),
            'likes': video_metadata.get('videoMeta', {}).get('diggCount', 0),
            'comments': video_metadata.get('videoMeta', {}).get('commentCount', 0),
            'shares': video_metadata.get('videoMeta', {}).get('shareCount', 0),
        }

        # Calculate engagement rate
        total_engagement = (
            analysis_json['metrics']['likes'] +
            analysis_json['metrics']['comments'] +
            analysis_json['metrics']['shares']
        )
        views = analysis_json['metrics']['views']
        if views > 0:
            analysis_json['metrics']['engagement_rate'] = round((total_engagement / views) * 100, 2)

        print("✅ Analysis complete!")

        return analysis_json

    def generate_viral_patterns_report(self, analyses: List[Dict]) -> Dict:
        """
        Meta-analysis: Find common patterns across all analyzed videos

        Args:
            analyses: List of video analysis dicts

        Returns:
            Dict with viral patterns and recommendations
        """
        if len(analyses) < 3:
            return {
                "note": "Need at least 3 videos for pattern analysis",
                "analyzed_count": len(analyses)
            }

        print(f"\n🔍 Analyzing patterns across {len(analyses)} videos...")

        # Extract patterns
        hook_types = []
        audio_strategies = []
        trending_audio_count = 0
        voiceover_count = 0
        psychological_triggers = []
        pacing_patterns = []
        text_frequencies = []
        avg_engagement = 0
        high_performers = []  # Videos with >5% engagement

        for video in analyses:
            # Engagement rate
            eng_rate = video.get('metrics', {}).get('engagement_rate', 0)
            avg_engagement += eng_rate
            if eng_rate > 5:
                high_performers.append(video)

            # Hook types
            hook_type = video.get('VISUAL_HOOK', {}).get('hook_type', '')
            if hook_type:
                hook_types.append(hook_type)

            # Audio strategy
            audio = video.get('AUDIO_STRATEGY', {})
            is_trending = audio.get('is_trending_audio') or audio.get('trending_audio', False)
            if is_trending:
                trending_audio_count += 1

            voiceover = audio.get('voiceover')
            if voiceover:
                if isinstance(voiceover, dict):
                    if voiceover.get('present'):
                        voiceover_count += 1
                elif isinstance(voiceover, str):
                    voiceover_count += 1

            # Psychological triggers
            triggers = video.get('WHY_IT_WORKS', {}).get('psychological_triggers', [])
            if not triggers:
                triggers = video.get('WHY_IT_WORKS', {}).get('triggers', [])
            psychological_triggers.extend(triggers)

            # Pacing
            pacing = video.get('NARRATIVE_STRUCTURE', {}).get('pacing', '')
            if pacing:
                pacing_patterns.append(pacing)

            # Text frequency
            text_freq = video.get('TEXT_OVERLAY', {}).get('frequency', '')
            if text_freq:
                text_frequencies.append(text_freq)

        # Calculate statistics
        avg_engagement = round(avg_engagement / len(analyses), 2) if analyses else 0

        # Find most common hook type
        from collections import Counter
        hook_counter = Counter(hook_types)
        most_common_hook = hook_counter.most_common(1)[0] if hook_counter else ("Unknown", 0)

        # Analyze psychological triggers (find common keywords)
        trigger_keywords = []
        for trigger in psychological_triggers:
            if isinstance(trigger, str):
                # Extract key concepts
                if 'FOMO' in trigger or 'fear of missing out' in trigger.lower():
                    trigger_keywords.append('FOMO')
                if 'curiosity' in trigger.lower():
                    trigger_keywords.append('Curiosity Gap')
                if 'social proof' in trigger.lower():
                    trigger_keywords.append('Social Proof')
                if 'authority' in trigger.lower():
                    trigger_keywords.append('Authority')
                if 'scarcity' in trigger.lower():
                    trigger_keywords.append('Scarcity')
                if 'belonging' in trigger.lower() or 'community' in trigger.lower():
                    trigger_keywords.append('Community/Belonging')
                if 'novelty' in trigger.lower():
                    trigger_keywords.append('Novelty')

        trigger_counter = Counter(trigger_keywords)
        top_triggers = trigger_counter.most_common(5)

        # Build report
        report = {
            "meta_analysis": {
                "total_videos_analyzed": len(analyses),
                "average_engagement_rate": avg_engagement,
                "high_performers_count": len(high_performers),
                "high_performers_threshold": "5% engagement rate"
            },
            "common_patterns": {
                "hook_type": {
                    "most_common": most_common_hook[0],
                    "frequency": f"{most_common_hook[1]}/{len(analyses)} videos",
                    "percentage": round((most_common_hook[1] / len(analyses)) * 100, 1) if analyses else 0,
                    "distribution": dict(hook_counter)
                },
                "audio_strategy": {
                    "trending_audio_usage": f"{trending_audio_count}/{len(analyses)} videos",
                    "trending_percentage": round((trending_audio_count / len(analyses)) * 100, 1) if analyses else 0,
                    "voiceover_usage": f"{voiceover_count}/{len(analyses)} videos",
                    "voiceover_percentage": round((voiceover_count / len(analyses)) * 100, 1) if analyses else 0,
                    "recommendation": "Use trending audio" if trending_audio_count > len(analyses)/2 else "Use original voice/audio for authority"
                },
                "psychological_triggers": {
                    "most_used": [{"trigger": name, "count": count, "percentage": round((count/len(trigger_keywords))*100, 1) if trigger_keywords else 0} for name, count in top_triggers],
                    "key_insight": top_triggers[0][0] if top_triggers else "N/A"
                }
            },
            "actionable_recommendations": self._generate_recommendations(
                most_common_hook[0],
                trending_audio_count > len(analyses)/2,
                top_triggers,
                avg_engagement,
                high_performers
            )
        }

        print(f"✅ Pattern analysis complete!")
        print(f"   Most common hook: {most_common_hook[0]} ({most_common_hook[1]} videos)")
        print(f"   Trending audio: {trending_audio_count}/{len(analyses)} videos")
        print(f"   Top trigger: {top_triggers[0][0] if top_triggers else 'N/A'}")

        return report

    def _add_replication_guide(self, analysis: Dict) -> Dict:
        """
        Automatically generate HOW_TO_REPLICATE guide from analysis data

        Args:
            analysis: Video analysis dict

        Returns:
            Analysis with HOW_TO_REPLICATE section added
        """
        hook = analysis.get('VISUAL_HOOK', {})
        audio = analysis.get('AUDIO_STRATEGY', {})
        narrative = analysis.get('NARRATIVE_STRUCTURE', {})
        text_overlay = analysis.get('TEXT_OVERLAY', {})
        blueprint = analysis.get('REPLICATION_BLUEPRINT', {})

        # Determine content type
        hook_type = hook.get('hook_type', 'Curiosity')
        is_trending_audio = audio.get('is_trending_audio', False)
        has_voiceover = audio.get('voiceover', {}).get('present', False) if isinstance(audio.get('voiceover'), dict) else bool(audio.get('voiceover'))
        pacing = narrative.get('pacing', '')

        # Generate step-by-step guide
        steps = []

        # PRE-PRODUCTION
        steps.append({
            "step": 1,
            "phase": "Pre-Production",
            "action": "Research trending content in your niche",
            "details": f"Browse TikTok/IG Reels for similar content. Note what's working. This video uses '{hook_type}' hooks - study similar examples.",
            "timing": "10-15 minutes",
            "pro_tip": "Save 5-10 reference videos to your phone for quick comparison"
        })

        steps.append({
            "step": 2,
            "phase": "Pre-Production",
            "action": "Select your trending audio" if is_trending_audio else "Plan your voiceover script",
            "details": "Go to TikTok Creative Center or browse trending sounds in your niche. Pick audio that fits the emotion you want to convey." if is_trending_audio else "Write a clear, concise script. Practice it 3-5 times before filming. Confident delivery builds authority.",
            "timing": "5-10 minutes",
            "pro_tip": "Test the audio in your editing app first to check timing" if is_trending_audio else "Record a voice memo first to check pacing and energy"
        })

        steps.append({
            "step": 3,
            "phase": "Pre-Production",
            "action": "Write your hook text overlay",
            "details": f"Create a {hook_type.lower()} hook. Keep it under 8 words. Test readability on mobile screen. Make it stop the scroll.",
            "timing": "5 minutes",
            "pro_tip": "Test with 3 variations, pick the one that creates the biggest curiosity gap"
        })

        # FILMING
        steps.append({
            "step": 4,
            "phase": "Filming",
            "action": "Set up your filming space",
            "details": "Position camera at eye level or slightly above. Use natural light from a window (morning best) or a ring light. Clear background or relevant context (bookshelf for BookTok).",
            "timing": "5-10 minutes",
            "pro_tip": "Film in vertical 9:16 format. Most phones default to this in TikTok/IG app"
        })

        steps.append({
            "step": 5,
            "phase": "Filming",
            "action": "Record your hook (first 3 seconds)",
            "details": f"This is CRITICAL. Your hook should be {hook_type.lower()}-based. " + blueprint.get('opening_shot_recommendation', 'Show your main subject immediately.'),
            "timing": "0-3 seconds of final video",
            "pro_tip": "Record 5+ takes. The hook is 80% of your video's success"
        })

        steps.append({
            "step": 6,
            "phase": "Filming",
            "action": "Film main content in segments",
            "details": "Based on this video's pacing, plan for cuts every 3-5 seconds. Film each segment separately for easier editing. Use physical props or actions to create visual dynamism.",
            "timing": "10-20 minutes filming time",
            "pro_tip": "Over-film! Shoot 2-3x more footage than you need, pick the best parts in editing"
        })

        # EDITING
        steps.append({
            "step": 7,
            "phase": "Editing",
            "action": "Import and arrange clips",
            "details": "Use CapCut (free, easy) or your preferred editor. Arrange clips chronologically. Cut out any pauses or 'umm' sounds. Keep only the energy.",
            "timing": "10-15 minutes",
            "pro_tip": "Use CapCut's auto-captions feature to save time on text overlays"
        })

        steps.append({
            "step": 8,
            "phase": "Editing",
            "action": "Add text overlays",
            "details": f"{text_overlay.get('style', 'Use bold, white text with black outline')}. {blueprint.get('text_overlay_strategy', 'Add text every 2-3 seconds to reinforce message.')}",
            "timing": "5-10 minutes",
            "pro_tip": "Keep text on screen for minimum 1 second for readability"
        })

        steps.append({
            "step": 9,
            "phase": "Editing",
            "action": "Sync audio and finalize pacing",
            "details": "Add your selected music/audio. Sync cuts to beat drops if using music. Ensure pacing matches reference (cuts every 3-5 sec). Watch 3 times before exporting.",
            "timing": "10-15 minutes",
            "pro_tip": "Export at 1080x1920 resolution, 30fps minimum for quality"
        })

        # PUBLISHING
        steps.append({
            "step": 10,
            "phase": "Publishing",
            "action": "Write caption with strategic hashtags",
            "details": "Use 3-5 relevant hashtags. Include 1 mega hashtag (1M+ posts), 2 medium (100K-500K), 1 niche (10K-50K). Add a question or CTA at the end.",
            "timing": "3-5 minutes",
            "pro_tip": "Don't use #fyp or #foryou - they don't help. Use niche-specific tags"
        })

        steps.append({
            "step": 11,
            "phase": "Publishing",
            "action": "Post at optimal time",
            "details": "For BookTok: evenings (7-10pm) work best when people are reading/browsing. Avoid Monday mornings. Test different times and track what works for YOUR audience.",
            "timing": "1 minute",
            "pro_tip": "Respond to first 5 comments within 10 minutes to boost early engagement"
        })

        steps.append({
            "step": 12,
            "phase": "Publishing",
            "action": "Monitor and engage",
            "details": "Check analytics after 1 hour, 6 hours, 24 hours. Respond to comments. If it's underperforming at 1 hour, boost with Stories/Reels cross-post.",
            "timing": "5-10 minutes per check",
            "pro_tip": "Save to a private playlist to easily find and analyze your own patterns later"
        })

        # Equipment recommendations
        equipment = {
            "camera": "iPhone 11+ or equivalent Android (built-in camera is fine)",
            "lighting": "Natural window light (soft, diffused) or $30 ring light from Amazon",
            "audio": "Built-in phone mic works if you're 2-3 feet from camera. For better quality: $20 lavalier mic",
            "editing_software": "CapCut (free, mobile/desktop) or Adobe Premiere Rush (free tier available)"
        }

        # Content checklist
        checklist = [
            "✓ Hook creates curiosity/emotion in first 3 seconds",
            "✓ Cuts happen every 3-5 seconds (no static shots)",
            "✓ Text overlays are readable on mobile (large, bold font)",
            "✓ Audio is clear and synced with visuals",
            "✓ Video is 15-60 seconds (sweet spot: 20-35 seconds)",
            "✓ Strong CTA at the end (question, follow, comment)",
            "✓ Captions/subtitles enabled for accessibility",
            "✓ Vertical format 9:16 (1080x1920)",
        ]

        # Common mistakes
        mistakes = [
            "Don't make static shots longer than 5 seconds - viewers will scroll away",
            "Don't use copyrighted music - use TikTok's library or royalty-free",
            "Don't over-edit with effects - keep it clean and authentic",
            "Don't forget captions - 80% of people watch on mute",
            "Don't use low lighting - poor quality = instant scroll",
            "Don't post without a caption/hashtags - algorithm needs context",
            "Don't use too many hashtags (10+) - looks spammy, use 3-5 max"
        ]

        # Time estimates
        time_estimate = {
            "filming": "15-30 minutes (including setup and multiple takes)",
            "editing": "20-40 minutes (faster with practice and templates)",
            "total": "45-75 minutes (first few videos) → 20-30 minutes (once you have workflow)"
        }

        # Add HOW_TO_REPLICATE section
        analysis['HOW_TO_REPLICATE'] = {
            "step_by_step_guide": steps,
            "equipment_needed": equipment,
            "content_checklist": checklist,
            "common_mistakes_to_avoid": mistakes,
            "estimated_time": time_estimate
        }

        return analysis

    def _generate_recommendations(self, common_hook: str, use_trending: bool,
                                 top_triggers: list, avg_eng: float, high_performers: list) -> Dict:
        """Generate actionable recommendations based on patterns"""

        recommendations = {
            "hook_strategy": f"Use '{common_hook}' hooks - this is the most effective pattern in your niche",
            "audio_strategy": "Use trending audio to boost algorithm visibility" if use_trending else "Use original voice to build authority and trust",
            "engagement_benchmark": f"Target {avg_eng}%+ engagement rate (current average)",
            "psychological_focus": f"Leverage '{top_triggers[0][0]}' as primary psychological trigger" if top_triggers else "Focus on curiosity and FOMO",
            "content_formula": []
        }

        # Extract patterns from high performers
        if high_performers:
            high_avg_engagement = round(sum(v.get('metrics', {}).get('engagement_rate', 0) for v in high_performers) / len(high_performers), 2)
            recommendations["high_performer_insights"] = {
                "count": len(high_performers),
                "average_engagement": high_avg_engagement,
                "key_takeaways": [
                    "Study these high-performing videos closely",
                    f"They average {high_avg_engagement}% engagement",
                    "Replicate their hook formulas and pacing"
                ]
            }

        # Content formula based on patterns
        if common_hook == "Curiosity":
            recommendations["content_formula"].append("Create curiosity gap in first 3 seconds")
        if use_trending:
            recommendations["content_formula"].append("Use trending audio that fits your niche")
        else:
            recommendations["content_formula"].append("Use confident voiceover to establish authority")

        recommendations["content_formula"].append("Cut every 3-5 seconds for maximum retention")
        recommendations["content_formula"].append(f"Trigger {top_triggers[0][0] if top_triggers else 'FOMO'} emotion")

        return recommendations

    def analyze_local_file(self, file_path: str) -> Dict:
        """
        Analyze a local video file

        Args:
            file_path: Path to local video file

        Returns:
            Analysis dict
        """
        video_path = Path(file_path)

        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {file_path}")

        # Create minimal metadata for local file
        file_size_mb = video_path.stat().st_size / (1024 * 1024)
        video_metadata = {
            'id': 'local_file',
            'webVideoUrl': str(video_path),
            'videoMeta': {
                'playCount': 0,
                'diggCount': 0,
                'commentCount': 0,
                'shareCount': 0,
            }
        }

        print(f"\n📹 Analyzing local file: {video_path.name}")
        print(f"   Size: {file_size_mb:.2f} MB")

        analysis = self.analyze_video_with_gemini(video_path, video_metadata)
        return analysis

    def run_analysis(self, hashtag: Optional[str] = None,
                    profile: Optional[str] = None,
                    local_file: Optional[str] = None,
                    count: int = 5,
                    min_views: int = 0,
                    output_file: Optional[str] = None,
                    keep_videos: bool = True) -> List[Dict]:
        """
        Main analysis pipeline

        Args:
            hashtag: TikTok hashtag to scrape
            profile: TikTok profile to scrape
            local_file: Local video file to analyze
            count: Number of videos to scrape
            min_views: Minimum views filter
            output_file: Custom output filename
            keep_videos: Whether to keep downloaded videos after analysis

        Returns:
            List of analysis dicts
        """
        analyses = []
        downloaded_videos = []

        # Mode 1: Analyze local file
        if local_file:
            print(f"\n{SEPARATOR_LIGHT}")
            print("Mode: Analyzing local file")
            print(SEPARATOR_LIGHT)

            try:
                analysis = self.analyze_local_file(local_file)
                analyses.append(analysis)
            except Exception as e:
                print(f"❌ Error analyzing local file: {e}")
                return []

        # Mode 2: Scrape and analyze TikTok videos
        else:
            print(f"\n{SEPARATOR_LIGHT}")
            print("Mode: TikTok scraping + analysis")
            print(SEPARATOR_LIGHT)

            # Step 1: Scrape videos
            videos = self.scrape_videos(hashtag=hashtag, profile=profile, count=count, min_views=min_views)

            if not videos:
                print("❌ No videos found matching criteria")
                return []

            # Step 2: Analyze each video
            for i, video in enumerate(videos, 1):
                print(f"\n{SEPARATOR_LIGHT}")
                print(f"📹 Processing video {i}/{len(videos)}")
                print(f"   ID: {video.get('id')}")
                print(f"   Views: {video.get('videoMeta', {}).get('playCount', 0):,}")

                try:
                    # Get video path (already downloaded from Key-Value Store)
                    video_url = video.get('videoUrl')
                    if not video_url:
                        print("⚠️  No video file, skipping...")
                        continue

                    video_path = Path(video_url)
                    if not video_path.exists():
                        print("⚠️  Video file not found, skipping...")
                        continue

                    downloaded_videos.append(video_path)

                    # Analyze with Gemini
                    analysis = self.analyze_video_with_gemini(video_path, video)

                    # Try to parse raw_analysis if JSON parsing failed
                    if 'raw_analysis' in analysis:
                        raw = analysis['raw_analysis']
                        # Try to extract JSON from raw text
                        try:
                            start = raw.find('{')
                            end = raw.rfind('}') + 1
                            if start != -1 and end > start:
                                json_str = raw[start:end]
                                parsed = json.loads(json_str)
                                # Merge parsed data with metadata
                                for key in ['video_id', 'url', 'metrics']:
                                    if key in analysis:
                                        parsed[key] = analysis[key]
                                analysis = parsed
                                print("✅ Successfully recovered data from raw analysis")
                        except:
                            pass  # Keep raw_analysis format

                    # Generate HOW_TO_REPLICATE guide automatically (if not already present)
                    if 'HOW_TO_REPLICATE' not in analysis and 'raw_analysis' not in analysis:
                        analysis = self._add_replication_guide(analysis)
                    elif 'raw_analysis' not in analysis:
                        # Has structured data, add guide
                        analysis = self._add_replication_guide(analysis)

                    analyses.append(analysis)

                    # Rate limiting
                    time.sleep(2)

                except Exception as e:
                    print(f"❌ Error processing video: {e}")
                    continue

        # Step 3: Generate meta-analysis report (if enough videos)
        viral_patterns = None
        if len(analyses) >= 3:
            viral_patterns = self.generate_viral_patterns_report(analyses)

        # Step 4: Save results
        if not output_file:
            # Auto-generate timestamped filename
            timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
            if local_file:
                filename = f"analysis_local_{timestamp}.json"
            elif hashtag:
                filename = f"analysis_{hashtag}_{timestamp}.json"
            elif profile:
                filename = f"analysis_{profile.replace('@', '')}_{timestamp}.json"
            else:
                filename = f"analysis_{timestamp}.json"
            output_file = filename

        # Create final output with meta-analysis
        final_output = {
            "viral_patterns": viral_patterns,
            "videos": analyses
        }

        output_path = Path(output_file)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(final_output, f, indent=2, ensure_ascii=False)

        print(f"\n{SEPARATOR_HEAVY}")
        print(f"✅ Analysis complete! Results saved to {output_path}")
        print(f"📊 Analyzed {len(analyses)} videos successfully")
        if viral_patterns:
            print(f"🎯 Viral patterns report included in output")
        print(SEPARATOR_HEAVY)

        # Step 5: Cleanup if requested
        if not keep_videos and downloaded_videos:
            print(f"\n🧹 Cleaning up {len(downloaded_videos)} downloaded videos...")
            for video_path in downloaded_videos:
                try:
                    video_path.unlink()
                    print(f"   🗑️  Deleted: {video_path.name}")
                except Exception as e:
                    print(f"   ⚠️  Could not delete {video_path.name}: {e}")

        return analyses


def main():
    parser = argparse.ArgumentParser(
        description='TikTok Viral Video Analyzer - AI-powered competitor analysis',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --hashtag cooking --count 3
  %(prog)s -t cooking -n 3
      Analyze 3 trending #cooking videos

  %(prog)s --profile @competitor_username --count 5
  %(prog)s -p @competitor_username -n 5
      Analyze 5 videos from a specific profile

  %(prog)s --file ./my-video.mp4
  %(prog)s -f ./my-video.mp4
      Analyze a local video file

  %(prog)s --hashtag fitness --min-views 100000 --no-keep
  %(prog)s -t fitness -v 100000 --no-keep
      Analyze #fitness videos with 100K+ views, delete after analysis
        """
    )

    # Input source (mutually exclusive)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument('--hashtag', '-t', type=str,
                           help='TikTok hashtag to analyze (without #)')
    input_group.add_argument('--profile', '-p', type=str,
                           help='TikTok profile to analyze (with @)')
    input_group.add_argument('--file', '-f', type=str,
                           help='Local video file to analyze')

    # Analysis options
    parser.add_argument('--count', '-n', type=int, default=10,
                       help='Number of videos to analyze (default: 10, minimum recommended for pattern analysis)')
    parser.add_argument('--min-views', '-v', type=int, default=0,
                       help='Minimum views filter (default: 0)')

    # Output options
    parser.add_argument('--output', '-o', type=str, default=None,
                       help='Output file path (default: auto-generated with timestamp)')
    parser.add_argument('--keep', dest='keep', action='store_true',
                       help='Keep downloaded videos after analysis (default)')
    parser.add_argument('--no-keep', dest='keep', action='store_false',
                       help='Delete downloaded videos after analysis')
    parser.set_defaults(keep=True)

    args = parser.parse_args()

    # Print banner
    print(f"\n{SEPARATOR_HEAVY}")
    print("TIKTOK VIRAL VIDEO ANALYZER")
    print("AI-Powered Competitor Analysis System")
    print(SEPARATOR_HEAVY)

    # Check API keys (only if not analyzing local file)
    if not args.file:
        if not APIFY_API_KEY:
            print("\n❌ Error: APIFY_API_KEY not found in .env file")
            print("   Get your API key from: https://console.apify.com/account/integrations")
            return

        if not GEMINI_API_KEY:
            print("\n❌ Error: GEMINI_API_KEY not found in .env file")
            print("   Get your API key from: https://aistudio.google.com/apikey")
            return
    else:
        # Only need Gemini for local file analysis
        if not GEMINI_API_KEY:
            print("\n❌ Error: GEMINI_API_KEY not found in .env file")
            print("   Get your API key from: https://aistudio.google.com/apikey")
            return

    # Run analysis
    analyzer = TikTokAnalyzer()
    try:
        analyzer.run_analysis(
            hashtag=args.hashtag,
            profile=args.profile,
            local_file=args.file,
            count=args.count,
            min_views=args.min_views,
            output_file=args.output,
            keep_videos=args.keep
        )
    except KeyboardInterrupt:
        print("\n\n⚠️  Analysis interrupted by user")
    except Exception as e:
        print(f"\n\n❌ Fatal error: {e}")
        raise


if __name__ == '__main__':
    main()
