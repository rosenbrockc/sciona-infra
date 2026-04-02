INSERT INTO public.roles (role_name, grants_tier, description) VALUES
    ('Administrator', 'internal', 'Full system access'),
    ('Founder', 'internal', 'Organization founder'),
    ('Maintainer', 'internal', 'Atom catalog maintainer'),
    ('Foundation Staff', 'internal', 'Foundation employee'),
    ('Board Member', 'early_access', 'Advisory board member'),
    ('Org Member', 'early_access', 'Organization member'),
    ('Paid Member', 'early_access', 'Paid subscription member'),
    ('Free Member', 'general', 'Free tier member')
ON CONFLICT (role_name) DO UPDATE
SET grants_tier = EXCLUDED.grants_tier,
    description = EXCLUDED.description;
