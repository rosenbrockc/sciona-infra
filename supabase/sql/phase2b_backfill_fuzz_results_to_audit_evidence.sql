INSERT INTO public.atom_audit_evidence (
    atom_id,
    audit_type,
    passed,
    details,
    source_kind,
    runner_version,
    created_at
)
SELECT
    a.atom_id,
    'fuzz_test',
    fr.passed,
    jsonb_build_object(
        'strategy', fr.strategy,
        'inputs_tested', fr.inputs_tested,
        'failures', fr.failures,
        'runtime_ms', fr.runtime_ms,
        'content_hash', fr.content_hash,
        'original_fuzz_id', fr.fuzz_id::text
    ),
    'automated',
    'backfill-from-fuzz_results',
    fr.created_at
FROM public.fuzz_results fr
JOIN public.atoms a ON a.fqdn = fr.atom_fqdn;
