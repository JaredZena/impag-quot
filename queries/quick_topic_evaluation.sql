-- =============================================================================
-- QUICK TOPIC EVALUATION - Single Query Summary
-- Run this for a quick overview of topic selection health
-- =============================================================================

WITH recent_posts AS (
    SELECT 
        id,
        date_for,
        created_at,
        topic,
        topic_hash,
        problem_identified,
        post_type,
        channel,
        CASE 
            WHEN topic = 'sin tema → sin solución' THEN 1 ELSE 0 
        END as is_placeholder,
        CASE 
            WHEN topic NOT LIKE '%→%' THEN 1 ELSE 0 
        END as invalid_format
    FROM social_post
    WHERE created_at >= NOW() - INTERVAL '30 days'
),
duplicate_check AS (
    SELECT 
        COUNT(*) as duplicate_count
    FROM social_post sp1
    INNER JOIN social_post sp2 
        ON sp1.topic_hash = sp2.topic_hash 
        AND sp1.id < sp2.id
        AND ABS(sp1.date_for - sp2.date_for) <= 10
    WHERE sp1.created_at >= NOW() - INTERVAL '30 days'
),
problem_duplicate_check AS (
    WITH topic_parts AS (
        SELECT 
            id,
            date_for,
            topic,
            TRIM(SPLIT_PART(topic, '→', 1)) as problem_part,
            TRIM(SPLIT_PART(topic, '→', 2)) as solution_part
        FROM social_post
        WHERE created_at >= NOW() - INTERVAL '30 days'
            AND topic LIKE '%→%'
            AND topic != 'sin tema → sin solución'
    )
    SELECT COUNT(*) as problem_duplicate_count
    FROM topic_parts tp1
    INNER JOIN topic_parts tp2 
        ON LOWER(tp1.problem_part) = LOWER(tp2.problem_part)
        AND tp1.id < tp2.id
        AND tp1.solution_part != tp2.solution_part
        AND ABS(tp1.date_for - tp2.date_for) <= 3
)
SELECT 
    '📊 TOPIC SELECTION HEALTH REPORT' as report_title,
    '' as separator,
    'Total Posts (30 days):' as metric,
    COUNT(*)::text as value
FROM recent_posts
UNION ALL
SELECT 
    '',
    '',
    'Unique Topics:',
    COUNT(DISTINCT topic_hash)::text
FROM recent_posts
UNION ALL
SELECT 
    '',
    '',
    'Topic Variety %:',
    ROUND(100.0 * COUNT(DISTINCT topic_hash) / NULLIF(COUNT(*), 0), 1)::text || '%'
FROM recent_posts
UNION ALL
SELECT 
    '',
    '',
    '⚠️ Placeholder Topics:',
    SUM(is_placeholder)::text || ' (' || ROUND(100.0 * SUM(is_placeholder) / NULLIF(COUNT(*), 0), 1) || '%)'
FROM recent_posts
UNION ALL
SELECT 
    '',
    '',
    '❌ Invalid Format Topics:',
    SUM(invalid_format)::text || ' (' || ROUND(100.0 * SUM(invalid_format) / NULLIF(COUNT(*), 0), 1) || '%)'
FROM recent_posts
UNION ALL
SELECT 
    '',
    '',
    '✅ Posts with Problem Identified:',
    COUNT(problem_identified)::text || ' (' || ROUND(100.0 * COUNT(problem_identified) / NULLIF(COUNT(*), 0), 1) || '%)'
FROM recent_posts
UNION ALL
SELECT 
    '',
    '',
    '🔴 Topic Duplicates (10 days):',
    COALESCE((SELECT duplicate_count FROM duplicate_check), 0)::text || 
    CASE WHEN (SELECT duplicate_count FROM duplicate_check) = 0 THEN ' ✅' ELSE ' ❌ FAIL' END
UNION ALL
SELECT 
    '',
    '',
    '🟡 Problem Duplicates (3 days):',
    COALESCE((SELECT problem_duplicate_count FROM problem_duplicate_check), 0)::text || 
    CASE WHEN (SELECT problem_duplicate_count FROM problem_duplicate_check) = 0 THEN ' ✅' ELSE ' ⚠️ WARN' END
UNION ALL
SELECT 
    '',
    '',
    '📅 Date Range:',
    MIN(date_for)::text || ' to ' || MAX(date_for)::text
FROM recent_posts
ORDER BY 
    CASE 
        WHEN report_title LIKE '%HEALTH%' THEN 1
        WHEN metric LIKE '%Duplicates%' THEN 2
        WHEN metric LIKE '%Placeholder%' OR metric LIKE '%Invalid%' THEN 3
        ELSE 4
    END,
    metric;

