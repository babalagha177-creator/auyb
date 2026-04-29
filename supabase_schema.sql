-- =========================================================
-- Bab Al-Agha Inventory - Canonical schema for app.py
-- Safe to run multiple times (idempotent migration script)
-- Timezone baseline: Asia/Baghdad
--
-- طريقة التطبيق: من لوحة Supabase → SQL → الصق هذا الملف وشغِّله (آمن للتكرار).
-- إن كان مشروعك أقدم من إضافة الشفتات، شغِّل السكربت ليُضاف shift_name و shift_cycle_key وغيرها تلقائياً.
--
-- Matches app.py expectations:
--   app_users (RBAC, permissions JSONB, managed_sections JSONB[] for DeptManager; seed admin "باب الاغا")
--   products (morning/evening/night baker FK, current_qty/request_qty; no min_qty)
--   audit_archive, system_settings (6 AM Baghdad archive cycle)
--   notifications (message, target_role, read_by_usernames JSONB array;
--                  is_read kept for legacy; app marks read per-username)
-- =========================================================

create extension if not exists "uuid-ossp";

-- -------------------------------------
-- 1) Users / RBAC
-- -------------------------------------
create table if not exists public.app_users (
    id uuid primary key default uuid_generate_v4(),
    username text unique not null,
    password_text text not null,
    role text not null,
    permissions jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default timezone('Asia/Baghdad', now())
);

-- Ensure required columns/defaults even if table already existed
alter table public.app_users
    add column if not exists password_text text,
    add column if not exists role text,
    add column if not exists permissions jsonb not null default '{}'::jsonb,
    add column if not exists managed_sections jsonb not null default '[]'::jsonb,
    add column if not exists created_at timestamptz not null default timezone('Asia/Baghdad', now());

-- أقسام يديرها مسؤول القسم (مصفوفة نصوص JSON، مثال: ["التوست","الخبز العربي"]).
-- يمكن لعدة مستخدمين بدور DeptManager أن يمتلكوا نفس أسماء الأقسام في managed_sections في آن واحد (شفتات متعددة).
update public.app_users
set managed_sections = '[]'::jsonb
where managed_sections is null;

-- -------------------------------------
-- 2) Products (used by inventory screens)
-- -------------------------------------
-- This script assumes products table exists or creates a compatible one.
create table if not exists public.products (
    id uuid primary key default uuid_generate_v4(),
    name text unique not null,
    unit_val text not null default 'قطعة',
    section_name text not null default 'عام',
    assigned_baker_id uuid null references public.app_users(id) on delete set null,
    morning_baker_id uuid null references public.app_users(id) on delete set null,
    evening_baker_id uuid null references public.app_users(id) on delete set null,
    night_baker_id uuid null references public.app_users(id) on delete set null,
    current_qty numeric not null default 0,
    request_qty numeric not null default 0,
    created_at timestamptz not null default timezone('Asia/Baghdad', now())
);

alter table public.products
    add column if not exists unit_val text not null default 'قطعة',
    add column if not exists section_name text not null default 'عام',
    add column if not exists assigned_baker_id uuid null,
    add column if not exists morning_baker_id uuid null,
    add column if not exists evening_baker_id uuid null,
    add column if not exists night_baker_id uuid null,
    add column if not exists current_qty numeric not null default 0,
    add column if not exists request_qty numeric not null default 0,
    add column if not exists created_at timestamptz not null default timezone('Asia/Baghdad', now());

-- شفت بغداد (صباحي / مسائي / ليلي) — يُحدَّث عند كل حفظ جرد
alter table public.products
    add column if not exists shift_name text null,
    add column if not exists shift_cycle_key text null;

-- سجل توصيات متعدد (عرض LIFO في الواجهة؛ لا يُستبدل بالكامل عند «توصية جديدة»).
-- عناصر JSON اختيارية يضيفها التطبيق: entry_id (uuid نصي)، segment_production_status (حالة إنتاج لسطر زيادة للخلفة).
alter table public.products
    add column if not exists recommendation_log jsonb not null default '[]'::jsonb;

do $$
begin
    if exists (
        select 1
        from information_schema.columns
        where table_schema = 'public'
          and table_name = 'products'
          and column_name = 'min_qty'
    ) then
        alter table public.products drop column min_qty;
    end if;
end $$;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'products_morning_baker_id_fkey'
    ) then
        alter table public.products
            add constraint products_morning_baker_id_fkey
            foreign key (morning_baker_id) references public.app_users(id)
            on delete set null;
    end if;
    if not exists (
        select 1
        from pg_constraint
        where conname = 'products_evening_baker_id_fkey'
    ) then
        alter table public.products
            add constraint products_evening_baker_id_fkey
            foreign key (evening_baker_id) references public.app_users(id)
            on delete set null;
    end if;
    if not exists (
        select 1
        from pg_constraint
        where conname = 'products_night_baker_id_fkey'
    ) then
        alter table public.products
            add constraint products_night_baker_id_fkey
            foreign key (night_baker_id) references public.app_users(id)
            on delete set null;
    end if;
end $$;

-- ترحيل توافقي: إذا كانت الأعمدة الجديدة فارغة، انسخ assigned_baker_id إليها
update public.products
set
    morning_baker_id = coalesce(morning_baker_id, assigned_baker_id),
    evening_baker_id = coalesce(evening_baker_id, assigned_baker_id),
    night_baker_id = coalesce(night_baker_id, assigned_baker_id);

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'products_assigned_baker_id_fkey'
    ) then
        alter table public.products
            add constraint products_assigned_baker_id_fkey
            foreign key (assigned_baker_id) references public.app_users(id)
            on delete set null;
    end if;
end $$;

-- -------------------------------------
-- 3) Archive table (12-hour cycle @ 6 AM Baghdad)
-- -------------------------------------
create table if not exists public.audit_archive (
    archive_id uuid primary key default uuid_generate_v4(),
    product_id uuid,
    archived_qty numeric,
    archived_request_qty numeric,
    archive_date timestamptz not null default timezone('Asia/Baghdad', now())
);

alter table public.audit_archive
    add column if not exists archive_id uuid default uuid_generate_v4(),
    add column if not exists product_id uuid,
    add column if not exists archived_qty numeric,
    add column if not exists archived_request_qty numeric,
    add column if not exists archive_date timestamptz not null default timezone('Asia/Baghdad', now());

alter table public.audit_archive
    add column if not exists shift_name text null,
    add column if not exists shift_cycle_key text null,
    add column if not exists product_name text null,
    add column if not exists section_name text null,
    add column if not exists unit_val text null,
    add column if not exists notes text null,
    add column if not exists production_status text null,
    add column if not exists last_updated_by text null,
    add column if not exists last_updated_at timestamptz null;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'audit_archive_pkey'
    ) then
        alter table public.audit_archive add constraint audit_archive_pkey primary key (archive_id);
    end if;
end $$;

-- -------------------------------------
-- 4) Notifications
-- -------------------------------------
create table if not exists public.notifications (
    id uuid primary key default uuid_generate_v4(),
    message text not null,
    is_read boolean not null default false,
    read_by_usernames jsonb not null default '[]'::jsonb,
    target_role text null,
    created_at timestamptz not null default timezone('Asia/Baghdad', now())
);

alter table public.notifications
    add column if not exists message text,
    add column if not exists is_read boolean not null default false,
    add column if not exists read_by_usernames jsonb not null default '[]'::jsonb,
    add column if not exists target_role text,
    add column if not exists created_at timestamptz not null default timezone('Asia/Baghdad', now());

-- Backfill null values for compatibility
update public.notifications
set read_by_usernames = '[]'::jsonb
where read_by_usernames is null;

-- -------------------------------------
-- 5) System settings (for archive cycle state)
-- -------------------------------------
create table if not exists public.system_settings (
    key text primary key,
    value text,
    updated_at timestamptz not null default timezone('Asia/Baghdad', now())
);

alter table public.system_settings
    add column if not exists value text,
    add column if not exists updated_at timestamptz not null default timezone('Asia/Baghdad', now());

-- -------------------------------------
-- 6) Performance indexes
-- -------------------------------------
create index if not exists idx_app_users_username on public.app_users(username);

create index if not exists idx_products_name on public.products(name);
create index if not exists idx_products_section on public.products(section_name);
create index if not exists idx_products_assigned_baker on public.products(assigned_baker_id);
create index if not exists idx_products_morning_baker on public.products(morning_baker_id);
create index if not exists idx_products_evening_baker on public.products(evening_baker_id);
create index if not exists idx_products_night_baker on public.products(night_baker_id);
create index if not exists idx_notifications_role_read_created
    on public.notifications(target_role, is_read, created_at desc);
create index if not exists idx_notifications_readers_gin
    on public.notifications using gin (read_by_usernames);
create index if not exists idx_audit_archive_date on public.audit_archive(archive_date desc);

-- -------------------------------------
-- 6.1) Historical cleanup: remove reopen ghost logs
-- -------------------------------------
-- Removes legacy UI ghost log entries from products.recommendation_log permanently.
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

-- -------------------------------------
-- 7) Seed default users (exact credentials used by app.py)
-- -------------------------------------
insert into public.app_users (username, password_text, role, permissions)
values
(
    'باب الاغا',
    '19488491',
    'Admin',
    '{
      "can_add_users": true,
      "can_edit_products": true,
      "can_view_archive": true,
      "can_view_home": true,
      "can_view_baker_screen": true,
      "can_view_inventory": true,
      "can_view_preview": true,
      "can_view_notifications": true
    }'::jsonb
),
(
    'المخزن',
    '11220099',
    'DeptManager',
    '{
      "can_add_users": false,
      "can_edit_products": false,
      "can_view_archive": false,
      "can_view_home": true,
      "can_view_baker_screen": false,
      "can_view_inventory": true,
      "can_view_preview": true,
      "can_view_notifications": true
    }'::jsonb
)
on conflict (username) do nothing;

-- ترحيل الحسابات القديمة: دور Warehouse أصبح مسؤول قسم (DeptManager)
update public.app_users
set
    role = 'DeptManager',
    permissions = '{
      "can_add_users": false,
      "can_edit_products": false,
      "can_view_archive": false,
      "can_view_home": true,
      "can_view_baker_screen": false,
      "can_view_inventory": true,
      "can_view_preview": true,
      "can_view_notifications": true
    }'::jsonb
where role = 'Warehouse';

-- ترحيل مفاتيح الصلاحيات القديمة إلى نموذج الشاشات (idempotent)
update public.app_users
set permissions = permissions
    || jsonb_strip_nulls(jsonb_build_object(
        'can_view_home', case
            when (permissions ? 'can_view_home') then null
            when (permissions->>'can_view_dashboard') in ('true', 't', '1') then true
            else null
        end,
        'can_view_baker_screen', case
            when (permissions ? 'can_view_baker_screen') then null
            when role = 'Baker' and (permissions->>'can_view_dashboard') in ('true', 't', '1') then true
            else null
        end,
        'can_view_inventory', case
            when (permissions ? 'can_view_inventory') then null
            when role = 'DeptManager' and (
                (permissions->>'can_view_dashboard') in ('true', 't', '1')
                or (permissions->>'can_submit_inventory') in ('true', 't', '1')
                or (permissions->>'can_request_production') in ('true', 't', '1')
            ) then true
            when role = 'Admin' then true
            else null
        end,
        'can_view_preview', case
            when (permissions ? 'can_view_preview') then null
            when role = 'DeptManager' and (permissions->>'can_view_dashboard') in ('true', 't', '1') then true
            when role = 'Admin' then true
            else null
        end,
        'can_view_notifications', case
            when (permissions ? 'can_view_notifications') then null
            when (permissions->>'can_view_dashboard') in ('true', 't', '1')
                 or (permissions->>'can_view_notifications') in ('true', 't', '1') then true
            else null
        end
    ))
where role in ('Admin', 'Baker', 'DeptManager');
