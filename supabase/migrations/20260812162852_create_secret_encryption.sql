-- Vault + pgcrypto encryption plumbing (Sprint 2.7b) -- Proposal §11:
-- "User provider keys never appear in MCP arguments, normal logs,
-- traces, or queue payloads." Pattern mirrored from
-- careerforge-command-center's `user_api_keys` encryption design
-- (frozen reference repo, read-only) -- its own history is worth
-- learning from directly: the encryption key started as a Postgres
-- custom setting with a weak `md5(current_database())` fallback, then
-- had that fallback removed (fail closed instead of silently weak), and
-- only in its final form moved the key into Supabase Vault, fetched
-- fresh inside every encrypt/decrypt call rather than ever passed
-- through a function argument or read from an env var into SQL. This
-- migration goes straight to that final shape -- no weak-fallback
-- interim step to walk back from.
--
-- `pgcrypto` and `supabase_vault` are already installed on this project
-- (confirmed via list_extensions before writing this), so no `create
-- extension` here.
--
-- `encrypt_secret`/`decrypt_secret` are deliberately generic (not named
-- for "api keys" specifically) -- provider_credentials (Sprint 2.7c) is
-- their first caller, but nothing about them is BYOK-specific.
--
-- Both functions are SECURITY DEFINER and revoked from public/anon/
-- authenticated in THIS SAME migration, not a follow-up -- Sprint 2.6d's
-- change_application_stage shipped without that revoke and needed two
-- live patches after the security advisor caught it. Applying that
-- lesson here from the start instead of rediscovering it.

do $$
begin
  if not exists (select 1 from vault.decrypted_secrets where name = 'ENCRYPTION_KEY') then
    perform vault.create_secret(
      encode(extensions.gen_random_bytes(32), 'hex'),
      'ENCRYPTION_KEY',
      'Symmetric passphrase for encrypt_secret/decrypt_secret (Sprint 2.7b) -- '
      || 'pgp_sym_encrypt/decrypt only, never leaves Postgres.'
    );
  end if;
end $$;

create function public.encrypt_secret(p_plaintext text)
returns text
language plpgsql
security definer
set search_path = public, extensions, vault
as $$
declare
  v_key text;
begin
  select decrypted_secret into v_key from vault.decrypted_secrets where name = 'ENCRYPTION_KEY';
  if v_key is null or v_key = '' then
    raise exception 'ENCRYPTION_KEY is not configured in Vault';
  end if;
  return encode(pgp_sym_encrypt(p_plaintext, v_key), 'base64');
end;
$$;

create function public.decrypt_secret(p_ciphertext text)
returns text
language plpgsql
security definer
set search_path = public, extensions, vault
as $$
declare
  v_key text;
begin
  select decrypted_secret into v_key from vault.decrypted_secrets where name = 'ENCRYPTION_KEY';
  if v_key is null or v_key = '' then
    raise exception 'ENCRYPTION_KEY is not configured in Vault';
  end if;
  return pgp_sym_decrypt(decode(p_ciphertext, 'base64'), v_key);
end;
$$;

revoke execute on function public.encrypt_secret(text) from public, anon, authenticated;
revoke execute on function public.decrypt_secret(text) from public, anon, authenticated;
