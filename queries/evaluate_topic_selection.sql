-- =============================================================================
-- Topic Selection Evaluation Queries
-- Use these queries to evaluate if topic-based deduplication is working correctly
-- =============================================================================

-- 1. RECENT POSTS WITH TOPICS (Last 30 days)
-- Shows all recent posts with their topics, dates, and key info
SELECT 
    id,
    date_for,
    created_at,
    topic,
    topic_hash,
    problem_identified,
    post_type,
    channel,
    selected_product_id,
    status,
    CASE 
        WHEN topic = 'sin tema → sin solución' THEN '⚠️ PLACEHOLDER'
        WHEN topic NOT LIKE '%→%' THEN '❌ INVALID FORMAT'
        ELSE '✅ VALID'
    END as topic_status
FROM social_post
WHERE created_at >= NOW() - INTERVAL '30 days'
ORDER BY created_at DESC
LIMIT 50;

-- 2. TOPIC DUPLICATES CHECK (Hard Rule: No same topic_hash within 10 days)
-- This should return ZERO rows if deduplication is working correctly
SELECT 
    sp1.id as post1_id,
    sp1.date_for as post1_date,
    sp1.topic as post1_topic,
    sp1.topic_hash,
    sp2.id as post2_id,
    sp2.date_for as post2_date,
    sp2.topic as post2_topic,
    ABS(sp1.date_for - sp2.date_for) as days_apart
FROM social_post sp1
INNER JOIN social_post sp2 
    ON sp1.topic_hash = sp2.topic_hash 
    AND sp1.id < sp2.id
    AND ABS(sp1.date_for - sp2.date_for) <= 10
WHERE sp1.created_at >= NOW() - INTERVAL '30 days'
ORDER BY sp1.date_for DESC, days_apart ASC;

-- 3. PROBLEM DUPLICATES CHECK (Soft Rule: Same problem with different solution within 3 days)
-- Extracts problem part (left of →) and checks for duplicates
WITH topic_parts AS (
    SELECT 
        id,
        date_for,
        topic,
        topic_hash,
        TRIM(SPLIT_PART(topic, '→', 1)) as problem_part,
        TRIM(SPLIT_PART(topic, '→', 2)) as solution_part
    FROM social_post
    WHERE created_at >= NOW() - INTERVAL '30 days'
        AND topic LIKE '%→%'
        AND topic != 'sin tema → sin solución'
)
SELECT 
    tp1.id as post1_id,
    tp1.date_for as post1_date,
    tp1.topic as post1_topic,
    tp1.problem_part,
    tp2.id as post2_id,
    tp2.date_for as post2_date,
    tp2.topic as post2_topic,
    tp2.problem_part,
    ABS(tp1.date_for - tp2.date_for) as days_apart
FROM topic_parts tp1
INNER JOIN topic_parts tp2 
    ON LOWER(tp1.problem_part) = LOWER(tp2.problem_part)
    AND tp1.id < tp2.id
    AND tp1.solution_part != tp2.solution_part  -- Different solutions
    AND ABS(tp1.date_for - tp2.date_for) <= 3
ORDER BY tp1.date_for DESC, days_apart ASC;

-- 4. TOPIC VARIETY METRICS (Last 30 days)
-- Shows distribution of topics, post types, channels
SELECT 
    COUNT(*) as total_posts,
    COUNT(DISTINCT topic_hash) as unique_topics,
    COUNT(DISTINCT post_type) as unique_post_types,
    COUNT(DISTINCT channel) as unique_channels,
    COUNT(*) FILTER (WHERE topic = 'sin tema → sin solución') as placeholder_topics,
    COUNT(*) FILTER (WHERE topic NOT LIKE '%→%') as invalid_format_topics,
    ROUND(100.0 * COUNT(DISTINCT topic_hash) / NULLIF(COUNT(*), 0), 2) as topic_variety_percent
FROM social_post
WHERE created_at >= NOW() - INTERVAL '30 days';

-- 5. TOPIC DISTRIBUTION BY DAY (Last 14 days)
-- Shows how many unique topics per day
SELECT 
    date_for,
    COUNT(*) as posts_count,
    COUNT(DISTINCT topic_hash) as unique_topics,
    COUNT(DISTINCT post_type) as post_types,
    COUNT(DISTINCT channel) as channels,
    STRING_AGG(DISTINCT topic, ' | ' ORDER BY topic) as topics_list
FROM social_post
WHERE created_at >= NOW() - INTERVAL '14 days'
GROUP BY date_for
ORDER BY date_for DESC;

-- 6. MOST COMMON TOPICS (Last 30 days)
-- Shows which topics are being repeated (should be minimal)
SELECT 
    topic,
    topic_hash,
    COUNT(*) as occurrence_count,
    MIN(date_for) as first_used,
    MAX(date_for) as last_used,
    STRING_AGG(DISTINCT date_for::text, ', ' ORDER BY date_for) as dates_used
FROM social_post
WHERE created_at >= NOW() - INTERVAL '30 days'
    AND topic != 'sin tema → sin solución'
GROUP BY topic, topic_hash
HAVING COUNT(*) > 1
ORDER BY occurrence_count DESC, last_used DESC;

-- 7. MISSING OR INVALID TOPICS (Should be ZERO)
-- Checks for posts without proper topics
SELECT 
    id,
    date_for,
    created_at,
    topic,
    topic_hash,
    CASE 
        WHEN topic IS NULL THEN '❌ NULL TOPIC'
        WHEN topic = '' THEN '❌ EMPTY TOPIC'
        WHEN topic = 'sin tema → sin solución' THEN '⚠️ PLACEHOLDER'
        WHEN topic NOT LIKE '%→%' THEN '❌ NO ARROW'
        WHEN LENGTH(TRIM(SPLIT_PART(topic, '→', 1))) < 10 THEN '❌ PROBLEM TOO SHORT'
        WHEN LENGTH(TRIM(SPLIT_PART(topic, '→', 2))) < 8 THEN '❌ SOLUTION TOO SHORT'
        ELSE '✅ VALID'
    END as validation_status
FROM social_post
WHERE created_at >= NOW() - INTERVAL '30 days'
    AND (
        topic IS NULL 
        OR topic = ''
        OR topic = 'sin tema → sin solución'
        OR topic NOT LIKE '%→%'
        OR LENGTH(TRIM(SPLIT_PART(topic, '→', 1))) < 10
        OR LENGTH(TRIM(SPLIT_PART(topic, '→', 2))) < 8
    )
ORDER BY created_at DESC;

-- 8. TOPIC NORMALIZATION CHECK
-- Verifies that topic_hash matches normalized topic
-- (This is a sanity check - all should match)
SELECT 
    id,
    date_for,
    topic,
    topic_hash,
    LOWER(TRIM(REGEXP_REPLACE(topic, '\s+', ' ', 'g'))) as normalized_manual,
    CASE 
        WHEN topic_hash = encode(digest(LOWER(TRIM(REGEXP_REPLACE(topic, '\s+', ' ', 'g'))), 'sha256'), 'hex') 
        THEN '✅ MATCH'
        ELSE '❌ MISMATCH'
    END as hash_check
FROM social_post
WHERE created_at >= NOW() - INTERVAL '30 days'
    AND topic != 'sin tema → sin solución'
ORDER BY created_at DESC
LIMIT 20;

-- 9. PROBLEM IDENTIFICATION COVERAGE
-- Shows how many posts have problem_identified field populated
SELECT 
    COUNT(*) as total_posts,
    COUNT(problem_identified) as posts_with_problem,
    COUNT(*) - COUNT(problem_identified) as posts_without_problem,
    ROUND(100.0 * COUNT(problem_identified) / NULLIF(COUNT(*), 0), 2) as coverage_percent
FROM social_post
WHERE created_at >= NOW() - INTERVAL '30 days';

-- 10. RECENT POSTS SUMMARY (Last 7 days - Quick Overview)
SELECT 
    date_for,
    COUNT(*) as posts,
    COUNT(DISTINCT topic_hash) as unique_topics,
    COUNT(DISTINCT post_type) as post_types,
    COUNT(DISTINCT channel) as channels,
    STRING_AGG(
        DISTINCT 
        CASE 
            WHEN LENGTH(topic) > 50 THEN LEFT(topic, 47) || '...'
            ELSE topic
        END, 
        ' | ' 
        ORDER BY topic
    ) as topics_preview
FROM social_post
WHERE date_for >= CURRENT_DATE - INTERVAL '7 days'
GROUP BY date_for
ORDER BY date_for DESC;

