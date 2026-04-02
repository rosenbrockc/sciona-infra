SELECT extname, extversion
FROM pg_extension
WHERE extname IN ('vector', 'pg_trgm', 'uuid-ossp')
ORDER BY extname;

SELECT count(*)
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_type = 'BASE TABLE';

SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_type = 'BASE TABLE'
ORDER BY table_name;

SELECT matviewname
FROM pg_matviews
WHERE schemaname = 'public';

SELECT routine_name
FROM information_schema.routines
WHERE routine_schema = 'public'
  AND routine_type = 'FUNCTION'
  AND routine_name IN (
      'handle_new_user',
      'is_contributor',
      'user_effective_entitlement',
      'refresh_user_effective_tier',
      'atom_is_publishable',
      'refresh_atom_publishable'
  )
ORDER BY routine_name;

SELECT
    trigger_name,
    event_object_table,
    CASE
        WHEN tg.tgenabled = 'D' THEN 'DISABLED'
        ELSE 'ENABLED'
    END AS trigger_state
FROM information_schema.triggers t
JOIN pg_trigger tg ON tg.tgname = t.trigger_name
WHERE t.trigger_schema = 'public'
  AND t.trigger_name LIKE 'trg_%'
ORDER BY trigger_name;

SELECT
    tc.table_name,
    tc.constraint_name,
    ccu.table_name AS references_table
FROM information_schema.table_constraints tc
JOIN information_schema.constraint_column_usage ccu
  ON tc.constraint_name = ccu.constraint_name
  AND tc.constraint_schema = ccu.constraint_schema
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND tc.table_schema = 'public'
ORDER BY tc.table_name, tc.constraint_name;

SELECT role_name, grants_tier FROM public.roles ORDER BY role_name;
