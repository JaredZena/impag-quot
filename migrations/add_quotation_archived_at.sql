-- Add archived_at column to quotation table for soft delete
ALTER TABLE quotation ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP WITH TIME ZONE;

-- Create index for better query performance
CREATE INDEX IF NOT EXISTS idx_quotation_archived_at ON quotation(archived_at);

