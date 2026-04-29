-- Shift-based baker assignment migration for products
-- Safe to run multiple times

alter table public.products
    add column if not exists morning_baker_id uuid null,
    add column if not exists evening_baker_id uuid null,
    add column if not exists night_baker_id uuid null;

do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'products_morning_baker_id_fkey'
    ) then
        alter table public.products
            add constraint products_morning_baker_id_fkey
            foreign key (morning_baker_id) references public.app_users(id)
            on delete set null;
    end if;

    if not exists (
        select 1 from pg_constraint where conname = 'products_evening_baker_id_fkey'
    ) then
        alter table public.products
            add constraint products_evening_baker_id_fkey
            foreign key (evening_baker_id) references public.app_users(id)
            on delete set null;
    end if;

    if not exists (
        select 1 from pg_constraint where conname = 'products_night_baker_id_fkey'
    ) then
        alter table public.products
            add constraint products_night_baker_id_fkey
            foreign key (night_baker_id) references public.app_users(id)
            on delete set null;
    end if;
end $$;

-- Backfill from legacy single baker assignment
update public.products
set
    morning_baker_id = coalesce(morning_baker_id, assigned_baker_id),
    evening_baker_id = coalesce(evening_baker_id, assigned_baker_id),
    night_baker_id = coalesce(night_baker_id, assigned_baker_id);

create index if not exists idx_products_morning_baker on public.products(morning_baker_id);
create index if not exists idx_products_evening_baker on public.products(evening_baker_id);
create index if not exists idx_products_night_baker on public.products(night_baker_id);

-- Optional (run manually later after app rollout verification):
-- alter table public.products drop column if exists assigned_baker_id;
