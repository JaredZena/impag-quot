# Social Post Generation Cost Analysis

## Current Cost Drivers (>$0.05 USD per post)

### 1. **Premium Model Usage**
- **Model**: `claude-sonnet-4-5-20250929` (Sonnet 4.5 - premium tier)
- **Estimated pricing**: ~$3 per 1M input tokens, ~$15 per 1M output tokens
- **Impact**: Using the most expensive Claude model

### 2. **Extremely Long Prompts**

#### Strategy Phase Prompt (~4,000-5,000 tokens):
- Full Durango context (agricultura, forestal, ganadería, agroindustria)
- Long history summary (last 20 posts)
- Extensive variety analysis
- Detailed post type definitions
- Multiple examples and instructions
- **Estimated cost**: ~$0.012-0.015 (input) + ~$0.0045 (output 300 tokens) = **~$0.016-0.019**

#### Content Phase Prompt (~6,000-8,000 tokens):
- Full strategy data
- Product details (if selected)
- Durango context again
- Extensive deduplication info
- Long channel format definitions
- Multiple structure guides
- Detailed image prompt instructions
- **Estimated cost**: ~$0.018-0.024 (input) + ~$0.030 (output 2000 tokens) = **~$0.048-0.054**

#### Total per post: **~$0.064-0.073 USD**

### 3. **Redundant Context Loading**
- Durango context loaded twice (once for problems, once for content)
- Full context files loaded even though only month-specific sections needed
- History includes full post details when only topics/types needed

### 4. **Large Output Tokens**
- Content phase: `max_tokens=2000` (very high for social posts)
- Strategy phase: `max_tokens=300` (reasonable)

### 5. **Potential Retries**
- Strategy can retry once (doubles cost if triggered)
- Content can retry once (doubles cost if triggered)

---

## Optimization Recommendations

### **Priority 1: Reduce Prompt Size (Biggest Impact)**

#### A. Truncate Durango Context
**Current**: Loading full context files, then truncating to 800 chars
**Optimization**: Only load month-specific sections, skip other sectors if not relevant

```python
# Current: ~2000 tokens per sector × 4 sectors = 8000 tokens
# Optimized: ~500 tokens per sector × 2 relevant sectors = 1000 tokens
# Savings: ~7000 tokens = ~$0.021 per post
```

#### B. Simplify History Summary
**Current**: Full post details with topics, types, channels, products
**Optimization**: Only last 5-10 posts, only topics and types (no full details)

```python
# Current: ~1500 tokens for 20 posts
# Optimized: ~300 tokens for 10 posts (topics only)
# Savings: ~1200 tokens = ~$0.0036 per post
```

#### C. Remove Redundant Instructions
**Current**: Repeating variety rules, examples, and warnings multiple times
**Optimization**: Consolidate into single concise section

```python
# Current: ~2000 tokens of repetitive instructions
# Optimized: ~800 tokens (consolidated)
# Savings: ~1200 tokens = ~$0.0036 per post
```

#### D. Simplify Content Prompt Structure Guides
**Current**: Multiple detailed structure guides (comparative, tutorial, system, multi-panel)
**Optimization**: Single concise guide with conditional logic

```python
# Current: ~1500 tokens of structure guides
# Optimized: ~400 tokens (single guide)
# Savings: ~1100 tokens = ~$0.0033 per post
```

**Total Prompt Reduction Savings: ~$0.0315 per post**

---

### **Priority 2: Reduce Output Tokens**

#### A. Lower Content Phase Max Tokens
**Current**: `max_tokens=2000`
**Optimization**: `max_tokens=1000` (social posts don't need 2000 tokens)

```python
# Current: ~2000 tokens output = $0.030
# Optimized: ~1000 tokens output = $0.015
# Savings: $0.015 per post
```

#### B. Keep Strategy Phase Low (Already Good)
- `max_tokens=300` is reasonable

**Total Output Reduction Savings: $0.015 per post**

---

### **Priority 3: Use Cheaper Model (If Quality Allows)**

#### Option A: Use Claude Haiku for Content Phase
**Current**: Sonnet 4.5 for both phases
**Optimization**: Keep Sonnet for strategy, use Haiku for content

```python
# Haiku pricing: ~$0.25 per 1M input, ~$1.25 per 1M output
# Content phase with Haiku:
#   Input: 6000 tokens × $0.25/1M = $0.0015 (vs $0.018 with Sonnet)
#   Output: 1000 tokens × $1.25/1M = $0.00125 (vs $0.015 with Sonnet)
# Savings: ~$0.030 per post
```

#### Option B: Use Haiku for Both Phases
**Risk**: Lower quality, but might be acceptable for structured JSON output
**Savings**: ~$0.045 per post

---

### **Priority 4: Cache Static Content**

#### Cache Durango Context
**Current**: Loading and processing context files every time
**Optimization**: Cache processed context in memory (already done, but verify it's working)

#### Cache Post Type Definitions
**Current**: Including full POST_TYPES_DEFINITIONS in every prompt
**Optimization**: Reference by ID, only include if needed

---

## Expected Cost After Optimizations

### Conservative (Prompt + Output Reduction Only):
- **Current**: ~$0.064-0.073 per post
- **After optimizations**: ~$0.017-0.026 per post
- **Savings**: ~60-65% reduction

### Aggressive (Prompt + Output + Haiku for Content):
- **Current**: ~$0.064-0.073 per post
- **After optimizations**: ~$0.003-0.012 per post
- **Savings**: ~80-85% reduction

---

## Implementation Priority

1. **Immediate (High Impact, Low Risk)**:
   - Reduce content phase `max_tokens` from 2000 to 1000
   - Truncate Durango context more aggressively
   - Simplify history summary to last 10 posts, topics only

2. **Short Term (High Impact, Medium Risk)**:
   - Consolidate redundant instructions
   - Simplify structure guides
   - Use Haiku for content phase (test quality first)

3. **Long Term (Medium Impact, Low Risk)**:
   - Cache static prompt sections
   - Reference post types by ID instead of full definitions
   - Optimize retry logic to avoid unnecessary retries

---

## Risk Assessment

- **Prompt reduction**: Low risk - same information, just more concise
- **Output token reduction**: Low risk - 1000 tokens is plenty for social posts
- **Model downgrade**: Medium risk - test quality first, can revert if needed

