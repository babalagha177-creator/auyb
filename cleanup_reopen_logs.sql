-- Cleanup migration: permanently remove reopen ghost entries
-- from products.recommendation_log.
-- Safe to run multiple times.

update public.products p
set recommendation_log = coalesce(cleaned.filtered_log, '[]'::jsonb)
from (
    select
        id,
        jsonb_agg(entry) filter (
            where not (
                lower(coalesce(entry->>'kind', '')) = 'reopen'
                or lower(coalesce(entry->>'notes', '')) = 'إعادة فتح نافذة التعديل'
                or lower(coalesce(entry->>'notes', '')) like '%reopen%'
            )
        ) as filtered_log
    from public.products
    left join lateral jsonb_array_elements(
        case
            when jsonb_typeof(recommendation_log) = 'array' then recommendation_log
            else '[]'::jsonb
        end
    ) as entry on true
    group by id
) as cleaned
where p.id = cleaned.id;
