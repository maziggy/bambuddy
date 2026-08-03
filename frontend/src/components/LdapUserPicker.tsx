import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Search, Plus, CheckCircle2 } from 'lucide-react';
import { Button } from './Button';
import { api } from '../api/client';
import type { LDAPSearchResult, UserResponse } from '../api/client';

interface LdapUserPickerProps {
  onSuccess: (user: UserResponse) => void;
}

const SEARCH_DEBOUNCE_MS = 300;
const MIN_QUERY_LENGTH = 2;

export function LdapUserPicker({ onSuccess }: LdapUserPickerProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [rawQuery, setRawQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [selectedDn, setSelectedDn] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Debounce keystrokes — the search hits the directory and we don't want a
  // request per character. 300ms matches the debounce in other typeaheads in
  // this app (e.g. file manager).
  useEffect(() => {
    const trimmed = rawQuery.trim();
    if (trimmed.length < MIN_QUERY_LENGTH) {
      setDebouncedQuery('');
      return;
    }
    const id = setTimeout(() => setDebouncedQuery(trimmed), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [rawQuery]);

  // Reset selection when the query changes so a stale selection from a previous
  // search can't be silently submitted.
  useEffect(() => {
    setSelectedDn(null);
    setErrorMessage(null);
  }, [debouncedQuery]);

  const searchQuery = useQuery({
    queryKey: ['ldap-search', debouncedQuery],
    queryFn: () => api.searchLDAPDirectory(debouncedQuery),
    enabled: debouncedQuery.length >= MIN_QUERY_LENGTH,
    staleTime: 30_000,
  });

  const provisionMutation = useMutation({
    mutationFn: (username: string) => api.provisionLDAPUser(username),
    onSuccess: (user) => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      onSuccess(user);
    },
    onError: (error: Error) => {
      setErrorMessage(error.message || t('users.modal.ldapErrorProvision'));
    },
  });

  const selectedResult = useMemo(
    () => searchQuery.data?.find((r) => r.dn === selectedDn) ?? null,
    [searchQuery.data, selectedDn]
  );

  const isShortQuery = rawQuery.trim().length > 0 && rawQuery.trim().length < MIN_QUERY_LENGTH;
  const isLoading = searchQuery.isFetching && debouncedQuery.length >= MIN_QUERY_LENGTH;
  const hasResults = !!searchQuery.data && searchQuery.data.length > 0;
  const showNoResults =
    !isLoading && !!searchQuery.data && searchQuery.data.length === 0 && debouncedQuery.length >= MIN_QUERY_LENGTH;

  const handleProvision = () => {
    if (!selectedResult || selectedResult.already_provisioned) return;
    setErrorMessage(null);
    provisionMutation.mutate(selectedResult.username);
  };

  return (
    <div className="space-y-4">
      {/* Search input */}
      <div>
        <label className="block text-sm font-medium text-white mb-2">
          {t('users.modal.ldapSearchLabel')}
        </label>
        <div className="relative">
          <Search className="w-4 h-4 text-bambu-gray absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={rawQuery}
            onChange={(e) => setRawQuery(e.target.value)}
            className="w-full pl-9 pr-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
            placeholder={t('users.modal.ldapSearchPlaceholder')}
            autoComplete="off"
          />
        </div>
        {isShortQuery && (
          <p className="mt-1 text-xs text-bambu-gray">{t('users.modal.ldapMinChars')}</p>
        )}
      </div>

      {/* Results panel */}
      <div className="min-h-[8rem] max-h-64 overflow-y-auto bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg">
        {isLoading && (
          <div className="flex items-center justify-center py-8 text-bambu-gray">
            <Loader2 className="w-4 h-4 animate-spin mr-2" />
            <span>{t('users.modal.ldapSearching')}</span>
          </div>
        )}

        {showNoResults && (
          <div className="flex items-center justify-center py-8 text-bambu-gray text-sm">
            {t('users.modal.ldapNoResults')}
          </div>
        )}

        {searchQuery.isError && (
          <div className="px-3 py-4 text-sm text-red-700 dark:text-red-400">
            {searchQuery.error instanceof Error ? searchQuery.error.message : t('users.modal.ldapSearchError')}
          </div>
        )}

        {!isLoading && hasResults && (
          <ul className="divide-y divide-bambu-dark-tertiary">
            {searchQuery.data!.map((result) => (
              <LdapResultRow
                key={result.dn}
                result={result}
                selected={selectedDn === result.dn}
                onSelect={() => setSelectedDn(result.dn)}
              />
            ))}
          </ul>
        )}

        {!isLoading && !searchQuery.data && !searchQuery.isError && (
          <div className="flex items-center justify-center py-8 text-bambu-gray text-sm">
            {t('users.modal.ldapTypeToSearch')}
          </div>
        )}
      </div>

      {/* Selected user summary */}
      {selectedResult && (
        <div className="bg-bambu-dark-secondary/50 border border-bambu-green/20 rounded-lg p-3 space-y-1">
          <p className="text-sm text-white">
            <span className="text-bambu-gray">{t('users.modal.ldapSelectedLabel')}: </span>
            <span className="font-medium">{selectedResult.username}</span>
            {selectedResult.display_name && (
              <span className="text-bambu-gray"> — {selectedResult.display_name}</span>
            )}
          </p>
          {selectedResult.email && (
            <p className="text-xs text-bambu-gray">{selectedResult.email}</p>
          )}
          <p className="text-xs text-bambu-gray break-all">{selectedResult.dn}</p>
        </div>
      )}

      {/* Error from the provision mutation */}
      {errorMessage && (
        <div className="bg-red-50 dark:bg-red-500/10 border border-red-300 dark:border-red-500/30 rounded-lg p-3">
          <p className="text-sm text-red-700 dark:text-red-400">{errorMessage}</p>
        </div>
      )}

      {/* Submit button */}
      <div className="flex justify-end">
        <Button
          onClick={handleProvision}
          disabled={
            !selectedResult || selectedResult.already_provisioned || provisionMutation.isPending
          }
        >
          {provisionMutation.isPending ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              {t('users.modal.ldapProvisioning')}
            </>
          ) : (
            <>
              <Plus className="w-4 h-4" />
              {t('users.modal.ldapProvision')}
            </>
          )}
        </Button>
      </div>
    </div>
  );
}

interface LdapResultRowProps {
  result: LDAPSearchResult;
  selected: boolean;
  onSelect: () => void;
}

function LdapResultRow({ result, selected, onSelect }: LdapResultRowProps) {
  const { t } = useTranslation();
  const disabled = result.already_provisioned;

  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        disabled={disabled}
        className={`w-full text-left px-3 py-2 flex items-center gap-3 transition-colors ${
          disabled
            ? 'opacity-50 cursor-not-allowed'
            : selected
              ? 'bg-bambu-green/10'
              : 'hover:bg-bambu-dark-tertiary'
        }`}
      >
        <div className="flex-1 min-w-0">
          <p className="text-sm text-white truncate">
            <span className="font-medium">{result.username}</span>
            {result.display_name && (
              <span className="text-bambu-gray"> — {result.display_name}</span>
            )}
          </p>
          {result.email && (
            <p className="text-xs text-bambu-gray truncate">{result.email}</p>
          )}
        </div>
        {disabled && (
          <span className="flex items-center gap-1 text-xs text-bambu-gray whitespace-nowrap">
            <CheckCircle2 className="w-3.5 h-3.5" />
            {t('users.modal.ldapAlreadyProvisioned')}
          </span>
        )}
      </button>
    </li>
  );
}
