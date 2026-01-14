# How to Verify Production Hardening Changes

## Prerequisites

1. Ensure you have access to the database
2. Ensure environment variables are set (CLAUDE_API_KEY, DATABASE_URL, etc.)
3. Have a test user with Google OAuth token

## Step 1: Run Migration

```bash
cd impag-quot
python migrations/add_social_post_hardening.py
```

**Expected Output**:
```
Adding 'external_id' column to 'social_post' table...
✓ Added 'external_id' column
Creating index on 'external_id'...
✓ Created index on 'external_id'
Migrating 'date_for' from VARCHAR to DATE...
✓ Migrated 'date_for' to DATE type
Creating composite index on (date_for, created_at)...
✓ Created composite index on (date_for, created_at)
Creating index on formatted_content->>'id'...
  Converting formatted_content from JSON to JSONB...
  ✓ Converted formatted_content to JSONB
✓ Created index on formatted_content->>'id'
Creating index on supplier_product(is_active, archived_at)...
✓ Created index on supplier_product(is_active, archived_at)
Creating index on supplier_product(category_id, is_active)...
✓ Created index on supplier_product(category_id, is_active)

✅ Migration completed successfully!
```

## Step 2: Verify Database Schema

```sql
-- Check external_id column exists
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'social_post' 
AND column_name = 'external_id';

-- Check date_for is DATE type
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'social_post' 
AND column_name = 'date_for';

-- Check indexes exist
SELECT indexname 
FROM pg_indexes 
WHERE tablename = 'social_post' 
AND indexname LIKE 'idx_social_post%';
```

## Step 3: Test Rate Limiting

```bash
# Make 21 rapid requests to /generate endpoint
# Should get 429 on 21st request

for i in {1..21}; do
  curl -X POST http://localhost:8000/social/generate \
    -H "Authorization: Bearer YOUR_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"date": "2024-12-15"}'
  echo "Request $i"
done
```

**Expected**: Request 21 should return `429 Too Many Requests` with message about rate limit.

## Step 4: Test Date Handling

```bash
# Test GET endpoint with date range
curl "http://localhost:8000/social/posts?start_date=2024-01-01&end_date=2024-12-31" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

**Expected**: Should return posts correctly filtered by date range (using DATE comparison, not string).

## Step 5: Test /save with external_id

```bash
# Save a post
curl -X POST http://localhost:8000/social/save \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "date_for": "2024-12-15",
    "caption": "Test post",
    "formatted_content": {"id": "test-123"}
  }'

# Save again with same external_id (should update, not create)
curl -X POST http://localhost:8000/social/save \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "date_for": "2024-12-15",
    "caption": "Updated post",
    "formatted_content": {"id": "test-123"}
  }'
```

**Expected**: 
- First request: `{"status": "success", "id": X, "updated": false}`
- Second request: `{"status": "success", "id": X, "updated": true}` (same ID)

**Verify in database**:
```sql
SELECT id, external_id, caption 
FROM social_post 
WHERE external_id = 'test-123';
-- Should show only ONE row with updated caption
```

## Step 6: Test JSON Parsing

```bash
# Generate a post (should always return valid JSON)
curl -X POST http://localhost:8000/social/generate \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"date": "2024-12-15"}'
```

**Expected**: 
- Response should always be valid JSON
- Check logs: Should NOT see "JSON Parse Error" or "Regex extraction"
- If LLM returns invalid JSON, should see "JSON parse error (attempt 1). Retrying..." and then success

## Step 7: Test Structured Logging

Check application logs for:
- No `print()` statements
- All logs use structured format
- Sensitive data (API keys, tokens) are redacted

**Example log check**:
```bash
# Look for redacted API keys
grep -i "api.*key" logs/app.log
# Should see: "api_key=***REDACTED***" not actual keys
```

## Step 8: Test Product Randomness

```bash
# Generate multiple posts and check product selection
# Products should vary (not always same order)
for i in {1..5}; do
  curl -X POST http://localhost:8000/social/generate \
    -H "Authorization: Bearer YOUR_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"date": "2024-12-15", "category": "riego"}' \
    | jq '.selected_product_id'
done
```

**Expected**: Product IDs should vary (true randomness from `func.random()`).

## Step 9: Test Deduplication

```bash
# Generate posts and verify they avoid recent products/categories
# Check that Counter is used correctly (not set comparison)
```

**Verify in code**: Check that `social_dedupe.py` uses `Counter` for category counting, not set comparison.

## Step 10: Run Unit Tests

```bash
cd impag-quot
pytest tests/test_social_dedupe.py -v
pytest tests/test_social_llm.py -v
pytest tests/test_social_save.py -v
```

**Expected**: All tests pass.

## Performance Verification

### Before/After Comparison

**Before**:
- `/save` with 1000 posts: O(n) scan = ~100ms
- Date queries: String comparison = slower

**After**:
- `/save` with 1000 posts: Indexed lookup = ~1ms
- Date queries: DATE comparison = faster

**Test**:
```sql
-- Create 1000 test posts
-- Then test /save lookup time
EXPLAIN ANALYZE 
SELECT * FROM social_post 
WHERE external_id = 'test-123';
-- Should show: Index Scan using idx_social_post_external_id
```

## Common Issues

### Issue: Import errors for new modules
**Solution**: Ensure Python path includes `routes/` directory

### Issue: Migration fails on existing data
**Solution**: Migration handles existing data, but if `date_for` has invalid dates, may need cleanup:
```sql
-- Check for invalid dates
SELECT id, date_for FROM social_post WHERE date_for::text !~ '^\d{4}-\d{2}-\d{2}$';
```

### Issue: Rate limiting too strict
**Solution**: Adjust limits in `social_rate_limit.py`:
```python
RATE_LIMITS = {
    "/generate": {
        "max_requests": 20,  # Increase if needed
        "window_seconds": 3600
    }
}
```

## Success Criteria

✅ Migration runs without errors
✅ All indexes created successfully
✅ Rate limiting works (429 on limit)
✅ Date comparisons work correctly
✅ /save uses indexed lookup (check EXPLAIN ANALYZE)
✅ JSON parsing always succeeds (no errors in logs)
✅ Structured logging redacts sensitive data
✅ Products selected with true randomness
✅ Deduplication uses Counter correctly


