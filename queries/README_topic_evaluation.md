# Topic Selection Evaluation Queries

This directory contains SQL queries to evaluate the topic-based deduplication system.

## Quick Start

Run these queries in your PostgreSQL database to check if topic selection is working correctly.

### Most Important Queries

1. **Query #2 - Topic Duplicates Check**
   - Should return **ZERO rows** if hard deduplication rule is working
   - Checks for same `topic_hash` within 10 days

2. **Query #3 - Problem Duplicates Check**
   - Should return **ZERO rows** if soft deduplication rule is working
   - Checks for same problem (left of →) with different solution within 3 days

3. **Query #7 - Missing or Invalid Topics**
   - Should return **ZERO rows** after migration
   - Checks for posts without proper topic format

### How to Run

```bash
# Using psql
psql -d your_database -f queries/evaluate_topic_selection.sql

# Or run individual queries
psql -d your_database -c "SELECT ... FROM social_post ..."
```

### Expected Results

✅ **Good Signs:**
- Query #2 returns 0 rows (no topic duplicates within 10 days)
- Query #3 returns 0 rows (no problem duplicates within 3 days)
- Query #7 returns 0 rows (all topics valid)
- Query #4 shows high `topic_variety_percent` (>80%)
- Query #9 shows high `coverage_percent` for `problem_identified` (>90%)

❌ **Warning Signs:**
- Query #2 returns rows → Hard deduplication not working
- Query #3 returns rows → Soft deduplication not working
- Query #7 returns rows → Topics not being set correctly
- Query #4 shows many `placeholder_topics` → LLM not returning topics
- Query #6 shows same topic used multiple times → Deduplication failing

### Query Descriptions

1. **Recent Posts with Topics** - Overview of last 30 days
2. **Topic Duplicates Check** - Hard rule violation check
3. **Problem Duplicates Check** - Soft rule violation check
4. **Topic Variety Metrics** - Overall statistics
5. **Topic Distribution by Day** - Daily breakdown
6. **Most Common Topics** - Repeated topics analysis
7. **Missing or Invalid Topics** - Data quality check
8. **Topic Normalization Check** - Hash verification
9. **Problem Identification Coverage** - Field completeness
10. **Recent Posts Summary** - Quick 7-day overview

