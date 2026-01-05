#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script to evaluate recent social posts from the database.
Checks format, completeness, and quality against expected standards.
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

# Add parent directory to path to import models
sys.path.insert(0, str(Path(__file__).parent))

from models import get_db, SocialPost

def evaluate_post(post, index):
    """Evaluate a single post against expected format."""
    issues = []
    warnings = []
    strengths = []
    
    # Required fields check
    if not post.caption or len(post.caption.strip()) < 10:
        issues.append("❌ Caption is missing or too short (< 10 chars)")
    else:
        strengths.append(f"✅ Caption present ({len(post.caption)} chars)")
    
    # Image prompt or carousel check
    has_image_prompt = post.image_prompt and len(post.image_prompt.strip()) > 0
    has_carousel = post.carousel_slides and isinstance(post.carousel_slides, list) and len(post.carousel_slides) > 0
    
    if not has_image_prompt and not has_carousel:
        issues.append("❌ Missing both image_prompt and carousel_slides")
    elif has_image_prompt and has_carousel:
        warnings.append("⚠️ Has both image_prompt and carousel_slides (should use one)")
    elif has_carousel:
        strengths.append(f"✅ Has carousel_slides ({len(post.carousel_slides)} slides)")
        if len(post.carousel_slides) < 2:
            warnings.append("⚠️ Carousel has less than 2 slides (TikTok needs 2-3, FB/IG can have more)")
        if len(post.carousel_slides) > 10:
            warnings.append("⚠️ Carousel has more than 10 slides (FB/IG max)")
    else:
        strengths.append("✅ Has image_prompt")
        # Check if image prompt is detailed enough
        if len(post.image_prompt) < 100:
            warnings.append("⚠️ Image prompt seems too short (< 100 chars)")
        if "IMPAG" not in post.image_prompt.upper():
            warnings.append("⚠️ Image prompt doesn't mention IMPAG branding")
    
    # Channel check
    if not post.channel:
        issues.append("❌ Missing channel field")
    else:
        valid_channels = ['wa-status', 'wa-broadcast', 'wa-message', 'fb-post', 'fb-reel', 'ig-post', 'ig-reel', 'tiktok']
        if post.channel not in valid_channels:
            issues.append(f"❌ Invalid channel: {post.channel} (not in valid list)")
        else:
            strengths.append(f"✅ Valid channel: {post.channel}")
            
            # Channel-specific checks
            if post.channel == 'tiktok':
                if not has_carousel:
                    warnings.append("⚠️ TikTok should use carousel_slides (2-3 slides)")
                if not post.needs_music:
                    warnings.append("⚠️ TikTok content typically needs music")
            elif post.channel in ['fb-reel', 'ig-reel']:
                if not post.needs_music:
                    warnings.append("⚠️ Reels typically need music")
            elif post.channel in ['wa-status', 'wa-broadcast']:
                if has_carousel:
                    warnings.append("⚠️ WhatsApp Status/Broadcast doesn't support carousels")
                if has_image_prompt and '1080x1920' not in post.image_prompt and 'vertical' not in post.image_prompt.lower():
                    warnings.append("⚠️ WhatsApp Status should use vertical format (1080x1920)")
    
    # Post type check
    if not post.post_type:
        warnings.append("⚠️ Missing post_type")
    else:
        strengths.append(f"✅ Post type: {post.post_type}")
    
    # Product selection check
    if post.selected_product_id:
        strengths.append(f"✅ Product selected: {post.selected_product_id}")
    else:
        warnings.append("⚠️ No product selected (might be intentional for generic posts)")
    
    # Formatted content check
    if post.formatted_content:
        if isinstance(post.formatted_content, dict):
            strengths.append("✅ Has formatted_content (JSON)")
            # Check for strategy notes
            if 'strategyNotes' in post.formatted_content or 'notes' in post.formatted_content:
                strengths.append("✅ Has strategy notes")
        else:
            warnings.append("⚠️ formatted_content is not a dict")
    else:
        warnings.append("⚠️ Missing formatted_content")
    
    # Date check
    if post.date_for:
        try:
            date_obj = datetime.strptime(post.date_for, "%Y-%m-%d")
            days_until = (date_obj - datetime.now()).days
            if days_until < 0:
                warnings.append(f"⚠️ Post date is in the past ({abs(days_until)} days ago)")
            elif days_until > 90:
                warnings.append(f"⚠️ Post date is far in the future ({days_until} days)")
            else:
                strengths.append(f"✅ Date for: {post.date_for} ({days_until} days from now)")
        except ValueError:
            issues.append(f"❌ Invalid date format: {post.date_for}")
    else:
        issues.append("❌ Missing date_for")
    
    # Status check
    if not post.status:
        warnings.append("⚠️ Missing status")
    else:
        strengths.append(f"✅ Status: {post.status}")
    
    # Quality checks
    if post.caption:
        # Check for common issues
        if len(post.caption) > 2000:
            warnings.append("⚠️ Caption is very long (> 2000 chars)")
        if post.caption.count('\n') > 10:
            warnings.append("⚠️ Caption has many line breaks (> 10)")
        # Check for contact info
        if 'whatsapp' in post.caption.lower() or '677' in post.caption:
            strengths.append("✅ Caption includes contact info")
        # Check for Durango context usage
        durango_keywords = ['durango', 'temporal', 'heladas', 'siembra', 'cosecha', 'manzana', 'frijol', 'maíz']
        if any(keyword in post.caption.lower() for keyword in durango_keywords):
            strengths.append("✅ Caption uses Durango regional context")
        else:
            warnings.append("⚠️ Caption doesn't seem to use Durango context")
    
    return {
        'index': index,
        'id': post.id,
        'date_for': post.date_for,
        'channel': post.channel,
        'issues': issues,
        'warnings': warnings,
        'strengths': strengths,
        'score': calculate_score(issues, warnings, strengths)
    }

def calculate_score(issues, warnings, strengths):
    """Calculate a quality score (0-100)."""
    score = 100
    score -= len(issues) * 20  # Major issues
    score -= len(warnings) * 5  # Minor warnings
    score += len(strengths) * 2  # Strengths
    return max(0, min(100, score))

def print_evaluation(results):
    """Print evaluation results in a readable format."""
    print("\n" + "="*80)
    print("SOCIAL POSTS EVALUATION REPORT")
    print("="*80)
    print(f"\nTotal posts evaluated: {len(results)}\n")
    
    # Overall statistics
    total_issues = sum(len(r['issues']) for r in results)
    total_warnings = sum(len(r['warnings']) for r in results)
    total_strengths = sum(len(r['strengths']) for r in results)
    avg_score = sum(r['score'] for r in results) / len(results) if results else 0
    
    print(f"Overall Statistics:")
    print(f"  - Total Issues: {total_issues}")
    print(f"  - Total Warnings: {total_warnings}")
    print(f"  - Total Strengths: {total_strengths}")
    print(f"  - Average Score: {avg_score:.1f}/100")
    print()
    
    # Sort by score (lowest first)
    results_sorted = sorted(results, key=lambda x: x['score'])
    
    # Print each post
    for result in results_sorted:
        print("-" * 80)
        print(f"Post #{result['index']} (ID: {result['id']}) - Score: {result['score']}/100")
        print(f"Date: {result['date_for']} | Channel: {result['channel']}")
        print()
        
        if result['issues']:
            print("  ISSUES:")
            for issue in result['issues']:
                print(f"    {issue}")
            print()
        
        if result['warnings']:
            print("  WARNINGS:")
            for warning in result['warnings']:
                print(f"    {warning}")
            print()
        
        if result['strengths']:
            print("  STRENGTHS:")
            for strength in result['strengths']:
                print(f"    {strength}")
            print()
    
    # Summary by channel
    print("\n" + "="*80)
    print("SUMMARY BY CHANNEL")
    print("="*80)
    
    channels = {}
    for result in results:
        channel = result['channel'] or 'unknown'
        if channel not in channels:
            channels[channel] = {'count': 0, 'scores': []}
        channels[channel]['count'] += 1
        channels[channel]['scores'].append(result['score'])
    
    for channel, data in sorted(channels.items()):
        avg_score = sum(data['scores']) / len(data['scores'])
        print(f"{channel}: {data['count']} posts, avg score: {avg_score:.1f}/100")

def main():
    """Main function to evaluate posts."""
    # Get database session
    db_gen = get_db()
    db: Session = next(db_gen)
    
    try:
        # Get last 20 posts
        posts = db.query(SocialPost).order_by(
            SocialPost.created_at.desc()
        ).limit(20).all()
        
        if not posts:
            print("No posts found in database.")
            return
        
        print(f"Found {len(posts)} recent posts. Evaluating...\n")
        
        # Evaluate each post
        results = []
        for i, post in enumerate(posts, 1):
            result = evaluate_post(post, i)
            results.append(result)
        
        # Print evaluation
        print_evaluation(results)
        
        # Print sample posts
        print("\n" + "="*80)
        print("SAMPLE POSTS (First 3)")
        print("="*80)
        
        for i, post in enumerate(posts[:3], 1):
            print(f"\n--- Post #{i} (ID: {post.id}) ---")
            print(f"Date: {post.date_for}")
            print(f"Channel: {post.channel}")
            print(f"Status: {post.status}")
            print(f"Caption: {post.caption[:200]}..." if post.caption and len(post.caption) > 200 else f"Caption: {post.caption}")
            if post.image_prompt:
                print(f"Image Prompt: {post.image_prompt[:200]}..." if len(post.image_prompt) > 200 else f"Image Prompt: {post.image_prompt}")
            if post.carousel_slides:
                print(f"Carousel Slides: {len(post.carousel_slides)} slides")
                for j, slide in enumerate(post.carousel_slides[:2], 1):
                    print(f"  Slide {j}: {slide[:100]}...")
            print()
        
    except Exception as e:
        print(f"Error evaluating posts: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    main()




