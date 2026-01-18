# Topic Selection Evaluation - User Posts Analysis

## Posts Analyzed: 5 posts (Jan 16-18, 2026)

### 📊 Summary Statistics

- **Total Posts**: 5
- **Date Range**: 2026-01-16 to 2026-01-18 (3 days)
- **Unique Topics**: 4 (1 duplicate detected)
- **Topic Variety**: 80% (4/5 unique)
- **Post Types**: 2 types (Tutorial corto: 4, Checklist: 1)
- **Channels**: Multiple (TikTok, Reels, WA Broadcast)

---

## 🔴 CRITICAL ISSUE: Topic Duplication Detected

### Duplicate Topics (Same Problem, Different Wording)

**Post #214** (2026-01-17):
- Topic: `sustrato seco causa germinación desigual → técnica correcta de hidratación pre-siembra`
- Hash: `d77df4025971904d594d47da843d246cd6704649e25702cf551b2ac270eb0404`
- Created: 2026-01-17 21:56:36
- Date For: 2026-01-17

**Post #210** (2026-01-18):
- Topic: `sustrato seco causa germinación desigual → técnica correcta de hidratación de charolas`
- Hash: `ef73360ad766ca20d89a2dbcf464b1148faa9fa9eb724eb61509dd349cd2b817`
- Created: 2026-01-17 21:36:03
- Date For: 2026-01-18

**Analysis:**
- ✅ Different hashes (correct - different solution wording)
- ❌ **SAME PROBLEM** ("sustrato seco causa germinación desigual")
- ⚠️ **WITHIN 1 DAY** (Jan 17 → Jan 18)
- ❌ **VIOLATES SOFT RULE** (same problem with different solution within 3 days)

**Root Cause Identified:**
The batch generation creates posts for different `date_for` values (Jan 17 and Jan 18). When checking for duplicates:
1. Post #210 (date_for = Jan 18) checks backwards 3 days from Jan 18 → includes Jan 15-18
2. At generation time (21:36:03), Post #214 didn't exist yet, so it passed
3. Post #214 (date_for = Jan 17) checks backwards 3 days from Jan 17 → includes Jan 14-17
4. Post #210 has date_for = Jan 18 (FUTURE date), so it's not in the range → missed!

**The Bug:** `check_problem_duplicate()` only looks backwards from `date_for`, not forwards. It should check posts within ±3 days, not just -3 days.

---

## ✅ Positive Aspects

### 1. Topic Format Quality
- ✅ All topics follow "Problema → Solución" format
- ✅ Problem parts are specific and actionable (10+ chars)
- ✅ Solution parts are clear and implementable (8+ chars)
- ✅ No placeholder topics ("sin tema → sin solución")

### 2. Problem Identification
- ✅ All posts have detailed `problem_identified` field
- ✅ Problems are specific with impact metrics (e.g., "30-50% pérdida")
- ✅ Problems are time-relevant (January germination season)

### 3. Topic Variety (Overall)
- ✅ 4 different problems addressed:
  1. Sustrato seco → Hidratación
  2. Heladas → Protección antiheladas
  3. Temperatura inadecuada → Control térmico
  4. Riego excesivo → Riego por capilaridad
- ✅ All topics are relevant to January agricultural phase

### 4. Strategy Quality
- ✅ Post types match problem urgency (Tutorial for urgent, Checklist for planning)
- ✅ Channels selected appropriately (TikTok for engagement, WA for broadcast)
- ✅ Instructions are detailed and actionable
- ✅ Content is educational (not just promotional)

---

## ⚠️ Areas for Improvement

### 1. Deduplication Not Working (CRITICAL BUG)
- ❌ Soft rule failed: Same problem within 1 day should be blocked
- **Bug Location**: `check_problem_duplicate()` in `social_dedupe.py` line 152-159
- **Issue**: Only checks backwards, not forwards from `date_for`
- **Fix Required**: Change date range to check ±3 days, not just -3 days

### 2. Post Type Variety
- ⚠️ 4 out of 5 posts are "Tutorial corto" (80%)
- ⚠️ Only 1 "Checklist operativo"
- **Recommendation**: More variety in post types (Infografías, Memes/tips, Casos de éxito)

### 3. Date Distribution
- ⚠️ 3 posts on same day (Jan 17)
- ⚠️ 1 post for Jan 16 (past date)
- ⚠️ 1 post for Jan 18 (future date)
- **Note**: This is expected for batch generation, but ensure date distribution is intentional

---

## 📈 Duplication Score: **6/10**

### Breakdown:
- **Hard Rule (Topic Hash)**: ✅ 10/10 - No exact duplicates
- **Soft Rule (Problem)**: ❌ 0/10 - Duplicate problem detected (BUG)
- **Topic Format**: ✅ 10/10 - All valid format
- **Variety**: ✅ 8/10 - Good variety, but post type repetition
- **Relevance**: ✅ 10/10 - All topics relevant to season

### Overall: **6/10** (Needs Improvement)

**Main Issue**: Soft deduplication rule has a bug - only checks backwards, not forwards.

---

## 🔧 Recommended Actions

### Immediate (Critical - Fix Bug)
1. **Fix `check_problem_duplicate()` Function**
   ```python
   # Current (WRONG):
   cutoff_date = date_obj - timedelta(days=days_back)
   recent_posts = db.query(SocialPost).filter(
       SocialPost.date_for >= cutoff_date,
       SocialPost.date_for <= date_obj
   ).all()
   
   # Should be (CORRECT):
   start_date = date_obj - timedelta(days=days_back)
   end_date = date_obj + timedelta(days=days_back)  # Also check forwards
   recent_posts = db.query(SocialPost).filter(
       SocialPost.date_for >= start_date,
       SocialPost.date_for <= end_date
   ).all()
   ```
   
   **File**: `impag-quot/routes/social_dedupe.py` line 152-159

2. **Also Fix `check_topic_duplicate()` for Consistency**
   - Same issue: only checks backwards
   - Should check ±10 days for hard rule too

### Short-term (Important)
3. **Increase Post Type Variety**
   - Adjust strategy prompt to penalize recent post types
   - Add more variety rules to LLM prompt

4. **Generate More Test Data**
   - Generate posts for 7-10 days to better evaluate patterns
   - Check for other deduplication issues
   - Verify topic variety over time

---

## 📝 Recommendation: Generate More Posts

**YES, you should generate more posts** for better evaluation:

### Why:
1. **Small Sample Size**: 5 posts over 3 days is too small
2. **Pattern Detection**: Need 10-20 posts to see patterns
3. **Deduplication Testing**: Need more data to verify fixes work
4. **Variety Analysis**: Can't assess variety with only 5 posts

### Suggested Test:
- Generate posts for **7-10 consecutive days**
- Target: **2-3 posts per day** (14-30 total posts)
- **After fixing the bug**, run evaluation queries to check:
  - Topic duplicates (should be 0)
  - Problem duplicates (should be 0)
  - Topic variety (should be >80%)
  - Post type distribution
  - Channel distribution

---

## 🧪 Test Query to Run After Fix

```sql
-- Check for problem duplicates in your generated posts
WITH topic_parts AS (
    SELECT 
        id,
        date_for,
        topic,
        TRIM(SPLIT_PART(topic, '→', 1)) as problem_part,
        TRIM(SPLIT_PART(topic, '→', 2)) as solution_part
    FROM social_post
    WHERE created_at > '2026-01-17'
        AND topic LIKE '%→%'
)
SELECT 
    tp1.id as post1_id,
    tp1.date_for as post1_date,
    tp1.topic as post1_topic,
    tp2.id as post2_id,
    tp2.date_for as post2_date,
    tp2.topic as post2_topic,
    ABS(tp1.date_for - tp2.date_for) as days_apart
FROM topic_parts tp1
INNER JOIN topic_parts tp2 
    ON LOWER(tp1.problem_part) = LOWER(tp2.problem_part)
    AND tp1.id < tp2.id
    AND tp1.solution_part != tp2.solution_part
    AND ABS(tp1.date_for - tp2.date_for) <= 3
ORDER BY tp1.date_for DESC;
```

This should return the duplicate you found (posts 214 and 210).
