# Cost Optimization - Clarified Strategy

## What I Meant by "Redundant Context"

### Durango Context Usage:
1. **Loaded once** (line 1459): `durango_context = social_context.load_durango_context(...)` ✅ Good - cached
2. **Used in Strategy prompt** (line 1520): `durango_context[:500]` - **Truncated to 500 chars**
3. **Used in Content prompt** (line 1877): `durango_context` - **Full context (could be 2000+ chars)**

**Not actually redundant** - it's loaded once and cached. But it IS sent in both prompts:
- Strategy gets truncated version (500 chars) ✅
- Content gets full version (2000+ chars) - This is where we can optimize

### History Summary:
The `build_history_summary` function includes for each of the last 20 posts:
- Post type
- Channel  
- Topic
- Product ID

If you have 20 recent posts, this could be ~1000-1500 tokens. This is necessary for deduplication, so it's not really "redundant" - it's needed.

---

## Your Strategy Makes Perfect Sense! ✅

### Strategy Phase: High Input + Low Output
**Current:**
- Input: ~4,000-5,000 tokens (context, history, problems, instructions)
- Output: 300 tokens (just JSON: topic, post_type, channel, etc.)
- **Makes sense**: Need lots of context to make good strategic decisions, but output is small structured data

### Content Phase: Lower Input + High Output  
**Current:**
- Input: ~6,000-8,000 tokens (strategy results + full Durango context + product details + instructions)
- Output: 2000 tokens (full caption, image_prompt, carousel_slides, etc.)
- **Makes sense**: Content generation needs the output space, but input can be optimized

---

## Optimization Strategy (Keeping max_tokens=2000)

### Goal: Reduce Content Phase INPUT tokens while keeping OUTPUT at 2000

### 1. **Truncate Durango Context in Content Phase**
**Current**: Full `durango_context` (~2000 chars = ~500 tokens)
**Optimization**: Truncate to 800 chars (same as strategy, or even less since strategy already provided context)

```python
# Line 1877 - Instead of full context:
f"{durango_context[:800]}...\n\n"  # Instead of full durango_context
```

**Savings**: ~300-400 tokens = ~$0.001-0.0012 per post

### 2. **Simplify Content Prompt Instructions**
**Current**: Very detailed structure guides, multiple examples, extensive formatting rules
**Optimization**: Consolidate into concise instructions, reference strategy phase decisions

**Potential savings**: ~1000-1500 tokens = ~$0.003-0.0045 per post

### 3. **Remove Redundant Strategy Info in Content Phase**
**Current**: Re-sending full strategy context
**Optimization**: Only send essential strategy results (topic, post_type, channel, problem_identified)

**Potential savings**: ~500 tokens = ~$0.0015 per post

### 4. **Optimize Deduplication Info**
**Current**: Full deduplication context with all recent posts
**Optimization**: Only send what's needed (recent topics, channels, products - not full history)

**Potential savings**: ~300 tokens = ~$0.0009 per post

---

## Expected Impact

**Current Content Phase Input**: ~6,000-8,000 tokens
**After Optimizations**: ~3,500-4,500 tokens
**Savings**: ~2,000-3,500 tokens = ~$0.006-0.0105 per post

**Total Cost Reduction**: ~15-20% (from ~$0.064 to ~$0.054-0.058 per post)

---

## Implementation Priority

1. **Truncate Durango context in content phase** (line 1877) - Easy, low risk
2. **Simplify content prompt structure guides** - Medium effort, low risk
3. **Optimize deduplication info** - Medium effort, need to verify it doesn't break deduplication

