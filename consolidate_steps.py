#!/usr/bin/env python3
"""
Script to consolidate logging steps into phases.
This is a one-time refactoring script.
"""

replacements = [
    # Phase 1: Initialization
    ('[STEP 0] Starting post generation', '[PHASE 1] Initialization'),
    ('[STEP 0] Rate limit exceeded', '[PHASE 1] Rate limit exceeded'),
    ('[STEP 0] CLAUDE_API_KEY not configured', '[PHASE 1] CLAUDE_API_KEY not configured'),
    ('[STEP 1] Parsing date and initializing context', '[PHASE 1] Parsing date'),
    ('[STEP 1] Invalid date format', '[PHASE 1] Invalid date format'),
    
    # Phase 2: Context Loading
    ('[STEP 2] Fetching recent posts for deduplication', '[PHASE 2] Loading context'),
    ('[STEP 2] Recent posts fetched', '[PHASE 2] Context loaded'),
    ('[STEP 3] Building history summary and extracting deduplication sets', '[PHASE 2] Building history'),
    ('[STEP 3] Deduplication sets extracted', '[PHASE 2] History built'),
    ('[STEP 4] Loading season context and Durango context', '[PHASE 2] Loading regional context'),
    ('[STEP 4] Context loaded', '[PHASE 2] Regional context loaded'),
    
    # Phase 3: Strategy Generation
    ('[STEP 6] Identifying agricultural problems', '[PHASE 3] Generating strategy'),
    ('[STEP 6] Problems identified', '[PHASE 3] Strategy generated'),
    ('[STEP 7] Building strategy prompt', ''),  # Remove - redundant with above
    ('[STEP 10] Checking problem duplicate (soft rule)', '[PHASE 3] Validating problem'),
    ('[STEP 10] Problem duplicate detected (soft rule)', '[PHASE 3] Problem duplicate found'),
    ('[STEP 10] Problem duplicate check passed', '[PHASE 3] Problem validated'),
    
    # Phase 4: Content Generation
    ('[STEP 11] Starting product selection', '[PHASE 4] Selecting product'),
    ('[STEP 11] Product selected', '[PHASE 4] Product selected'),
    ('[STEP 11] Product selection failed', '[PHASE 4] Product selection failed'),
    ('[STEP 11] Product selection skipped (search_needed=false)', '[PHASE 4] Product selection skipped'),
    ('[STEP 12] Starting content generation phase', '[PHASE 4] Generating content'),
    ('[STEP 12] Fetching product details', '[PHASE 4] Fetching product details'),
    ('[STEP 12] Product details fetched', '[PHASE 4] Product details fetched'),
    ('[STEP 12] Error fetching product details', '[PHASE 4] Error fetching product details'),
    ('[STEP 12] No product selected, skipping product details', '[PHASE 4] No product selected'),
    ('[STEP 12] Building content generation prompt', '[PHASE 4] Building content prompt'),
    ('[STEP 13] Calling content LLM', '[PHASE 4] Calling LLM'),
    ('[STEP 13] Content LLM response received', '[PHASE 4] LLM response received'),
    ('[STEP 13] Missing image_prompt for non-carousel post', '[PHASE 4] Missing image_prompt'),
    
    # Phase 5: Saving
    ('[STEP 15] Checking for existing post by topic_hash', '[PHASE 5] Saving post'),
    ('[STEP 15] Existing post found, updating', '[PHASE 5] Updating existing post'),
    ('[STEP 15] Post updated successfully', '[PHASE 5] Post updated'),
    ('[STEP 15] Creating new post', '[PHASE 5] Creating new post'),
    ('[STEP 15] Post created successfully', '[PHASE 5] Post created'),
]

# This is just for reference - we'll do the replacements manually
print("Replacements to make:")
for old, new in replacements:
    if new:  # Skip empty replacements (removals)
        print(f"  '{old}' -> '{new}'")
    else:
        print(f"  Remove: '{old}'")

