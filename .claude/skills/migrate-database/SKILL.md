---
name: migrate-database
description: Run Perseus database migrations safely
triggers:
  - migrate
  - database migration
  - schema change
  - init-db
---

# Database Migration Skill (Perseus)

## When to Use
Agent should trigger when:
- Setting up project for first time
- After schema changes in scripts/init-db.sql
- Adding new tables or columns

## Safety
- ALWAYS run `make backup` before migrating
- Test SQL on a separate connection first
- Check init-db.sql diff before running

## Instructions
1. Backup: `make backup`
2. Check current schema: `docker exec perseus-postgres psql -U perseus -d perseus -c "\dt"`
3. Run migrations: `docker exec -i perseus-postgres psql -U perseus -d perseus < scripts/init-db.sql`
4. Verify: `docker exec perseus-postgres psql -U perseus -d perseus -c "\dt"`
5. Report results

## Rollback
If migration fails:
1. Restore from backup: `make restore BACKUP=/path/to/backup.sql.gz`
2. Report the failure and error
