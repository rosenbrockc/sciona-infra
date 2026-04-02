-- Phase 2E: recompute atoms.is_publishable after Phases 2A-2D are complete.

ALTER TABLE public.atom_io_specs DISABLE TRIGGER trg_publishable_io_specs;
ALTER TABLE public.atom_parameters DISABLE TRIGGER trg_publishable_parameters;
ALTER TABLE public.atom_descriptions DISABLE TRIGGER trg_publishable_descriptions;
ALTER TABLE public.atom_audit_rollups DISABLE TRIGGER trg_publishable_rollups;
ALTER TABLE public.atom_references DISABLE TRIGGER trg_publishable_references;

UPDATE public.atoms
   SET is_publishable = public.atom_is_publishable(atom_id),
       updated_at = now();

ALTER TABLE public.atom_io_specs ENABLE TRIGGER trg_publishable_io_specs;
ALTER TABLE public.atom_parameters ENABLE TRIGGER trg_publishable_parameters;
ALTER TABLE public.atom_descriptions ENABLE TRIGGER trg_publishable_descriptions;
ALTER TABLE public.atom_audit_rollups ENABLE TRIGGER trg_publishable_rollups;
ALTER TABLE public.atom_references ENABLE TRIGGER trg_publishable_references;
